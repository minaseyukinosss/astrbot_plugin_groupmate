from __future__ import annotations

import pytest

from groupmate.social_runtime.actions.contracts import (
    DeliveryBundle,
    DeliveryPart,
    DeliveryPartKind,
    DeliveryReceipt,
    DeliveryReceiptStatus,
    OutboxStatus,
)
from groupmate.social_runtime.delivery.outbox import (
    BundleIdentityConflict,
    OutboxService,
    UnsupportedDeliveryPart,
)


def _part(part_id, order, *, kind=DeliveryPartKind.TEXT, expires_at=200, **overrides):
    values = {
        "part_id": part_id,
        "kind": kind,
        "payload": {"text": f"message-{order}"},
        "order": order,
        "idempotency_key": f"key-{part_id}",
        "expires_at": expires_at,
        "decorative": False,
        "task_id": None,
        "role": "content",
    }
    values.update(overrides)
    return DeliveryPart.create(**values)


def _bundle(*parts, **overrides):
    values = {
        "bundle_id": "bundle-1",
        "correlation_id": "corr-1",
        "persona_id": "persona-1",
        "group_id": "group-1",
        "topic_id": "topic-1",
        "parts": parts or (_part("part-1", 0),),
        "created_at": 100,
        "expires_at": 200,
    }
    values.update(overrides)
    return DeliveryBundle.create(**values)


def _success(part_id, *, event_id="receipt-1", occurred_at=110):
    return DeliveryReceipt.create(
        receipt_id=event_id,
        part_id=part_id,
        status=DeliveryReceiptStatus.SUCCESS,
        occurred_at=occurred_at,
        platform_message_id=f"qq-{part_id}",
    )


def test_commit_and_claim_preserve_order_and_persist_sending_before_return(tmp_path):
    service = OutboxService(tmp_path / "social-runtime.db")
    service.commit_bundle(_bundle(_part("part-2", 1), _part("part-1", 0)))

    claimed = service.claim_ready(now=105, limit=10)

    assert [part.part_id for part in claimed] == ["part-1"]
    assert service.outbox("part-1").status is OutboxStatus.SENDING
    assert service.outbox("part-2").status is OutboxStatus.READY
    service.record_receipt(_success("part-1"))
    assert [part.part_id for part in service.claim_ready(now=111)] == ["part-2"]


def test_bundle_commit_is_idempotent_but_rejects_reused_identity(tmp_path):
    service = OutboxService(tmp_path / "social-runtime.db")
    original = _bundle()

    assert service.commit_bundle(original) == original
    assert service.commit_bundle(original) == original
    with pytest.raises(BundleIdentityConflict, match="bundle"):
        service.commit_bundle(
            _bundle(parts=(_part("different", 0),))
        )


def test_platform_unsupported_part_kind_is_rejected_before_outbox_write(tmp_path):
    service = OutboxService(
        tmp_path / "social-runtime.db",
        supported_part_kinds=(DeliveryPartKind.TEXT,),
    )
    bundle = _bundle(
        _part(
            "poke-1",
            0,
            kind=DeliveryPartKind.POKE,
            payload={"target_id": "user-1"},
        )
    )

    with pytest.raises(UnsupportedDeliveryPart, match="poke"):
        service.commit_bundle(bundle)
    assert service.count() == 0


def test_expired_decorative_part_is_suppressed_and_essential_part_expires(tmp_path):
    service = OutboxService(tmp_path / "social-runtime.db")
    service.commit_bundle(
        _bundle(
            _part("decorative", 0, expires_at=105, decorative=True),
            _part("essential", 1, expires_at=105),
            expires_at=105,
        )
    )

    assert service.claim_ready(now=105, limit=10) == ()
    assert service.outbox("decorative").status is OutboxStatus.SUPPRESSED
    assert service.outbox("essential").status is OutboxStatus.EXPIRED


def test_task_result_suppresses_only_unsent_progress_parts(tmp_path):
    service = OutboxService(tmp_path / "social-runtime.db")
    service.commit_bundle(
        _bundle(
            _part(
                "progress-1",
                0,
                task_id="task-1",
                role="progress",
                decorative=True,
            )
        )
    )
    service.commit_bundle(
        _bundle(
            _part("result-1", 0, task_id="task-1", role="result"),
            bundle_id="bundle-result",
            correlation_id="corr-result",
        )
    )

    assert service.outbox("progress-1").status is OutboxStatus.SUPPRESSED
    assert service.outbox("result-1").status is OutboxStatus.READY


@pytest.mark.parametrize(
    ("receipt_status", "expected"),
    (
        (DeliveryReceiptStatus.RETRYABLE_FAILURE, OutboxStatus.READY),
        (DeliveryReceiptStatus.PERMANENT_FAILURE, OutboxStatus.FAILED),
        (DeliveryReceiptStatus.UNKNOWN, OutboxStatus.UNKNOWN),
    ),
)
def test_receipt_status_controls_safe_retry(receipt_status, expected, tmp_path):
    service = OutboxService(tmp_path / f"{receipt_status.value}.db")
    service.commit_bundle(_bundle())
    service.claim_ready(now=101)

    updated = service.record_receipt(
        DeliveryReceipt.create(
            receipt_id="receipt-1",
            part_id="part-1",
            status=receipt_status,
            occurred_at=102,
            error_code="platform-error",
        )
    )

    assert updated.status is expected
