"""Decision ledger queries and plugin-page API for path transparency."""

import asyncio
import sys
import types

import pytest

from groupmate.memory.store import SQLiteMemoryStore


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
