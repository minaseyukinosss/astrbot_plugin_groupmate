from __future__ import annotations

import asyncio

from groupmate.adapters.onebot_delivery import OneBotDeliveryAdapter
from groupmate.social_runtime.actions.contracts import (
    DeliveryBundle,
    DeliveryPart,
    DeliveryPartKind,
    DeliveryReceipt,
    DeliveryReceiptStatus,
    OutboxStatus,
)
from groupmate.social_runtime.delivery.dispatcher import DeliveryDispatcher
from groupmate.social_runtime.delivery.outbox import OutboxService


def _bundle(kind=DeliveryPartKind.TEXT, payload=None):
    part = DeliveryPart.create(
        part_id="part-1",
        kind=kind,
        payload=payload or {"text": "你好"},
        order=0,
        idempotency_key="delivery-key-1",
        expires_at=200,
    )
    return DeliveryBundle.create(
        bundle_id="bundle-1",
        correlation_id="corr-1",
        persona_id="persona-1",
        group_id="group-1",
        topic_id="topic-1",
        parts=(part,),
        created_at=100,
        expires_at=200,
    )


class _Transport:
    def __init__(self, service, status=DeliveryReceiptStatus.SUCCESS):
        self.service = service
        self.status = status
        self.seen_status = None

    async def send(self, part):
        self.seen_status = self.service.outbox(part.part_id).status
        return DeliveryReceipt.create(
            receipt_id="receipt-1",
            part_id=part.part_id,
            status=self.status,
            occurred_at=110,
            platform_message_id=(
                "qq-message-1"
                if self.status is DeliveryReceiptStatus.SUCCESS
                else None
            ),
            error_code=(
                None if self.status is DeliveryReceiptStatus.SUCCESS else "send-error"
            ),
        )


def test_dispatcher_calls_platform_only_after_sending_intent_is_persisted(tmp_path):
    service = OutboxService(tmp_path / "social-runtime.db")
    service.commit_bundle(_bundle())
    part = service.claim_ready(now=105)[0]
    transport = _Transport(service)

    updated = asyncio.run(DeliveryDispatcher(service, transport).dispatch(part))

    assert transport.seen_status is OutboxStatus.SENDING
    assert updated.status is OutboxStatus.SENT
    ledger = service.bot_ledger("part-1")
    assert ledger.correlation_id == "corr-1"
    assert ledger.platform_message_id == "qq-message-1"


def test_dispatcher_keeps_unknown_result_non_retryable(tmp_path):
    service = OutboxService(tmp_path / "social-runtime.db")
    service.commit_bundle(_bundle())
    part = service.claim_ready(now=105)[0]

    updated = asyncio.run(
        DeliveryDispatcher(
            service,
            _Transport(service, DeliveryReceiptStatus.UNKNOWN),
        ).dispatch(part)
    )

    assert updated.status is OutboxStatus.UNKNOWN
    assert service.claim_ready(now=120) == ()


def test_onebot_adapter_translates_structured_part_without_free_text_parsing():
    calls = []

    async def send_group_message(*, group_id, segments, idempotency_key):
        calls.append((group_id, segments, idempotency_key))
        return {"message_id": "qq-message-8"}

    adapter = OneBotDeliveryAdapter(send_group_message)
    bundle = _bundle(
        kind=DeliveryPartKind.MENTION,
        payload={"target_id": "user-8"},
    )
    outbox_part = OutboxService.in_memory_part(bundle, bundle.parts[0], OutboxStatus.SENDING)

    receipt = asyncio.run(adapter.send(outbox_part))

    assert receipt.status is DeliveryReceiptStatus.SUCCESS
    assert calls == [
        (
            "group-1",
            [{"type": "at", "data": {"qq": "user-8"}}],
            "delivery-key-1",
        )
    ]
