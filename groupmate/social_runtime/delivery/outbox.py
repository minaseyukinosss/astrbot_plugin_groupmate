"""Transactional Outbox for ordered, idempotent social delivery."""

from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path
from typing import Callable, Iterable, Mapping

from ..actions.contracts import (
    BotLedgerEntry,
    DeliveryBundle,
    DeliveryPart,
    DeliveryPartKind,
    DeliveryReceipt,
    DeliveryReceiptStatus,
    OutboxPart,
    OutboxStatus,
)
from ..persistence.schema import connect_database, initialize_database


class BundleIdentityConflict(RuntimeError):
    """Raised when a durable bundle/part identity is reused for new content."""


class UnsupportedDeliveryPart(ValueError):
    """Raised before an unsupported platform part enters Outbox."""


class OutboxAuthorizationError(PermissionError):
    """Raised when the composition release gate forbids a bundle's group."""


class OutboxStateConflict(RuntimeError):
    """Raised when receipt/state ordering would become ambiguous."""


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _part_to_dict(part: DeliveryPart) -> dict[str, object]:
    return {
        "part_id": part.part_id,
        "kind": part.kind.value,
        "payload": dict(part.payload),
        "order": part.order,
        "idempotency_key": part.idempotency_key,
        "expires_at": part.expires_at,
        "decorative": part.decorative,
        "task_id": part.task_id,
        "role": part.role,
    }


def _part_from_dict(value: Mapping[str, object]) -> DeliveryPart:
    return DeliveryPart.create(
        part_id=value["part_id"],
        kind=DeliveryPartKind(value["kind"]),
        payload=value["payload"],
        order=value["order"],
        idempotency_key=value["idempotency_key"],
        expires_at=value["expires_at"],
        decorative=value.get("decorative", False),
        task_id=value.get("task_id"),
        role=value.get("role", "content"),
    )


def _bundle_to_dict(bundle: DeliveryBundle) -> dict[str, object]:
    return {
        "bundle_id": bundle.bundle_id,
        "correlation_id": bundle.correlation_id,
        "persona_id": bundle.persona_id,
        "group_id": bundle.group_id,
        "topic_id": bundle.topic_id,
        "parts": [_part_to_dict(item) for item in bundle.parts],
        "created_at": bundle.created_at,
        "expires_at": bundle.expires_at,
    }


def _bundle_from_dict(value: Mapping[str, object]) -> DeliveryBundle:
    return DeliveryBundle.create(
        bundle_id=value["bundle_id"],
        correlation_id=value["correlation_id"],
        persona_id=value["persona_id"],
        group_id=value["group_id"],
        topic_id=value.get("topic_id"),
        parts=tuple(_part_from_dict(item) for item in value["parts"]),
        created_at=value["created_at"],
        expires_at=value["expires_at"],
    )


def _receipt_to_dict(receipt: DeliveryReceipt) -> dict[str, object]:
    return {
        "receipt_id": receipt.receipt_id,
        "part_id": receipt.part_id,
        "status": receipt.status.value,
        "occurred_at": receipt.occurred_at,
        "platform_message_id": receipt.platform_message_id,
        "error_code": receipt.error_code,
    }


def _receipt_from_dict(value: Mapping[str, object]) -> DeliveryReceipt:
    return DeliveryReceipt.create(
        receipt_id=value["receipt_id"],
        part_id=value["part_id"],
        status=DeliveryReceiptStatus(value["status"]),
        occurred_at=value["occurred_at"],
        platform_message_id=value.get("platform_message_id"),
        error_code=value.get("error_code"),
    )


class OutboxService:
    def __init__(
        self,
        path: Path,
        *,
        supported_part_kinds: Iterable[DeliveryPartKind] = tuple(DeliveryPartKind),
        group_authorizer: Callable[[str], bool] | None = None,
        bundle_authorizer: Callable[[DeliveryBundle], bool] | None = None,
    ) -> None:
        self.path = Path(path)
        initialize_database(self.path)
        self.supported_part_kinds = frozenset(
            DeliveryPartKind(item) for item in supported_part_kinds
        )
        self._group_authorizer = group_authorizer
        self._bundle_authorizer = bundle_authorizer

    def commit_bundle(self, bundle: DeliveryBundle) -> DeliveryBundle:
        if not isinstance(bundle, DeliveryBundle):
            raise ValueError("bundle must be a DeliveryBundle")
        # Recreate at the authority boundary so direct dataclass construction cannot bypass it.
        bundle = _bundle_from_dict(_bundle_to_dict(bundle))
        self._require_authorized(bundle)
        for part in bundle.parts:
            if part.kind not in self.supported_part_kinds:
                raise UnsupportedDeliveryPart(
                    f"platform does not support delivery kind: {part.kind.value}"
                )
        encoded_bundle = _canonical_json(_bundle_to_dict(bundle))
        with closing(connect_database(self.path)) as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT bundle_json FROM delivery_bundles WHERE bundle_id=?",
                    (bundle.bundle_id,),
                ).fetchone()
                if row is not None:
                    if row["bundle_json"] != encoded_bundle:
                        raise BundleIdentityConflict(
                            f"bundle identity was reused: {bundle.bundle_id}"
                        )
                    db.commit()
                    return _bundle_from_dict(json.loads(row["bundle_json"]))

                self._suppress_replaced_progress(db, bundle)
                db.execute(
                    "INSERT INTO delivery_bundles(bundle_id, correlation_id, persona_id, "
                    "group_id, status, bundle_json, expires_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
                    (
                        bundle.bundle_id,
                        bundle.correlation_id,
                        bundle.persona_id,
                        bundle.group_id,
                        OutboxStatus.READY.value,
                        encoded_bundle,
                        bundle.expires_at,
                    ),
                )
                for part in bundle.parts:
                    db.execute(
                        "INSERT INTO outbox(part_id, bundle_id, persona_id, group_id, "
                        "idempotency_key, status, payload_json, expires_at) "
                        "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            part.part_id,
                            bundle.bundle_id,
                            bundle.persona_id,
                            bundle.group_id,
                            part.idempotency_key,
                            OutboxStatus.READY.value,
                            _canonical_json(_part_to_dict(part)),
                            part.expires_at,
                        ),
                    )
                db.commit()
                return bundle
            except BundleIdentityConflict:
                db.rollback()
                raise
            except Exception as exc:
                db.rollback()
                if "UNIQUE constraint failed" in str(exc):
                    raise BundleIdentityConflict(
                        "delivery part identity or idempotency key was reused"
                    ) from exc
                raise

    def claim_ready(self, *, now: int, limit: int = 1) -> tuple[OutboxPart, ...]:
        if limit < 1:
            raise ValueError("claim limit must be positive")
        with closing(connect_database(self.path)) as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                self._expire_ready(db, now)
                rows = db.execute(
                    "SELECT outbox.*, delivery_bundles.bundle_json "
                    "FROM outbox JOIN delivery_bundles USING(bundle_id) "
                    "WHERE outbox.status='ready' ORDER BY delivery_bundles.rowid, outbox.rowid"
                ).fetchall()
                claimed = []
                for row in rows:
                    if len(claimed) >= limit:
                        break
                    bundle = _bundle_from_dict(json.loads(row["bundle_json"]))
                    if not self._is_authorized(bundle):
                        continue
                    part = _part_from_dict(json.loads(row["payload_json"]))
                    siblings = db.execute(
                        "SELECT status, payload_json FROM outbox WHERE bundle_id=?",
                        (row["bundle_id"],),
                    ).fetchall()
                    earlier = [
                        (OutboxStatus(item["status"]), _part_from_dict(json.loads(item["payload_json"])))
                        for item in siblings
                        if _part_from_dict(json.loads(item["payload_json"])).order < part.order
                    ]
                    allowed = {
                        OutboxStatus.SENT,
                        OutboxStatus.SUPPRESSED,
                        OutboxStatus.EXPIRED,
                    }
                    if any(status not in allowed for status, _ in earlier):
                        continue
                    changed = db.execute(
                        "UPDATE outbox SET status='sending' "
                        "WHERE part_id=? AND status='ready'",
                        (part.part_id,),
                    ).rowcount
                    if changed == 1:
                        claimed.append(
                            self.in_memory_part(bundle, part, OutboxStatus.SENDING)
                        )
                db.commit()
                return tuple(claimed)
            except BaseException:
                db.rollback()
                raise

    def record_receipt(self, receipt: DeliveryReceipt) -> OutboxPart:
        if not isinstance(receipt, DeliveryReceipt):
            raise ValueError("receipt must be a DeliveryReceipt")
        receipt = _receipt_from_dict(_receipt_to_dict(receipt))
        encoded = _canonical_json(_receipt_to_dict(receipt))
        effect_id = f"delivery-receipt:{receipt.receipt_id}"
        with closing(connect_database(self.path)) as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                prior = db.execute(
                    "SELECT effect_json FROM journal WHERE effect_id=?", (effect_id,)
                ).fetchone()
                if prior is not None:
                    if prior["effect_json"] != encoded:
                        raise OutboxStateConflict(
                            f"receipt identity was reused: {receipt.receipt_id}"
                        )
                    db.commit()
                    return self.outbox(receipt.part_id)
                row = db.execute(
                    "SELECT outbox.status, delivery_bundles.correlation_id "
                    "FROM outbox JOIN delivery_bundles USING(bundle_id) "
                    "WHERE part_id=?",
                    (receipt.part_id,),
                ).fetchone()
                if row is None:
                    raise OutboxStateConflict(f"outbox part not found: {receipt.part_id}")
                if OutboxStatus(row["status"]) is not OutboxStatus.SENDING:
                    raise OutboxStateConflict(
                        f"receipt requires sending state, got {row['status']}"
                    )
                target = {
                    DeliveryReceiptStatus.SUCCESS: OutboxStatus.SENT,
                    DeliveryReceiptStatus.RETRYABLE_FAILURE: OutboxStatus.READY,
                    DeliveryReceiptStatus.PERMANENT_FAILURE: OutboxStatus.FAILED,
                    DeliveryReceiptStatus.UNKNOWN: OutboxStatus.UNKNOWN,
                }[receipt.status]
                db.execute(
                    "UPDATE outbox SET status=?, receipt_json=? WHERE part_id=?",
                    (target.value, encoded, receipt.part_id),
                )
                db.execute(
                    "INSERT INTO journal(effect_id, source_event_id, correlation_id, "
                    "causation_id, actor_key, effect_type, effect_json, committed_at) "
                    "VALUES(?, ?, ?, NULL, ?, ?, ?, ?)",
                    (
                        effect_id,
                        receipt.receipt_id,
                        row["correlation_id"],
                        f"delivery:{receipt.part_id}",
                        "delivery.receipt",
                        encoded,
                        receipt.occurred_at,
                    ),
                )
                db.commit()
            except BaseException:
                db.rollback()
                raise
        return self.outbox(receipt.part_id)

    def recover_inflight(self, *, now: int) -> tuple[OutboxPart, ...]:
        with closing(connect_database(self.path)) as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                rows = db.execute(
                    "SELECT part_id FROM outbox WHERE status='sending' ORDER BY rowid"
                ).fetchall()
                part_ids = [str(row["part_id"]) for row in rows]
                for part_id in part_ids:
                    receipt = DeliveryReceipt.create(
                        receipt_id=f"recovery-unknown:{part_id}:{now}",
                        part_id=part_id,
                        status=DeliveryReceiptStatus.UNKNOWN,
                        occurred_at=now,
                        error_code="send_interrupted_before_receipt",
                    )
                    db.execute(
                        "UPDATE outbox SET status='unknown', receipt_json=? WHERE part_id=?",
                        (_canonical_json(_receipt_to_dict(receipt)), part_id),
                    )
                self._expire_ready(db, now)
                db.commit()
            except BaseException:
                db.rollback()
                raise
        return tuple(self.outbox(part_id) for part_id in part_ids)

    def outbox(self, part_id: str) -> OutboxPart:
        with closing(connect_database(self.path)) as db:
            row = db.execute(
                "SELECT outbox.*, delivery_bundles.bundle_json "
                "FROM outbox JOIN delivery_bundles USING(bundle_id) WHERE part_id=?",
                (part_id,),
            ).fetchone()
        if row is None:
            raise LookupError(part_id)
        bundle = _bundle_from_dict(json.loads(row["bundle_json"]))
        receipt = (
            None
            if row["receipt_json"] is None
            else _receipt_from_dict(json.loads(row["receipt_json"]))
        )
        return self.in_memory_part(
            bundle,
            _part_from_dict(json.loads(row["payload_json"])),
            OutboxStatus(row["status"]),
            receipt,
        )

    def bot_ledger(self, part_id: str) -> BotLedgerEntry:
        part = self.outbox(part_id)
        if part.status is not OutboxStatus.SENT or part.receipt is None:
            raise LookupError(f"sent bot ledger entry not found: {part_id}")
        assert part.receipt.platform_message_id is not None
        return BotLedgerEntry(
            part_id=part.part_id,
            bundle_id=part.bundle_id,
            correlation_id=part.correlation_id,
            persona_id=part.persona_id,
            group_id=part.group_id,
            platform_message_id=part.receipt.platform_message_id,
            sent_at=part.receipt.occurred_at,
        )

    def count(self) -> int:
        with closing(connect_database(self.path)) as db:
            return int(db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0])

    def _require_authorized(self, bundle: DeliveryBundle) -> None:
        if self._group_authorizer is not None and not self._group_authorizer(
            bundle.group_id
        ):
            raise OutboxAuthorizationError(
                "Gate C forbids DeliveryBundle persistence for this group"
            )
        if self._bundle_authorizer is not None and not self._bundle_authorizer(bundle):
            raise OutboxAuthorizationError(
                "DeliveryBundle requires a matching validated ActionPlan"
            )

    def _is_authorized(self, bundle: DeliveryBundle) -> bool:
        return bool(
            (self._group_authorizer is None or self._group_authorizer(bundle.group_id))
            and (
                self._bundle_authorizer is None
                or self._bundle_authorizer(bundle)
            )
        )

    def receipted_parts(self) -> tuple[OutboxPart, ...]:
        with closing(connect_database(self.path)) as db:
            rows = db.execute(
                "SELECT part_id FROM outbox WHERE receipt_json IS NOT NULL ORDER BY rowid"
            ).fetchall()
        return tuple(self.outbox(str(row["part_id"])) for row in rows)

    @staticmethod
    def in_memory_part(
        bundle: DeliveryBundle,
        part: DeliveryPart,
        status: OutboxStatus,
        receipt: DeliveryReceipt | None = None,
    ) -> OutboxPart:
        return OutboxPart(
            bundle_id=bundle.bundle_id,
            correlation_id=bundle.correlation_id,
            persona_id=bundle.persona_id,
            group_id=bundle.group_id,
            topic_id=bundle.topic_id,
            part=part,
            status=status,
            receipt=receipt,
        )

    @staticmethod
    def _expire_ready(db, now: int) -> None:
        rows = db.execute(
            "SELECT part_id, payload_json FROM outbox "
            "WHERE status IN ('planned','ready') AND expires_at<=?",
            (now,),
        ).fetchall()
        for row in rows:
            part = _part_from_dict(json.loads(row["payload_json"]))
            target = (
                OutboxStatus.SUPPRESSED if part.decorative else OutboxStatus.EXPIRED
            )
            db.execute(
                "UPDATE outbox SET status=? WHERE part_id=?",
                (target.value, part.part_id),
            )

    @staticmethod
    def _suppress_replaced_progress(db, bundle: DeliveryBundle) -> None:
        result_task_ids = {
            part.task_id
            for part in bundle.parts
            if part.role == "result" and part.task_id is not None
        }
        if not result_task_ids:
            return
        rows = db.execute(
            "SELECT part_id, payload_json FROM outbox WHERE status IN ('planned','ready')"
        ).fetchall()
        for row in rows:
            part = _part_from_dict(json.loads(row["payload_json"]))
            if part.role == "progress" and part.task_id in result_task_ids:
                db.execute(
                    "UPDATE outbox SET status='suppressed' WHERE part_id=?",
                    (part.part_id,),
                )


__all__ = (
    "BundleIdentityConflict",
    "OutboxAuthorizationError",
    "OutboxService",
    "OutboxStateConflict",
    "UnsupportedDeliveryPart",
)
