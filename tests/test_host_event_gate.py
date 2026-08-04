from types import SimpleNamespace

import pytest

from groupmate.host.event_gate import HostEventDisposition, HostEventGate


class CommandFilter:
    pass


class EventMessageTypeFilter:
    pass


class FakeEvent:
    def __init__(
        self,
        *,
        text="普通消息",
        raw_text=None,
        filters=(),
        at_bot=False,
        group_id="g1",
        sender_id="u1",
        stopped=False,
    ):
        segments = []
        if at_bot:
            segments.append({"type": "at", "data": {"qq": "bot"}})
        segments.append(
            {
                "type": "text",
                "data": {"text": text if raw_text is None else raw_text},
            }
        )
        self.message_str = text
        self.message_obj = SimpleNamespace(
            raw_message={"message": segments},
            message=(),
        )
        self.unified_msg_origin = "aiocqhttp:GroupMessage:{}".format(group_id)
        self.is_at_or_wake_command = at_bot
        self._group_id = group_id
        self._sender_id = sender_id
        self._stopped = stopped
        self._extras = {
            "activated_handlers": [
                SimpleNamespace(event_filters=list(filters))
            ]
            if filters
            else []
        }

    def get_group_id(self):
        return self._group_id

    def get_sender_id(self):
        return self._sender_id

    def get_self_id(self):
        return "bot"

    def get_extra(self, key=None, default=None):
        return self._extras.get(key, default)

    def is_stopped(self):
        return self._stopped


def gate(prefixes=("/",), enabled_groups=()):
    return HostEventGate(
        config_resolver=lambda umo: {"wake_prefix": list(prefixes)},
        enabled_groups=enabled_groups,
    )


@pytest.mark.parametrize("command_index", [0, 1])
def test_registered_command_wins_regardless_of_handler_order(command_index):
    event = FakeEvent(text="取名 小明", raw_text="/取名 小明")
    generic = SimpleNamespace(event_filters=[EventMessageTypeFilter()])
    command = SimpleNamespace(event_filters=[CommandFilter()])
    handlers = [generic, command]
    if command_index == 0:
        handlers.reverse()
    event._extras["activated_handlers"] = handlers

    assert gate().classify(event) is HostEventDisposition.HOST_COMMAND


def test_unknown_configured_prefix_stays_with_astrbot():
    event = FakeEvent(text="未知命令", raw_text="!未知命令")

    assert gate(("!",)).classify(event) is HostEventDisposition.HOST_WAKE_PREFIX


def test_raw_prefix_survives_astrbot_message_stripping_and_bot_at():
    event = FakeEvent(text="取名", raw_text=" /取名", at_bot=True)

    assert gate().classify(event) is HostEventDisposition.HOST_WAKE_PREFIX


def test_native_bot_at_without_prefix_enters_groupmate():
    event = FakeEvent(text="你今天怎样", at_bot=True)

    assert gate().classify(event) is HostEventDisposition.GROUPMATE_MESSAGE


def test_wake_event_without_raw_direct_evidence_stays_with_astrbot():
    event = FakeEvent(text="help")
    event.is_at_or_wake_command = True
    event.message_obj.raw_message = None

    assert gate().classify(event) is HostEventDisposition.HOST_WAKE_PREFIX


def test_wake_flagged_poke_targeting_bot_enters_groupmate():
    """Empty wake_prefix makes AstrBot set is_at_or_wake_command on poke notices."""

    class Poke:
        type = "Poke"

        def __init__(self, target):
            self.id = target
            self.qq = 0

        def target_id(self):
            return str(self.id)

    event = FakeEvent(text="")
    event.is_at_or_wake_command = True
    event.message_obj.message = [Poke("bot")]
    event.message_obj.raw_message = {
        "post_type": "notice",
        "notice_type": "notify",
        "sub_type": "poke",
        "target_id": "bot",
        "user_id": "u1",
        "group_id": "g1",
        "self_id": "bot",
        "message": [],
    }

    assert gate().classify(event) is HostEventDisposition.GROUPMATE_MESSAGE


def test_wake_flagged_poke_targeting_other_stays_with_astrbot():
    event = FakeEvent(text="")
    event.is_at_or_wake_command = True
    event.message_obj.raw_message = {
        "post_type": "notice",
        "notice_type": "notify",
        "sub_type": "poke",
        "target_id": "u2",
        "user_id": "u1",
        "group_id": "g1",
        "self_id": "bot",
        "message": [],
    }

    assert gate().classify(event) is HostEventDisposition.HOST_WAKE_PREFIX


def test_ordinary_group_message_enters_groupmate():
    assert gate().classify(FakeEvent()) is HostEventDisposition.GROUPMATE_MESSAGE


@pytest.mark.parametrize(
    "event",
    [
        FakeEvent(group_id=""),
        FakeEvent(sender_id="bot"),
        FakeEvent(stopped=True),
        FakeEvent(group_id="g2"),
    ],
)
def test_ignored_events_never_enter_groupmate(event):
    assert gate(enabled_groups=("g1",)).classify(event) is HostEventDisposition.IGNORE


def test_bridge_no_longer_owns_host_command_reflection():
    from groupmate.host.bridge import AstrBotBridge

    assert not hasattr(AstrBotBridge, "_is_" + "command_event")
