from types import SimpleNamespace

import pytest

from groupmate.host.event_adapters import (
    HostEventAdapterStatus,
    PokeEventAdapter,
)
from groupmate.models import MessageOrigin


class Poke:
    type = "poke"

    def __init__(self, qq):
        self.qq = qq


class Event:
    def __init__(
        self,
        target="bot",
        *,
        component=True,
        raw_segment=False,
        raw_notice=False,
    ):
        message = [Poke(target)] if component else []
        raw_message = {
            "message_id": "notice-1",
            "group_id": "g1",
            "user_id": "u1",
            "target_id": target,
            "time": 100,
            "sender": {"nickname": "Alice"},
            "message": (
                [{"type": "poke", "data": {"qq": target}}]
                if raw_segment
                else []
            ),
        }
        if raw_notice:
            raw_message["sub_type"] = "poke"
        self.message_obj = SimpleNamespace(
            message_id="notice-1",
            timestamp=100,
            message=message,
            raw_message=raw_message,
        )
        self.unified_msg_origin = "aiocqhttp:GroupMessage:g1"

    def get_group_id(self):
        return "g1"

    def get_sender_id(self):
        return "u1"

    def get_sender_name(self):
        return "Alice"

    def get_self_id(self):
        return "bot"


def test_poke_is_bypassed_when_disabled():
    result = PokeEventAdapter(enabled=False).adapt(Event())

    assert result.status is HostEventAdapterStatus.BYPASSED
    assert result.reason_code == "disabled"


def test_poke_targeting_another_user_is_bypassed():
    result = PokeEventAdapter(enabled=True).adapt(Event("u2"))

    assert result.status is HostEventAdapterStatus.BYPASSED
    assert result.reason_code == "target_not_bot"


@pytest.mark.parametrize(
    "event",
    [
        Event(component=True),
        Event(component=False, raw_segment=True),
        Event(component=False, raw_notice=True),
    ],
)
def test_poke_targeting_bot_becomes_whitelisted_synthetic_message(event):
    result = PokeEventAdapter(enabled=True).adapt(event)

    assert result.status is HostEventAdapterStatus.ADMITTED
    assert result.message.origin is MessageOrigin.SYSTEM_SYNTHETIC
    assert result.message.text == ""
    assert result.message.segment_types == ("poke",)
    assert result.message.platform == "aiocqhttp"
    assert result.message.bot_id == "bot"
    assert result.message.metadata == {
        "interaction_kind": "poke",
        "target_id": "bot",
        "source_adapter": "aiocqhttp_poke",
    }
    assert "raw" not in repr(result.message.metadata).lower()


def test_non_poke_is_not_matched():
    ordinary = Event(component=False)
    ordinary.message_obj.raw_message["message"] = [
        {"type": "text", "data": {"text": "hello"}}
    ]

    result = PokeEventAdapter(True).adapt(ordinary)

    assert result.status is HostEventAdapterStatus.NOT_MATCHED


def test_other_platform_poke_is_not_matched():
    event = Event()
    event.unified_msg_origin = "discord:GroupMessage:g1"

    result = PokeEventAdapter(True).adapt(event)

    assert result.status is HostEventAdapterStatus.NOT_MATCHED


@pytest.mark.parametrize(
    "break_event",
    [
        lambda event: setattr(event, "get_group_id", lambda: ""),
        lambda event: setattr(event.message_obj, "timestamp", 0),
    ],
)
def test_missing_identity_or_timestamp_is_invalid(break_event):
    event = Event()
    break_event(event)
    if event.message_obj.timestamp == 0:
        event.message_obj.raw_message.pop("time")

    result = PokeEventAdapter(True).adapt(event)

    assert result.status is HostEventAdapterStatus.BYPASSED
    assert result.reason_code == "invalid_event"


def test_sender_name_falls_back_to_sender_id():
    event = Event()
    event.get_sender_name = lambda: ""

    result = PokeEventAdapter(True).adapt(event)

    assert result.message.sender_name == "u1"


def test_getter_exception_fails_closed():
    event = Event()

    def fail():
        raise RuntimeError("boom")

    event.get_self_id = fail

    result = PokeEventAdapter(True).adapt(event)

    assert result.status is HostEventAdapterStatus.BYPASSED
    assert result.reason_code == "invalid_event"


def test_fallback_event_id_is_deterministic():
    event = Event()
    event.message_obj.message_id = ""
    event.message_obj.raw_message.pop("message_id")

    first = PokeEventAdapter(True).adapt(event).message.message_id
    second = PokeEventAdapter(True).adapt(event).message.message_id

    assert first == second
    assert first.startswith("poke-")


def test_existing_event_id_is_prefixed_once():
    event = Event()
    event.message_obj.message_id = "poke-notice-1"

    result = PokeEventAdapter(True).adapt(event)

    assert result.message.message_id == "poke-notice-1"
