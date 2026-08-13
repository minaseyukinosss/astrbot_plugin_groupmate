"""Decision ledger queries and plugin-page API for path transparency."""

import asyncio
import json
import sys
import types

import pytest

from groupmate.memory.store import SQLiteMemoryStore


async def _async_value(value):
    return value


@pytest.fixture
def store(tmp_path):
    value = SQLiteMemoryStore(tmp_path / "decision-visibility.db")
    try:
        yield value
    finally:
        value.close()


def _record_path(store, persona_id, decision_id, group_id, *, sent=False, end_reason="silent"):
    base = 1000
    store.record_transition(persona_id, decision_id, group_id, "OBSERVE", "ALIAS_DIRECT", base)
    store.record_transition(persona_id, decision_id, group_id, "SCENE", "DIRECT_ADDRESS", base + 1)
    store.record_transition(
        persona_id, decision_id, group_id, "PARTICIPATION", "direct_required", base + 2
    )
    store.record_transition(
        persona_id, decision_id, group_id, "INTENT", "reply:answer", base + 3
    )
    store.record_transition(persona_id, decision_id, group_id, "ACT", "answer", base + 4)
    if sent:
        store.record_transition(persona_id, decision_id, group_id, "SEND", "sent", base + 5)
        store.record_transition(persona_id, decision_id, group_id, "END", "sent", base + 6)
    else:
        store.record_transition(
            persona_id, decision_id, group_id, "END", end_reason, base + 5
        )


def test_recent_decisions_include_path_summary_without_text(store):
    _record_path(store, "aemeath", "d-sent", "g1", sent=True)
    _record_path(
        store, "aemeath", "d-silent", "g1", sent=False, end_reason="model_silence"
    )
    store.record_transition("aemeath", "d-g2", "g2", "OBSERVE", "candidate", 1498)
    store.record_transition("aemeath", "d-g2", "g2", "SEND", "sent", 1499)
    store.record_transition("aemeath", "d-g2", "g2", "END", "sent", 1500)
    store.record_transition("future", "d-other", "g1", "END", "sent", 2000)

    items = store.recent_decisions("aemeath", limit=10)
    assert [item["decision_id"] for item in items] == ["d-g2", "d-sent", "d-silent"]
    silent = items[2]
    assert silent["sent"] is False
    assert silent["trigger"] == "ALIAS_DIRECT"
    assert silent["scene"] == "DIRECT_ADDRESS"
    assert silent["participation"] == "direct_required"
    assert silent["end_reason"] == "model_silence"
    assert "text" not in silent

    sent_only = store.recent_decisions("aemeath", outcome="sent")
    assert [item["decision_id"] for item in sent_only] == ["d-g2", "d-sent"]
    assert sent_only[0]["sent"] is True

    group_filtered = store.recent_decisions("aemeath", group_id="missing")
    assert group_filtered == []
    multi = store.recent_decisions("aemeath", group_id="g1,g2", limit=10)
    assert {item["group_id"] for item in multi} == {"g1", "g2"}
    assert store.decision_group_ids("aemeath") == ["g1", "g2"]
    with pytest.raises(ValueError, match="outcome"):
        store.recent_decisions("aemeath", outcome="maybe")


def test_decision_trace_returns_ordered_stages(store):
    _record_path(store, "aemeath", "d1", "g9", sent=True)
    trace = store.decision_trace("aemeath", "d1")
    assert trace is not None
    assert trace["group_id"] == "g9"
    assert trace["sent"] is True
    assert [stage["state"] for stage in trace["stages"]] == [
        "OBSERVE",
        "SCENE",
        "PARTICIPATION",
        "INTENT",
        "ACT",
        "SEND",
        "END",
    ]
    assert trace["context"] == []
    assert store.decision_trace("aemeath", "missing") is None
    assert store.decision_trace("future", "d1") is None


def test_decision_trace_decodes_human_facing_addressee_summary(store):
    payload = {
        "reply": {"kind": "user", "name": "小明", "source": "platform_mention", "confidence": 0.97},
        "social": {"kind": "user", "name": "小红", "source": "interaction_partner", "confidence": 0.9},
        "memory": {"kind": "ambiguous", "name": "", "source": "recount_unconfirmed", "confidence": 0.4},
    }
    store.record_transition("aemeath", "targeted", "g", "OBSERVE", "alias_direct", 1)
    store.record_transition(
        "aemeath", "targeted", "g", "ADDRESSEE",
        json.dumps(payload, ensure_ascii=False), 2,
    )
    store.record_transition("aemeath", "targeted", "g", "END", "sent", 3)

    trace = store.decision_trace("aemeath", "targeted")
    assert trace["addressee"] == payload


def test_decision_trace_includes_nearby_chat_context(store, message_factory):
    from groupmate.models import MessageOrigin

    store.save_message(
        "aemeath",
        message_factory(
            message_id="m0",
            group_id="g9",
            sender_name="小明",
            text="前面一句",
            timestamp=990,
        ),
    )
    store.save_message(
        "aemeath",
        message_factory(
            message_id="m1",
            group_id="g9",
            sender_name="小红",
            text="爱弥斯在吗",
            timestamp=1000,
        ),
    )
    store.save_message(
        "aemeath",
        message_factory(
            message_id="m-bot",
            group_id="g9",
            sender_name="爱弥斯",
            text="在呢。",
            timestamp=1007,
            is_bot=True,
            origin=MessageOrigin.BOT_DELIVERY,
            decision_id="d1",
        ),
    )
    store.save_message(
        "aemeath",
        message_factory(
            message_id="m-later",
            group_id="g9",
            sender_name="路人",
            text="决策后的消息",
            timestamp=2000,
        ),
    )
    _record_path(store, "aemeath", "d1", "g9", sent=True)

    trace = store.decision_trace("aemeath", "d1")
    texts = [item["text"] for item in trace["context"]]
    assert "前面一句" in texts
    assert "爱弥斯在吗" in texts
    assert "在呢。" in texts
    assert "决策后的消息" not in texts
    focus = next(item for item in trace["context"] if item["is_focus"])
    assert focus["text"] == "爱弥斯在吗"
    reply = next(item for item in trace["context"] if item["is_reply"])
    assert reply["text"] == "在呢。"
    assert "sender_id" not in trace["context"][0]


def test_decision_context_dedupes_platform_echo_of_bot_delivery(store, message_factory):
    from groupmate.models import MessageOrigin

    store.save_message(
        "aemeath",
        message_factory(
            message_id="u1",
            group_id="g9",
            sender_name="复读斥候",
            text="小爱，1分钟后提醒我交材料",
            timestamp=1000,
        ),
    )
    store.save_message(
        "aemeath",
        message_factory(
            message_id="bot-d1",
            group_id="g9",
            sender_name="爱弥斯",
            text="好嘞，1分钟倒计时开始哦",
            timestamp=1008,
            is_bot=True,
            origin=MessageOrigin.BOT_DELIVERY,
            decision_id="d1",
        ),
    )
    store.save_message(
        "aemeath",
        message_factory(
            message_id="echo-1",
            group_id="g9",
            sender_name="爱弥斯",
            text="好嘞，1分钟倒计时开始哦",
            timestamp=1008,
            is_bot=True,
            origin=MessageOrigin.PLATFORM_HISTORY,
        ),
    )
    store.record_transition("aemeath", "d2", "g9", "OBSERVE", "alias_direct", 1010)
    store.record_transition("aemeath", "d2", "g9", "END", "sent", 1011)

    context = store.decision_context_messages(
        "aemeath",
        "g9",
        decision_id="d2",
        at_timestamp=1010,
        limit=12,
    )
    bot_texts = [item["text"] for item in context if item["is_bot"]]
    assert bot_texts.count("好嘞，1分钟倒计时开始哦") == 1


def test_decision_api_list_and_detail(monkeypatch):
    from groupmate.host.web_api import GroupmateWebAPI

    web = types.ModuleType("astrbot.api.web")
    web.json_response = lambda payload: payload
    web.error_response = lambda message, status_code=400: {
        "error": message,
        "status_code": status_code,
    }
    web.request = types.SimpleNamespace(query={"outcome": "silent", "limit": "20"})
    monkeypatch.setitem(sys.modules, "astrbot.api.web", web)

    bridge = types.SimpleNamespace(
        list_decisions=lambda **kwargs: {
            "items": [
                {
                    "decision_id": "d1",
                    "group_id": "g1",
                    "sent": False,
                    "trigger": "SOFT_MENTION",
                    "scene": "OPEN",
                    "end_reason": "participation_silence",
                }
            ],
            "active_persona": "aemeath",
        },
        get_decision_trace=lambda decision_id: (
            {
                "decision_id": decision_id,
                "group_id": "g1",
                "sent": False,
                "stages": [{"state": "END", "reason": "participation_silence", "timestamp": 1}],
            }
            if decision_id == "d1"
            else None
        ),
    )
    api = GroupmateWebAPI(bridge)

    listed = asyncio.run(api.decisions())
    assert listed["items"][0]["decision_id"] == "d1"
    assert "text" not in repr(listed)

    detail = asyncio.run(api.decision_detail("d1"))
    assert detail["stages"][0]["state"] == "END"

    missing = asyncio.run(api.decision_detail("nope"))
    assert missing["status_code"] == 404


def test_governance_api_requires_confirmation_and_applies_operations(monkeypatch):
    from groupmate.host.web_api import GroupmateWebAPI

    class Request:
        payload = {}

        @classmethod
        async def json(cls, default=None):
            return cls.payload if cls.payload is not None else default

    web = types.ModuleType("astrbot.api.web")
    web.json_response = lambda payload: payload
    web.error_response = lambda message, status_code=400: {
        "error": message,
        "status_code": status_code,
    }
    web.request = Request
    monkeypatch.setitem(sys.modules, "astrbot.api.web", web)

    deleted = []
    corrected = []
    corrected_continuity = []
    corrected_self_commitments = []
    run_self_commitments = []
    rejected_evidence = []
    reviewed_evidence = []
    bridge = types.SimpleNamespace(
        cognition_snapshot=lambda: {"identity": {"display_name": "爱弥斯"}},
        delete_governed_memory=lambda memory_id, reason: deleted.append(
            (memory_id, reason)
        )
        or {"action_id": "a-delete"},
        correct_relationship=lambda **values: corrected.append(values) or {
            "relationship": values,
            "action": {"action_id": "a-correct"},
        },
        correct_continuity_status=lambda **values: corrected_continuity.append(
            values
        )
        or {
            "item": {"item_id": values["item_id"], "status": values["status"]},
            "action": {"action_id": "a-continuity"},
        },
        correct_self_commitment_status=lambda **values: corrected_self_commitments.append(
            values
        )
        or {
            "commitment": {
                "commitment_id": values["commitment_id"],
                "status": values["status"],
            },
            "action": {"action_id": "a-self-commitment"},
        },
        run_self_commitment_now=lambda commitment_id: _async_value(
            run_self_commitments.append(commitment_id)
            or {
                "processed": 1,
                "mode": "astrbot_cron",
                "reason": "ok",
                "commitment": {
                    "commitment_id": commitment_id,
                    "status": "completed",
                },
            }
        ),
        reject_relationship_evidence=lambda event_id, reason: rejected_evidence.append(
            (event_id, reason)
        )
        or {"action_id": "a-evidence"},
        review_relationship_evidence=lambda event_id, outcome, reason: reviewed_evidence.append(
            (event_id, outcome, reason)
        )
        or {"action_id": "a-review"},
        revert_governance_action=lambda action_id, reason: {
            "action_id": "a-revert",
            "reverts_action_id": action_id,
            "reason": reason,
        },
    )
    api = GroupmateWebAPI(bridge)

    assert asyncio.run(api.cognition())["identity"]["display_name"] == "爱弥斯"
    Request.payload = {"confirm": False}
    assert asyncio.run(api.delete_memory("m1"))["status_code"] == 400

    Request.payload = {"confirm": True, "reason": "成员要求遗忘"}
    result = asyncio.run(api.delete_memory("m1"))
    assert result["deleted"] is True
    assert result["action"]["action_id"] == "a-delete"
    assert deleted == [("m1", "成员要求遗忘")]

    Request.payload = {
        "confirm": True,
        "group_id": "g1",
        "user_id": "u1",
        "familiarity": 40,
        "affinity": 12,
        "trust": 8,
        "boundary_pressure": 0,
        "reason": "管理员确认需要修正",
    }
    result = asyncio.run(api.correct_relationship())
    assert result["corrected"] is True
    assert corrected[0]["affinity"] == 12

    Request.payload = {"confirm": False, "status": "completed", "reason": "已完成"}
    assert asyncio.run(api.correct_continuity("c1"))["status_code"] == 400

    Request.payload = {
        "confirm": True,
        "status": "completed",
        "reason": "本人已在群里确认完成",
    }
    result = asyncio.run(api.correct_continuity("c1"))
    assert result["corrected"] is True
    assert result["item"]["status"] == "completed"
    assert corrected_continuity == [
        {
            "item_id": "c1",
            "status": "completed",
            "reason": "本人已在群里确认完成",
        }
    ]

    Request.payload = {
        "confirm": True,
        "status": "blocked",
        "reason": "当前缺少执行所需权限",
    }
    result = asyncio.run(api.correct_self_commitment("sc1"))
    assert result["corrected"] is True
    assert result["commitment"]["status"] == "blocked"
    assert corrected_self_commitments == [
        {
            "commitment_id": "sc1",
            "status": "blocked",
            "reason": "当前缺少执行所需权限",
        }
    ]

    Request.payload = {"confirm": False}
    assert asyncio.run(api.run_self_commitment("sc1"))["status_code"] == 400

    Request.payload = {"confirm": True}
    result = asyncio.run(api.run_self_commitment("sc1"))
    assert result["started"] is True
    assert result["commitment"]["status"] == "completed"
    assert run_self_commitments == ["sc1"]

    Request.payload = {"confirm": True, "reason": "不是对爱弥斯说的"}
    result = asyncio.run(api.reject_relationship_evidence("e1"))
    assert result["rejected"] is True
    assert rejected_evidence == [("e1", "不是对爱弥斯说的")]

    Request.payload = {
        "confirm": True,
        "outcome": "wrong_person",
        "reason": "这句话实际是对另一位群友说的",
    }
    result = asyncio.run(api.review_relationship_evidence("e2"))
    assert result["reviewed"] is True
    assert reviewed_evidence == [
        ("e2", "wrong_person", "这句话实际是对另一位群友说的")
    ]

    Request.payload = {"confirm": True, "reason": "恢复误操作"}
    result = asyncio.run(api.revert_governance("a-correct"))
    assert result["reverted"] is True
    assert result["action"]["reverts_action_id"] == "a-correct"


def test_store_governance_lists_cross_group_memory_and_relationships(store):
    from groupmate.models import MemoryItem, MemoryKind, RelationshipState

    store.add_memory(
        "aemeath",
        MemoryItem(
            memory_id="m1",
            group_id="g1",
            subject_id="u1",
            kind=MemoryKind.PROFILE,
            text="喜欢喝乌龙茶",
            created_at=100,
        ),
    )
    store.add_memory(
        "aemeath",
        MemoryItem(
            memory_id="m2",
            group_id="g2",
            subject_id="u2",
            kind=MemoryKind.EPISODIC,
            text="一起讨论过旅行",
            created_at=200,
        ),
    )
    store.upsert_relationship_state(
        "aemeath",
        RelationshipState(
            group_id="g1",
            user_id="u1",
            familiarity=20,
            affinity=5,
            updated_at=300,
        ),
    )

    memories = store.list_recent_memories("aemeath", now=250, limit=10)
    assert [item.memory_id for item in memories] == ["m2", "m1"]
    assert store.list_recent_memories(
        "aemeath", group_id="g1", now=250, limit=10
    )[0].memory_id == "m1"
    relationships = store.list_relationship_states("aemeath")
    assert relationships[0].user_id == "u1"


def test_governance_audit_corrects_and_reverts_relationship_atomically(store):
    from groupmate.models import RelationshipState

    original = RelationshipState(
        group_id="g1",
        user_id="u1",
        familiarity=20,
        affinity=5,
        trust=3,
        boundary_pressure=1,
        interaction_count=9,
        updated_at=100,
    )
    store.upsert_relationship_state("aemeath", original)
    corrected = RelationshipState(
        group_id="g1",
        user_id="u1",
        familiarity=50,
        affinity=12,
        trust=10,
        boundary_pressure=0,
        interaction_count=9,
        updated_at=200,
    )

    action = store.correct_relationship_with_audit(
        "aemeath", corrected, reason="人工确认", actor="管理员", now=200
    )
    assert action["before"]["affinity"] == 5
    assert action["after"]["affinity"] == 12
    assert action["can_revert"] is True
    assert store.get_relationship_state("aemeath", "g1", "u1").affinity == 12

    rollback = store.revert_governance_action(
        "aemeath", action["action_id"], reason="修正误操作", actor="管理员", now=300
    )
    assert rollback["reverts_action_id"] == action["action_id"]
    restored = store.get_relationship_state("aemeath", "g1", "u1")
    assert restored.affinity == 5
    history = store.list_governance_actions("aemeath")
    assert [item["action_type"] for item in history] == [
        "governance_reverted",
        "relationship_corrected",
    ]
    assert history[1]["reverted_at"] == 300
    assert history[1]["can_revert"] is False


def test_governance_audit_deletes_and_restores_memory_with_tombstone(store):
    from groupmate.models import MemoryItem, MemoryKind
    from groupmate.memory.privacy import claim_hash

    store.add_memory(
        "aemeath",
        MemoryItem(
            memory_id="m-restore",
            group_id="g1",
            subject_id="u1",
            kind=MemoryKind.PROFILE,
            text="喜欢乌龙茶",
            created_at=100,
        ),
    )
    action = store.delete_memory_with_audit(
        "aemeath", "m-restore", reason="成员要求", actor="管理员", now=200
    )
    assert action["action_type"] == "memory_deleted"
    assert store.get_memory("aemeath", "m-restore").status.value == "deleted"
    assert store.has_tombstone(
        "aemeath", "g1", "u1", claim_hash("喜欢乌龙茶")
    )

    store.revert_governance_action(
        "aemeath", action["action_id"], reason="确认误删", actor="管理员", now=300
    )
    assert store.get_memory("aemeath", "m-restore").status.value == "accepted"
    assert not store.has_tombstone(
        "aemeath", "g1", "u1", claim_hash("喜欢乌龙茶")
    )


def test_governance_rejects_rollback_when_newer_target_action_exists(store):
    from groupmate.models import RelationshipState

    first = store.correct_relationship_with_audit(
        "aemeath",
        RelationshipState(group_id="g1", user_id="u1", affinity=10, updated_at=100),
        reason="第一次",
        actor="管理员",
        now=100,
    )
    store.correct_relationship_with_audit(
        "aemeath",
        RelationshipState(group_id="g1", user_id="u1", affinity=20, updated_at=200),
        reason="第二次",
        actor="管理员",
        now=200,
    )
    with pytest.raises(ValueError, match="newer governance action"):
        store.revert_governance_action(
            "aemeath", first["action_id"], reason="错误回滚", actor="管理员", now=300
        )


def test_rejecting_relationship_evidence_rebuilds_and_can_be_reverted(store):
    from groupmate.models import SocialEvent, SocialEventKind, SocialEventStatus

    first = SocialEvent(
        event_id="e1",
        group_id="g1",
        user_id="u1",
        kind=SocialEventKind.THANKS,
        source_message_id="m1",
        confidence=0.95,
        occurred_at=100,
        evidence_text="谢谢你",
    )
    second = SocialEvent(
        event_id="e2",
        group_id="g1",
        user_id="u1",
        kind=SocialEventKind.PRAISE,
        source_message_id="m2",
        confidence=0.95,
        occurred_at=110,
        evidence_text="你真厉害",
    )
    store.record_social_interaction("aemeath", first, now=100)
    store.record_social_interaction("aemeath", second, now=110)
    assert store.get_relationship_state("aemeath", "g1", "u1").affinity == 4

    action = store.reject_social_event_with_audit(
        "aemeath", "e1", reason="并不是对爱弥斯说的", actor="管理员", now=200
    )

    assert action["action_type"] == "relationship_evidence_rejected"
    assert action["can_revert"] is True
    assert store.get_social_event("aemeath", "e1").status is SocialEventStatus.REJECTED
    assert store.get_relationship_state("aemeath", "g1", "u1").affinity == 2

    store.revert_governance_action(
        "aemeath", action["action_id"], reason="确认误判", actor="管理员", now=300
    )
    assert store.get_social_event("aemeath", "e1").status is SocialEventStatus.ACCEPTED
    assert store.get_relationship_state("aemeath", "g1", "u1").affinity == 4


def test_pending_relationship_evidence_requires_review_before_it_changes_state(store):
    from groupmate.models import SocialEvent, SocialEventKind, SocialEventStatus

    event = SocialEvent(
        event_id="pending-e1",
        group_id="g1",
        user_id="u1",
        kind=SocialEventKind.THANKS,
        source_message_id="m-pending",
        confidence=0.96,
        occurred_at=100,
        evidence_text="谢谢你",
        status=SocialEventStatus.PENDING,
    )
    assert store.record_social_interaction("aemeath", event, now=100) is None
    assert store.get_relationship_state("aemeath", "g1", "u1") is None

    action = store.review_pending_social_event_with_audit(
        "aemeath",
        event.event_id,
        outcome="correct",
        reason="确认是对爱弥斯的感谢",
        actor="管理员",
        now=200,
    )

    reviewed = store.get_social_event("aemeath", event.event_id)
    assert action["action_type"] == "relationship_evidence_reviewed"
    assert reviewed.status is SocialEventStatus.ACCEPTED
    assert reviewed.review_code == "correct"
    assert store.get_relationship_state("aemeath", "g1", "u1").affinity == 2
    quality = store.relationship_learning_quality("aemeath", "g1")
    assert quality["reviewed_count"] == 1
    assert quality["error_rate"] == 0


def test_relationship_review_error_categories_feed_quality_gate(store):
    from groupmate.models import SocialEvent, SocialEventKind, SocialEventStatus

    for index, outcome in enumerate(("wrong_person", "wrong_kind", "insufficient_context")):
        event = SocialEvent(
            event_id=f"pending-{index}",
            group_id="g1",
            user_id="u1",
            kind=SocialEventKind.PRAISE,
            source_message_id=f"m-{index}",
            confidence=0.96,
            occurred_at=100 + index,
            evidence_text="你真厉害",
            status=SocialEventStatus.PENDING,
        )
        store.record_social_interaction("aemeath", event, now=100 + index)
        store.review_pending_social_event_with_audit(
            "aemeath",
            event.event_id,
            outcome=outcome,
            reason="人工复核误判",
            actor="管理员",
            now=200 + index,
        )

    quality = store.relationship_learning_quality("aemeath", "g1")
    assert quality["reviewed_count"] == 3
    assert quality["error_count"] == 3
    assert quality["wrong_person"] == 1
    assert quality["wrong_kind"] == 1
    assert quality["insufficient_context"] == 1
    assert quality["error_rate"] == 1.0


def test_rejecting_auto_applied_evidence_counts_as_quality_error(store):
    from groupmate.models import SocialEvent, SocialEventKind

    event = SocialEvent(
        event_id="auto-e1",
        group_id="g1",
        user_id="u1",
        kind=SocialEventKind.THANKS,
        source_message_id="m-auto",
        confidence=0.99,
        occurred_at=100,
        evidence_text="谢谢你",
    )
    store.record_social_interaction("aemeath", event, now=100)
    store.reject_social_event_with_audit(
        "aemeath",
        event.event_id,
        reason="自动生效后确认误判",
        actor="管理员",
        now=200,
    )

    quality = store.relationship_learning_quality("aemeath", "g1")
    assert quality["reviewed_count"] == 1
    assert quality["error_count"] == 1
    assert quality["other_error"] == 1

def test_manual_relationship_correction_is_rebuild_baseline(store):
    from groupmate.models import RelationshipState, SocialEvent, SocialEventKind

    store.record_social_interaction(
        "aemeath",
        SocialEvent(
            event_id="e-old",
            group_id="g1",
            user_id="u1",
            kind=SocialEventKind.THANKS,
            source_message_id="m1",
            confidence=0.95,
            occurred_at=100,
            evidence_text="谢谢",
        ),
        now=100,
    )
    store.correct_relationship_with_audit(
        "aemeath",
        RelationshipState(
            group_id="g1",
            user_id="u1",
            familiarity=40,
            affinity=50,
            trust=20,
            interaction_count=1,
            updated_at=200,
        ),
        reason="人工确认关系",
        actor="管理员",
        now=200,
    )

    store.reject_social_event_with_audit(
        "aemeath", "e-old", reason="旧证据误判", actor="管理员", now=300
    )

    state = store.get_relationship_state("aemeath", "g1", "u1")
    assert state.affinity == 50
    assert state.familiarity == 40
