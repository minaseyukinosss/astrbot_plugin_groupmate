"""Single AstrBot event ingress for Groupmate host traffic."""

from __future__ import annotations

from typing import Any

from .bridge import TurnOwner
from .event_adapters import (
    HostEventAdapterRuntime,
    HostEventAdapterStatus,
)
from .event_gate import HostEventDisposition, HostEventGate


class AstrBotEventIngress:
    def __init__(
        self,
        gate: HostEventGate,
        bridge: Any,
        event_adapters: HostEventAdapterRuntime = None,
    ) -> None:
        self.gate = gate
        self.bridge = bridge
        self.event_adapters = event_adapters or HostEventAdapterRuntime()

    async def handle_group_message(
        self,
        event: Any,
    ) -> HostEventDisposition:
        disposition = self.gate.classify(event)
        if disposition is not HostEventDisposition.GROUPMATE_MESSAGE:
            return disposition
        adapted = self.event_adapters.adapt(event)
        if adapted.status is HostEventAdapterStatus.BYPASSED:
            return HostEventDisposition.HOST_INTERACTION_BYPASS
        if adapted.status is HostEventAdapterStatus.ADMITTED:
            await self.bridge.handle_adapted_event(event, adapted.message)
            return HostEventDisposition.GROUPMATE_INTERACTION
        owner = self.bridge.apply_owner_to_event(event)
        if owner is TurnOwner.ASTRBOT_AGENT:
            await self.bridge.observe_only(event)
        else:
            await self.bridge.handle_event(event)
        return disposition

    async def enrich_request(
        self,
        event: Any,
        req: Any,
    ) -> HostEventDisposition:
        disposition = self.gate.classify(event)
        if disposition is not HostEventDisposition.GROUPMATE_MESSAGE:
            return disposition
        await self.bridge.enrich_request(event, req)
        return disposition
