import json

import pytest

from groupmate.evaluation.models import ShadowRecord
from groupmate.memory import SQLiteMemoryStore
from groupmate.web_api import (
    GroupmateWebAPI,
    build_overview_payload,
    label_shadow_decision,
    normalize_label,
    parse_shadow_filters,
    serialize_shadow_decision,
    set_runtime_paused,
)


def shadow_record(**overrides):
    values = {
        "decision_id": "d1",
        "group_hash": "group-hash",
        "sender_hash": "sender-hash",
        "trigger": "candidate",
        "action": "ignore",
        "confidence": 0.2,
        "reason_code": "not_useful",
        "would_rate_limit": False,
        "features": {"message_count": 2},
        "context": None,
        "model_id": "model-a",
        "policy_version": "1",
        "latency_ms": 12.5,
        "error_code": None,
        "created_at": 10,
        "expires_at": 100,
    }
    values.update(overrides)
    return ShadowRecord(**values)


class FakeRuntime:
    def snapshots(self):
        return {}


class FakeBridge:
    def __init__(self, memory, store_text=False):
        self.memory = memory
        self.paused = False
        self.runtime = FakeRuntime()
        self.settings = {
            "shadow_mode": True,
            "shadow_store_message_text": store_text,
            "shadow_retention_days": 7,
        }

    def _setting(self, key, default=None):
        return self.settings.get(key, default)

    def status(self):
        return {
            "paused": self.paused,
            "shadow_mode": self.settings["shadow_mode"],
            "groups": self.runtime.snapshots(),
            "shadow": self.memory.shadow_stats(),
        }


def test_overview_payload_excludes_identity_fields(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    store.save_shadow_decision(
        shadow_record(
            decision_id="pending",
            action="respond",
            created_at=30,
            context=[{"sender": "成员1", "text": "请回复"}],
        )
    )
    store.save_shadow_decision(
        shadow_record(decision_id="labeled", created_at=20)
    )
    store.label_shadow_decision("labeled", "must_silence", 40)
    bridge = FakeBridge(store, store_text=True)

    payload = build_overview_payload(bridge)
    text = json.dumps(payload, ensure_ascii=False)

    assert "group_hash" not in text
    assert "sender_hash" not in text
    assert "context_json" not in text
    assert payload["pending_count"] == 1
    assert payload["runtime"]["initialized_group_count"] == 0
    assert payload["recent"][0]["message_preview"] == "请回复"
    store.close()


def test_decision_serializer_hides_context_when_disabled():
    row = {
        "decision_id": "d1",
        "trigger": "candidate",
        "action": "ignore",
        "confidence": 0.2,
        "reason_code": "not_useful",
        "would_rate_limit": 0,
        "label": "unlabeled",
        "labeled_at": None,
        "model_id": "model-a",
        "policy_version": "1",
        "latency_ms": 1.0,
        "error_code": None,
        "created_at": 10,
        "context_json": json.dumps(
            [{"sender": "成员1", "text": "你好", "index": 1}]
        ),
    }

    hidden = serialize_shadow_decision(row, include_context=False)
    visible = serialize_shadow_decision(row, include_context=True)

    assert hidden["message_preview"] == "未保存文本"
    assert "context" not in hidden
    assert visible["context"][0]["sender"] == "成员1"


def test_filter_parser_rejects_invalid_values():
    with pytest.raises(ValueError):
        parse_shadow_filters({"label": "drop-table"})
    with pytest.raises(ValueError):
        parse_shadow_filters({"limit": "999"})
    assert parse_shadow_filters({"label": "unlabeled", "action": "ignore"})[:3] == (
        "unlabeled",
        "ignore",
        20,
    )


def test_runtime_toggle_is_explicitly_runtime_only():
    bridge = FakeBridge(SQLiteMemoryStore(":memory:"))
    assert set_runtime_paused(bridge, True) == {
        "paused": True,
        "runtime_only": True,
    }
    assert bridge.paused is True
    bridge.memory.close()


def test_label_is_validated_idempotently_and_keeps_prediction(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    store.save_shadow_decision(shadow_record(action="ignore"))
    bridge = FakeBridge(store)

    assert normalize_label("must_respond") == "must_respond"
    assert normalize_label("not-a-label") is None
    first = label_shadow_decision(bridge, "d1", "must_respond", labeled_at=20)
    second = label_shadow_decision(bridge, "d1", "must_respond", labeled_at=21)

    assert first["action"] == "ignore"
    assert first["label"] == "must_respond"
    assert second["label"] == "must_respond"
    assert label_shadow_decision(bridge, "missing", "must_respond", labeled_at=20) is None
    store.close()


def test_web_api_registers_expected_routes():
    class Context:
        def __init__(self):
            self.calls = []

        def register_web_api(self, route, handler, methods, desc):
            self.calls.append((route, handler.__name__, tuple(methods), desc))

    context = Context()
    api = GroupmateWebAPI(FakeBridge(SQLiteMemoryStore(":memory:")), "/tmp")
    api.register(context)

    assert [route for route, *_ in context.calls] == [
        "/astrbot_plugin_groupmate/dashboard/overview",
        "/astrbot_plugin_groupmate/shadow/decisions",
        "/astrbot_plugin_groupmate/shadow/decisions/<decision_id>/label",
        "/astrbot_plugin_groupmate/runtime/pause",
        "/astrbot_plugin_groupmate/runtime/resume",
        "/astrbot_plugin_groupmate/shadow/export",
    ]
