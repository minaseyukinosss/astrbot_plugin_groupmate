from types import SimpleNamespace

from groupmate.social_runtime.attention import AttentionFrame
from groupmate.social_runtime.contracts import SocialEventEnvelope
from groupmate.social_runtime.control.message_traces import MessageTraceRepository
from groupmate.social_runtime.governor import GovernorResult


def _platform_event(
    message_id: str,
    *,
    card: str = "",
    nickname: str = "小夏",
    owner: str = "UNKNOWN",
) -> SocialEventEnvelope:
    display_name = card or nickname
    return SocialEventEnvelope.create(
        event_id=f"qq:{message_id}",
        event_type="platform.message",
        occurred_at=10,
        received_at=10,
        persona_id="groupmate:default",
        group_id="g-1",
        actor_id="42",
        source_message_id=message_id,
        correlation_id=f"qq:{message_id}",
        causation_id=None,
        payload={
            "text": "今晚一起打游戏吗？",
            "sender": {"id": "42", "name": display_name},
            "interaction_owner": owner,
            "external_trigger_kind": "command" if owner == "EXTERNAL_PLUGIN" else None,
            "media": [],
        },
    )


def _evaluation(event: SocialEventEnvelope, outcome: str):
    frame = AttentionFrame(
        frame_id="frame-1",
        group_id="g-1",
        scene_version=1,
        trigger_kind="AMBIENT",
        focus_topic_ids=(),
        focus_event_ids=(event.event_id,),
        candidate_audiences=("42",),
        urgency="normal",
        deadline=20,
        requested_workers=(),
        persona_state_version=1,
        config_version=1,
    )
    result = GovernorResult(
        outcome=outcome,
        selected_intention_ids=("intent-1",) if outcome == "ACT" else (),
        rejected=(),
        reason_codes=("no_eligible_intention",) if outcome == "SILENCE" else (),
        reconsider_at=30 if outcome == "DEFER" else None,
        constraints=(),
    )
    return SimpleNamespace(
        source_event=event,
        runtime_mode=SimpleNamespace(value="SHADOW"),
        frame=frame,
        governor_result=result,
        accepted=True,
        status="evaluated",
        candidate_response="我会先看报错第一行。" if outcome == "ACT" else None,
        reply_diagnostic=None,
        participation_lane="DIRECT_FAST",
        participation_diagnostics=("deterministic_direct_fast",),
        candidates=(SimpleNamespace(intention_id="intent-1"),),
        cognition_diagnostics=(
            SimpleNamespace(
                worker="direct_interaction",
                status="SUCCEEDED",
                latency_ms=120,
                diagnostic_code=None,
                queue_wait_ms=4,
                provider_latency_ms=100,
                input_bytes=2048,
                timeout_ms=8000,
            ),
        ),
    )


def test_trace_updates_one_message_instead_of_appending_projection_rows(tmp_path):
    repo = MessageTraceRepository(tmp_path / "runtime.db")
    event = _platform_event("m-1", card="夏夏", nickname="小夏")

    repo.record_received(event, runtime_mode="SHADOW", now=10)
    repo.mark_entered(event.event_id, now=11)
    repo.record_evaluation(_evaluation(event, outcome="SILENCE"), now=12)

    view = repo.query(persona_id="groupmate:default", group_id="g-1")
    assert len(view["items"]) == 1
    summary = view["items"][0]["summary"]
    assert set(summary) == {
        "actor",
        "message",
        "route",
        "understanding",
        "decision",
        "delivery",
        "timing",
        "stages",
    }
    assert summary["actor"]["display_name"] == "夏夏"
    assert summary["route"]["owner"] == "GROUPMATE"
    assert summary["decision"]["outcome"] == "SILENCE"
    assert summary["delivery"]["status"] == "SILENT"
    assert "42" not in str(view["items"][0])

    detail = repo.detail(
        persona_id="groupmate:default",
        group_id="g-1",
        trace_ref=view["items"][0]["entity_ref"],
    )
    assert [stage["kind"] for stage in detail["summary"]["stages"]] == [
        "RECEIVED",
        "ROUTED",
        "ATTENDED",
        "UNDERSTOOD",
        "DECIDED",
    ]


def test_external_trigger_is_visible_without_claiming_plugin_success(tmp_path):
    repo = MessageTraceRepository(tmp_path / "runtime.db")
    event = _platform_event("m-2", owner="EXTERNAL_PLUGIN")

    repo.record_received(event, runtime_mode="SOCIAL_RUNTIME", now=20)

    item = repo.query(
        persona_id="groupmate:default", group_id="g-1"
    )["items"][0]
    assert item["summary"]["route"] == {
        "owner": "EXTERNAL_PLUGIN",
        "label": "交给外部能力",
        "reason": "匹配已配置的外部触发规则",
    }
    assert item["summary"]["delivery"]["status"] == "HANDED_OFF"
    assert item["summary"]["delivery"]["label"] == "结果由外部插件负责"


def test_query_orders_newest_message_first(tmp_path):
    repo = MessageTraceRepository(tmp_path / "runtime.db")
    repo.record_received(_platform_event("old"), runtime_mode="SHADOW", now=10)
    repo.record_received(_platform_event("new"), runtime_mode="SHADOW", now=20)

    items = repo.query(persona_id="groupmate:default", group_id="g-1")["items"]
    assert [item["summary"]["timing"]["received_at"] for item in items] == [20, 10]


def test_shadow_act_keeps_pre_gate_decision_separate_from_delivery(tmp_path):
    repo = MessageTraceRepository(tmp_path / "runtime.db")
    event = _platform_event("shadow-act")

    repo.record_received(event, runtime_mode="SHADOW", now=10)
    repo.mark_entered(event.event_id, now=11)
    repo.record_evaluation(_evaluation(event, outcome="ACT"), now=12)

    summary = repo.query(
        persona_id="groupmate:default", group_id="g-1"
    )["items"][0]["summary"]
    assert summary["decision"]["pre_gate_outcome"] == "ACT"
    assert summary["decision"]["candidate_response"] == "我会先看报错第一行。"
    assert summary["decision"]["participation_lane"] == "DIRECT_FAST"
    assert summary["decision"]["would_reply"] is True
    assert summary["delivery"]["status"] == "BLOCKED_BY_SHADOW"
    assert summary["delivery"]["label"] == "SHADOW：已完成判断，未发送"
    assert summary["understanding"]["diagnostics"][0]["status"] == "SUCCEEDED"
    assert summary["understanding"]["diagnostics"][0] == {
        "worker": "direct_interaction",
        "status": "SUCCEEDED",
        "latency_ms": 120,
        "diagnostic_code": None,
        "queue_wait_ms": 4,
        "provider_latency_ms": 100,
        "input_bytes": 2048,
        "timeout_ms": 8000,
    }
    assert summary["understanding"]["candidate_count"] == 1
    assert summary["understanding"]["candidate_source"] == "deterministic"
    assert "chain_of_thought" not in str(summary)
    assert "prompt" not in str(summary)


def test_non_text_segments_keep_order_without_exposing_platform_sources(tmp_path):
    repo = MessageTraceRepository(tmp_path / "runtime.db")
    event = SocialEventEnvelope.create(
        event_id="qq:media-1",
        event_type="platform.message",
        occurred_at=10,
        received_at=10,
        persona_id="groupmate:default",
        group_id="g-1",
        actor_id="42",
        source_message_id="media-1",
        correlation_id="qq:media-1",
        causation_id=None,
        payload={
            "text": "看看",
            "sender": {"id": "42", "name": "夏夏"},
            "interaction_owner": "UNKNOWN",
            "segments": [
                {"type": "text", "data": {"text": "看看"}},
                {
                    "type": "image",
                    "data": {
                        "url": "https://multimedia.nt.qq.com.cn/demo.jpg",
                        "summary": "猫猫照片",
                        "file_size": 2048,
                    },
                },
                {"type": "record", "data": {"file": "voice.amr", "file_size": 4096}},
                {"type": "video", "data": {"url": "https://example.com/demo.mp4"}},
                {"type": "file", "data": {"file": "发布清单.pdf", "file_size": 8192}},
                {"type": "face", "data": {"id": "14"}},
            ],
            "media": [
                {"type": "image", "url": "https://multimedia.nt.qq.com.cn/demo.jpg"},
                {"type": "record", "file": "voice.amr"},
                {"type": "video", "url": "https://example.com/demo.mp4"},
                {"type": "file", "file": "发布清单.pdf"},
            ],
        },
    )

    repo.record_received(event, runtime_mode="SHADOW", now=10)

    message = repo.query(
        persona_id="groupmate:default", group_id="g-1"
    )["items"][0]["summary"]["message"]
    assert message["summary"] == "看看 · 图片 · 语音 · 视频 · 文件 · QQ 表情"
    assert [part["kind"] for part in message["parts"]] == [
        "text",
        "image",
        "record",
        "video",
        "file",
        "face",
    ]
    assert message["parts"][1] == {
        "kind": "image",
        "label": "图片",
        "media_ref": message["parts"][1]["media_ref"],
        "name": "猫猫照片",
        "size": 2048,
        "preview": "image",
    }
    assert message["parts"][4]["name"] == "发布清单.pdf"
    public_text = str(message)
    assert "multimedia.nt.qq.com.cn" not in public_text
    assert "example.com" not in public_text
    assert "voice.amr" not in public_text
    assert '"14"' not in public_text


def test_at_segment_uses_known_member_name_without_exposing_qq_id(tmp_path):
    repo = MessageTraceRepository(tmp_path / "runtime.db")
    mentioned = _platform_event("known-member", card="夏夏")
    repo.record_received(mentioned, runtime_mode="SHADOW", now=10)
    event = SocialEventEnvelope.create(
        event_id="qq:mention-1",
        event_type="platform.message",
        occurred_at=11,
        received_at=11,
        persona_id="groupmate:default",
        group_id="g-1",
        actor_id="84",
        source_message_id="mention-1",
        correlation_id="qq:mention-1",
        causation_id=None,
        payload={
            "text": "@夏夏 看这里",
            "sender": {"id": "84", "name": "小林"},
            "interaction_owner": "UNKNOWN",
            "segments": [
                {"type": "at", "data": {"qq": "42"}},
                {"type": "text", "data": {"text": " 看这里"}},
            ],
        },
    )

    repo.record_received(event, runtime_mode="SHADOW", now=11)

    message = repo.query(
        persona_id="groupmate:default", group_id="g-1"
    )["items"][0]["summary"]["message"]
    mention = message["parts"][0]
    assert mention["kind"] == "at"
    assert mention["label"] == "@夏夏"
    assert mention["display_name"] == "夏夏"
    assert mention["member_ref"].startswith("member:")
    assert mention["avatar_ref"].startswith("participant:")
    assert "42" not in str(message)


def test_at_all_and_unknown_member_have_safe_labels(tmp_path):
    repo = MessageTraceRepository(tmp_path / "runtime.db")
    event = SocialEventEnvelope.create(
        event_id="qq:mention-2",
        event_type="platform.message",
        occurred_at=10,
        received_at=10,
        persona_id="groupmate:default",
        group_id="g-1",
        actor_id="84",
        source_message_id="mention-2",
        correlation_id="qq:mention-2",
        causation_id=None,
        payload={
            "text": "",
            "sender": {"id": "84", "name": "小林"},
            "interaction_owner": "UNKNOWN",
            "segments": [
                {"type": "at", "data": {"qq": "all"}},
                {"type": "at", "data": {"qq": "99887766"}},
            ],
        },
    )

    repo.record_received(event, runtime_mode="SHADOW", now=10)

    parts = repo.query(
        persona_id="groupmate:default", group_id="g-1"
    )["items"][0]["summary"]["message"]["parts"]
    assert parts == [
        {"kind": "at", "label": "@全体成员", "display_name": "全体成员"},
        {"kind": "at", "label": "@未知成员", "display_name": "未知成员"},
    ]
    assert "99887766" not in str(parts)
