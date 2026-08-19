"""Narrow OneBot delivery adapter for validated Outbox parts."""

from __future__ import annotations

import time
from typing import Awaitable, Callable, Mapping

from ..social_runtime.actions.contracts import (
    DeliveryPartKind,
    DeliveryReceipt,
    DeliveryReceiptStatus,
    OutboxPart,
    OutboxStatus,
)


class RetryableOneBotError(RuntimeError):
    """A confirmed non-send that policy may safely retry."""


class PermanentOneBotError(RuntimeError):
    """A confirmed permanent platform rejection."""


class OneBotDeliveryAdapter:
    def __init__(
        self,
        send_group_message: Callable[..., Awaitable[Mapping[str, object]]],
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not callable(send_group_message):
            raise ValueError("send_group_message must be callable")
        self._send_group_message = send_group_message
        self._clock = clock

    async def send(self, part: OutboxPart) -> DeliveryReceipt:
        if part.status is not OutboxStatus.SENDING:
            raise ValueError("OneBot adapter accepts only sending Outbox parts")
        occurred_at = int(self._clock())
        try:
            response = await self._send_group_message(
                group_id=part.group_id,
                segments=[self._segment(part)],
                idempotency_key=part.idempotency_key,
            )
        except RetryableOneBotError as exc:
            return self._failure(
                part,
                DeliveryReceiptStatus.RETRYABLE_FAILURE,
                occurred_at,
                str(exc) or "onebot_retryable_failure",
            )
        except PermanentOneBotError as exc:
            return self._failure(
                part,
                DeliveryReceiptStatus.PERMANENT_FAILURE,
                occurred_at,
                str(exc) or "onebot_permanent_failure",
            )
        except Exception:
            # The call may have reached the platform; no blind retry is safe.
            return self._failure(
                part,
                DeliveryReceiptStatus.UNKNOWN,
                occurred_at,
                "onebot_receipt_unknown",
            )
        if not isinstance(response, Mapping):
            return self._failure(
                part,
                DeliveryReceiptStatus.UNKNOWN,
                occurred_at,
                "onebot_unstructured_receipt",
            )
        message_id = str(response.get("message_id") or "").strip()
        if not message_id:
            return self._failure(
                part,
                DeliveryReceiptStatus.UNKNOWN,
                occurred_at,
                "onebot_missing_message_id",
            )
        return DeliveryReceipt.create(
            receipt_id=f"onebot:{part.part_id}:{message_id}",
            part_id=part.part_id,
            status=DeliveryReceiptStatus.SUCCESS,
            occurred_at=occurred_at,
            platform_message_id=message_id,
        )

    @staticmethod
    def _segment(part: OutboxPart) -> dict[str, object]:
        payload = part.part.payload
        kind = part.part.kind
        if kind is DeliveryPartKind.TEXT:
            return {"type": "text", "data": {"text": str(payload["text"])}}
        if kind is DeliveryPartKind.MENTION:
            return {"type": "at", "data": {"qq": str(payload["target_id"])}}
        if kind is DeliveryPartKind.FACE:
            return {"type": "face", "data": {"id": str(payload["face_id"])}}
        if kind in {
            DeliveryPartKind.IMAGE,
            DeliveryPartKind.AUDIO,
            DeliveryPartKind.VIDEO,
            DeliveryPartKind.FILE,
        }:
            onebot_kind = "record" if kind is DeliveryPartKind.AUDIO else kind.value
            return {
                "type": onebot_kind,
                "data": {"file": str(payload["media_ref"])},
            }
        if kind is DeliveryPartKind.FORWARD:
            return {"type": "forward", "data": {"nodes": payload["nodes"]}}
        return {"type": "poke", "data": {"qq": str(payload["target_id"])}}

    @staticmethod
    def _failure(
        part: OutboxPart,
        status: DeliveryReceiptStatus,
        occurred_at: int,
        error_code: str,
    ) -> DeliveryReceipt:
        return DeliveryReceipt.create(
            receipt_id=f"onebot:{part.part_id}:{status.value}:{occurred_at}",
            part_id=part.part_id,
            status=status,
            occurred_at=occurred_at,
            error_code=error_code,
        )


__all__ = (
    "OneBotDeliveryAdapter",
    "PermanentOneBotError",
    "RetryableOneBotError",
)
