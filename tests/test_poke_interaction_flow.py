"""End-to-end ownership regression for host poke interactions."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from groupmate.host.bridge import AstrBotBridge, TurnOwner
from groupmate.host.event_adapters import (
    HostEventAdapter,
    HostEventAdapterManifest,
    HostEventAdapterResult,
    HostEventAdapterRuntime,
    PokeEventAdapter,
)
from groupmate.host.event_gate import HostEventDisposition, HostEventGate
from groupmate.host.ingress import AstrBotEventIngress
from groupmate.models import MessageOrigin


class CommandFilter:
    pass


class Poke:
    type = "poke"

    def __init__(self, target_id):
        self.qq = target_id


class Event:
    def __init__(self, *, text="", poke_target=None, command=False):
        components = [] if poke_target is None else [Poke(poke_target)]
        segments = []
        if text:
            segments.append({"type": "text", "data": {"text": text}})
        if poke_target is not None:
            segments.append(
                {"type": "poke", "data": {"qq": str(poke_target)}}
            )
        handlers = (
            [SimpleNamespace(event_filters=[CommandFilter()])]
            if command
            else []
        )
        self.message_str = text
        self.message_obj = SimpleNamespace(
            message_id="notice-1",
            timestamp=100,
            message=components,
            raw_message={
                "message_id": "notice-1",
                "group_id": "g1",
                "user_id": "u1",
                "target_id": poke_target,
                "time": 100,
                "sender": {"nickname": "Alice"},
                "message": segments,
            },
        )
        self.unified_msg_origin = "aiocqhttp:GroupMessage:g1"
        self.is_at_or_wake_command = False
        self.call_llm = False
        self.call_llm_values = []
        self.stop_calls = 0
        self._extras = {"activated_handlers": handlers}

    def get_group_id(self):
        return "g1"

    def get_sender_id(self):
        return "u1"

    def get_sender_name(self):
        return "Alice"

    def get_self_id(self):
        return "bot"

    def get_extra(self, key=None, default=None):
        return self._extras.get(key, default)

    def is_stopped(self):
        return False

    def should_call_llm(self, value):
        self.call_llm_values.append(bool(value))
        self.call_llm = bool(value)

    def stop_event(self):
        self.stop_calls += 1


class RecordingActor:
    def __init__(self):
        self.submissions = []
        self.sent = []

    async def submit(self, message, schedule=True):
        self.submissions.append((message, bool(schedule)))


class RecordingBridge:
    def __init__(self, *, paused=False):
        self.paused = bool(paused)
        self.actor = RecordingActor()
        self.calls = []

    async def handle_adapted_event(self, event, message):
        self.calls.append(("adapted", event, message))
        AstrBotBridge._mark_groupmate_owner(event)
        await self.actor.submit(message, schedule=not self.paused)
        return True

    def apply_owner_to_event(self, event):
        self.calls.append(("owner", event))
        AstrBotBridge._mark_groupmate_owner(event)
        return TurnOwner.GROUPMATE

    async def handle_event(self, event):
        self.calls.append(("handle", event))

    async def observe_only(self, event):
        self.calls.append(("observe", event))


class RecordingPokeEventAdapter(PokeEventAdapter):
    def __init__(self, enabled):
        self.calls = []
        super().__init__(enabled=enabled)

    def adapt(self, event):
        self.calls.append(event)
        return super().adapt(event)


class FailOnceAdapter(HostEventAdapter):
    manifest = HostEventAdapterManifest("fail-once", ("fault_once",))

    def __init__(self):
        self.calls = 0
        super().__init__()

    def adapt(self, event):
        del event
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("adapter unavailable")
        return HostEventAdapterResult.not_matched()


def ingress(adapter, bridge=None):
    gate = HostEventGate(config_resolver=lambda umo: {"wake_prefix": ["/"]})
    return AstrBotEventIngress(
        gate,
        bridge or RecordingBridge(),
        event_adapters=HostEventAdapterRuntime((adapter,)),
    )


def test_host_command_precedes_poke_adapter_without_event_mutation():
    adapter = RecordingPokeEventAdapter(enabled=True)
    bridge = RecordingBridge()
    event = Event(text="/取名 小明", poke_target="bot", command=True)

    disposition = asyncio.run(ingress(adapter, bridge).handle_group_message(event))

    assert disposition is HostEventDisposition.HOST_COMMAND
    assert adapter.calls == []
    assert bridge.calls == []
    assert event.call_llm is False
    assert event.call_llm_values == []
    assert event.stop_calls == 0


def test_disabled_or_other_target_poke_bypasses_without_storage():
    disabled = RecordingPokeEventAdapter(enabled=False)
    disabled_bridge = RecordingBridge()
    disabled_event = Event(poke_target="bot")
    other = RecordingPokeEventAdapter(enabled=True)
    other_bridge = RecordingBridge()
    other_event = Event(poke_target="u2")

    disabled_result = asyncio.run(
        ingress(disabled, disabled_bridge).handle_group_message(disabled_event)
    )
    other_result = asyncio.run(
        ingress(other, other_bridge).handle_group_message(other_event)
    )

    assert disabled_result is HostEventDisposition.HOST_INTERACTION_BYPASS
    assert other_result is HostEventDisposition.HOST_INTERACTION_BYPASS
    assert disabled_bridge.actor.submissions == []
    assert other_bridge.actor.submissions == []
    assert disabled_event.call_llm_values == []
    assert other_event.call_llm_values == []
    assert disabled_event.stop_calls == 0
    assert other_event.stop_calls == 0


def test_enabled_bot_poke_submits_one_synthetic_owned_message():
    adapter = RecordingPokeEventAdapter(enabled=True)
    bridge = RecordingBridge()
    event = Event(poke_target="bot")

    disposition = asyncio.run(ingress(adapter, bridge).handle_group_message(event))

    assert disposition is HostEventDisposition.GROUPMATE_INTERACTION
    assert len(bridge.actor.submissions) == 1
    message, schedule = bridge.actor.submissions[0]
    assert message.origin is MessageOrigin.SYSTEM_SYNTHETIC
    assert message.segment_types == ("poke",)
    assert schedule is True
    assert event.call_llm_values == [True]
    assert event.stop_calls == 0


def test_normal_owner_path_recovers_after_one_adapter_exception():
    failure = FailOnceAdapter()
    poke = PokeEventAdapter(enabled=True)
    bridge = RecordingBridge()
    flow = AstrBotEventIngress(
        HostEventGate(),
        bridge,
        event_adapters=HostEventAdapterRuntime((failure, poke)),
    )
    failed_event = Event(text="第一条普通消息")
    following_event = Event(text="第二条普通消息")

    failed = asyncio.run(flow.handle_group_message(failed_event))
    following = asyncio.run(flow.handle_group_message(following_event))

    assert failed is HostEventDisposition.HOST_INTERACTION_BYPASS
    assert following is HostEventDisposition.GROUPMATE_MESSAGE
    assert bridge.calls == [
        ("owner", following_event),
        ("handle", following_event),
    ]
    assert following_event.call_llm_values == [True]
    assert failed_event.stop_calls == 0
    assert following_event.stop_calls == 0


def test_paused_poke_observes_without_schedule_or_send():
    adapter = RecordingPokeEventAdapter(enabled=True)
    bridge = RecordingBridge(paused=True)
    event = Event(poke_target="bot")

    disposition = asyncio.run(ingress(adapter, bridge).handle_group_message(event))

    assert disposition is HostEventDisposition.GROUPMATE_INTERACTION
    assert len(bridge.actor.submissions) == 1
    _, schedule = bridge.actor.submissions[0]
    assert schedule is False
    assert bridge.actor.sent == []
    assert event.call_llm_values == [True]
    assert event.stop_calls == 0
