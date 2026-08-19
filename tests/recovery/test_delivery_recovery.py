from __future__ import annotations

from groupmate.social_runtime.actions.contracts import (
    DeliveryBundle,
    DeliveryPart,
    DeliveryPartKind,
    DeliveryReceipt,
    DeliveryReceiptStatus,
    OutboxStatus,
)
from groupmate.social_runtime.delivery.outbox import OutboxService


def _bundle():
    parts = tuple(
        DeliveryPart.create(
            part_id=f"part-{index}",
            kind=DeliveryPartKind.TEXT,
            payload={"text": str(index)},
            order=index,
            idempotency_key=f"key-{index}",
            expires_at=300,
        )
        for index in range(3)
    )
    return DeliveryBundle.create(
        bundle_id="bundle-1",
        correlation_id="corr-1",
        persona_id="persona-1",
        group_id="group-1",
        topic_id="topic-1",
        parts=parts,
        created_at=100,
        expires_at=300,
    )


def test_restart_preserves_sent_part_and_marks_unconfirmed_send_unknown(tmp_path):
    path = tmp_path / "social-runtime.db"
    service = OutboxService(path)
    service.commit_bundle(_bundle())
    first = service.claim_ready(now=101)[0]
    service.record_receipt(
        DeliveryReceipt.create(
            receipt_id="receipt-first",
            part_id=first.part_id,
            status=DeliveryReceiptStatus.SUCCESS,
            occurred_at=102,
            platform_message_id="qq-first",
        )
    )
    second = service.claim_ready(now=103)[0]
    assert second.status is OutboxStatus.SENDING

    restarted = OutboxService(path)
    recovered = restarted.recover_inflight(now=110)

    assert [part.part_id for part in recovered] == ["part-1"]
    assert restarted.outbox("part-0").status is OutboxStatus.SENT
    assert restarted.outbox("part-1").status is OutboxStatus.UNKNOWN
    assert restarted.outbox("part-2").status is OutboxStatus.READY
    assert restarted.claim_ready(now=120) == ()
    assert restarted.bot_ledger("part-0").platform_message_id == "qq-first"
