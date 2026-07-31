import asyncio

from groupmate.host.bridge import TurnOwner
from groupmate.host.event_gate import HostEventDisposition
from groupmate.host.ingress import AstrBotEventIngress


class StaticGate:
    def __init__(self, disposition):
        self.disposition = disposition

    def classify(self, event):
        del event
        return self.disposition


class RecordingBridge:
    def __init__(self, owner=TurnOwner.OBSERVE_ONLY):
        self.owner = owner
        self.calls = []

    def apply_owner_to_event(self, event):
        self.calls.append(("owner", event))
        return self.owner

    async def handle_event(self, event):
        self.calls.append(("handle", event))

    async def observe_only(self, event):
        self.calls.append(("observe", event))

    async def enrich_request(self, event, req):
        self.calls.append(("enrich", event, req))


class Event:
    def __init__(self):
        self.stop_calls = 0

    def stop_event(self):
        self.stop_calls += 1


def test_host_command_returns_without_bridge_or_stop_event():
    event = Event()
    bridge = RecordingBridge()
    ingress = AstrBotEventIngress(
        StaticGate(HostEventDisposition.HOST_COMMAND),
        bridge,
    )

    disposition = asyncio.run(ingress.handle_group_message(event))

    assert disposition is HostEventDisposition.HOST_COMMAND
    assert bridge.calls == []
    assert event.stop_calls == 0


def test_host_prefix_skips_llm_enrichment():
    event = Event()
    request = object()
    bridge = RecordingBridge()
    ingress = AstrBotEventIngress(
        StaticGate(HostEventDisposition.HOST_WAKE_PREFIX),
        bridge,
    )

    disposition = asyncio.run(ingress.enrich_request(event, request))

    assert disposition is HostEventDisposition.HOST_WAKE_PREFIX
    assert bridge.calls == []


def test_groupmate_owner_uses_normal_bridge_path():
    event = Event()
    bridge = RecordingBridge(TurnOwner.GROUPMATE)
    ingress = AstrBotEventIngress(
        StaticGate(HostEventDisposition.GROUPMATE_MESSAGE),
        bridge,
    )

    asyncio.run(ingress.handle_group_message(event))

    assert bridge.calls == [("owner", event), ("handle", event)]


def test_astrbot_agent_owner_only_preloads_context():
    event = Event()
    bridge = RecordingBridge(TurnOwner.ASTRBOT_AGENT)
    ingress = AstrBotEventIngress(
        StaticGate(HostEventDisposition.GROUPMATE_MESSAGE),
        bridge,
    )

    asyncio.run(ingress.handle_group_message(event))

    assert bridge.calls == [("owner", event), ("observe", event)]


def test_admitted_request_reaches_bridge_enrichment():
    event = Event()
    request = object()
    bridge = RecordingBridge()
    ingress = AstrBotEventIngress(
        StaticGate(HostEventDisposition.GROUPMATE_MESSAGE),
        bridge,
    )

    asyncio.run(ingress.enrich_request(event, request))

    assert bridge.calls == [("enrich", event, request)]
