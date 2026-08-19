"""Dispatcher that can send only already-claimed Outbox parts."""

from __future__ import annotations

from typing import Awaitable, Callable, Protocol

from ..actions.contracts import DeliveryReceipt, OutboxPart, OutboxStatus
from .outbox import OutboxService, OutboxStateConflict


class DeliveryTransport(Protocol):
    async def send(self, part: OutboxPart) -> DeliveryReceipt: ...


class DeliveryDispatcher:
    def __init__(
        self,
        outbox: OutboxService,
        transport: DeliveryTransport,
        *,
        receipt_handler: Callable[[DeliveryReceipt], Awaitable[OutboxPart]] | None = None,
    ) -> None:
        self.outbox = outbox
        self.transport = transport
        self.receipt_handler = receipt_handler

    async def dispatch(self, part: OutboxPart) -> OutboxPart:
        durable = self.outbox.outbox(part.part_id)
        if durable != part or durable.status is not OutboxStatus.SENDING:
            raise OutboxStateConflict(
                "dispatcher requires the exact durable sending Outbox part"
            )
        receipt = await self.transport.send(durable)
        if not isinstance(receipt, DeliveryReceipt):
            raise OutboxStateConflict("delivery transport must return DeliveryReceipt")
        if receipt.part_id != durable.part_id:
            raise OutboxStateConflict("delivery receipt belongs to another part")
        if self.receipt_handler is None:
            return self.outbox.record_receipt(receipt)
        handled = await self.receipt_handler(receipt)
        if (
            not isinstance(handled, OutboxPart)
            or handled.part_id != durable.part_id
            or handled.receipt != receipt
        ):
            raise OutboxStateConflict(
                "receipt handler must persist and return the matching Outbox part"
            )
        return handled

    async def dispatch_next(self, *, now: int) -> OutboxPart | None:
        claimed = self.outbox.claim_ready(now=now)
        if not claimed:
            return None
        return await self.dispatch(claimed[0])


__all__ = ("DeliveryDispatcher", "DeliveryTransport")
