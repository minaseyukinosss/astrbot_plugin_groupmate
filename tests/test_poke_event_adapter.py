from types import SimpleNamespace

import pytest

from groupmate.host.event_adapters import (
    HostEventAdapterStatus,
    PokeEventAdapter,
)
from groupmate.models import MessageOrigin


class LegacyPoke:
    """Legacy attribute-only poke used by older adapters/tests."""

    type = "poke"

    def __init__(self, qq):
        self.qq = qq


class AstrBotPoke:
    """Mirrors current AstrBot Poke: target_id() method, id field, qq=0."""

    type = "Poke"

    def __init__(self, target):
        self.id = target
        self.qq = 0

    def target_id(self):
        for value in (self.id, self.qq):
            text = str(value or "").strip()
            if text and text != "0":
                return text
        return None


class Event:
    def __init__(
        self,
        target="bot",
        *,
        component=True,
        raw_segment=False,
        raw_notice=False,
        poke_factory=LegacyPoke,
        raw_segment_data=None,
    ):
        message = [poke_factory(target)] if component else []
        if raw_segment:
            segment_data = (
                {"qq": target}
                if raw_segment_data is None
                else dict(raw_segment_data)
            )
            raw_segments = [{"type": "poke", "data": segment_data}]
        else:
            raw_segments = []
        raw_message = {
            "message_id": "notice-1",
            "group_id": "g1",
            "user_id": "u1",
            "target_id": target,
            "time": 100,
            "sender": {"nickname": "Alice"},
            "message": raw_segments,
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
        Event(component=True, poke_factory=AstrBotPoke),
        Event(component=False, raw_segment=True),
        Event(
            component=False,
            raw_segment=True,
            raw_segment_data={"type": "126", "id": "bot"},
        ),
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


def test_astrbot_poke_method_target_id_is_not_stringified():
    """Regression: getattr(Poke, 'target_id') is a method on current AstrBot."""
    result = PokeEventAdapter(enabled=True).adapt(
        Event(poke_factory=AstrBotPoke)
    )

    assert result.status is HostEventAdapterStatus.ADMITTED
    assert result.message.metadata["target_id"] == "bot"


def test_astrbot_poke_other_target_still_bypasses():
    result = PokeEventAdapter(enabled=True).adapt(
        Event("u2", poke_factory=AstrBotPoke)
    )

    assert result.status is HostEventAdapterStatus.BYPASSED
    assert result.reason_code == "target_not_bot"


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


def test_platform_id_umo_still_matches_via_platform_name():
    """AstrBot umo uses platform id (e.g. default), not adapter name."""
    event = Event(poke_factory=AstrBotPoke)
    event.unified_msg_origin = "default:GroupMessage:912113397"
    event.get_platform_name = lambda: "aiocqhttp"

    result = PokeEventAdapter(True).adapt(event)

    assert result.status is HostEventAdapterStatus.ADMITTED


def test_platform_id_umo_without_platform_name_is_not_matched():
    event = Event(poke_factory=AstrBotPoke)
    event.unified_msg_origin = "default:GroupMessage:912113397"

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
