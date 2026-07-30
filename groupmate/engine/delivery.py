"""Humanized delivery planning: delay, segmentation, and expiry checks."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from ..models import (
    ChatMessage,
    MessageOrigin,
    OutboundKind,
    OutboundSegment,
    OutboxStatus,
    SendReceiptKind,
    SendResult,
    Urgency,
    WorkflowOutcome,
)


@dataclass(frozen=True)
class DeliveryPlan:
    decision_id: str
    group_id: str
    segments: Tuple[str, ...]
    delay_seconds: float
    expires_at: int
    quote_message_id: Optional[str] = None
    outbound: Tuple[OutboundSegment, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "segments", tuple(self.segments or ()))
        outbound = tuple(self.outbound or ())
        if not all(isinstance(item, OutboundSegment) for item in outbound):
            raise TypeError("delivery outbound must contain OutboundSegment values")
        object.__setattr__(self, "outbound", outbound)


_SENTENCE_END = re.compile(r"(?<=[。！？!?～~…])")


def split_reply_segments(
    text: str,
    max_chars: int = 60,
    max_segments: int = 2,
) -> Tuple[str, ...]:
    cleaned = (text or "").strip()
    if not cleaned:
        return ()
    max_chars = max(1, int(max_chars))
    max_segments = max(1, int(max_segments))
    if max_segments == 1:
        return (cleaned[:max_chars],)

    parts = [part.strip() for part in _SENTENCE_END.split(cleaned) if part.strip()]
    if len(parts) <= 1:
        return (cleaned[:max_chars],)

    segments: List[str] = []
    for part in parts:
        piece = part[:max_chars]
        if not piece:
            continue
        segments.append(piece)
        if len(segments) >= max_segments:
            break
    return tuple(segments) or (cleaned[:max_chars],)


def compute_delay_seconds(
    urgency: Urgency,
    text: str,
    *,
    enabled: bool = True,
    direct_wake: bool = False,
) -> float:
    if not enabled:
        return 0.0
    length = len((text or "").strip())
    if direct_wake or urgency is Urgency.HIGH:
        return min(0.35, 0.05 + length * 0.004)
    if urgency is Urgency.LOW:
        return min(1.5, 0.25 + length * 0.015)
    return min(0.9, 0.1 + length * 0.01)


def build_delivery_plan(
    *,
    decision_id: str,
    group_id: str,
    text: str,
    urgency: Urgency,
    now: int,
    ttl_seconds: int,
    max_chars: int,
    max_segments: int,
    humanize_delay: bool,
    direct_wake: bool,
    quote_message_id: Optional[str] = None,
) -> DeliveryPlan:
    segments = split_reply_segments(text, max_chars=max_chars, max_segments=max_segments)
    joined = "".join(segments)
    delay = compute_delay_seconds(
        urgency,
        joined,
        enabled=humanize_delay,
        direct_wake=direct_wake,
    )
    return DeliveryPlan(
        decision_id=decision_id,
        group_id=group_id,
        segments=segments,
        delay_seconds=delay,
        expires_at=int(now) + max(1, int(ttl_seconds)),
        quote_message_id=quote_message_id,
    )


def delivery_still_valid(plan: DeliveryPlan, now: int) -> bool:
    return bool(plan.segments or plan.outbound) and int(now) <= int(plan.expires_at)


class DeliveryService:
    """The only component allowed to invoke PlatformPort send methods."""

    def __init__(
        self,
        platform,
        memory,
        clock,
        *,
        persona_id: str,
        character_name: str = "爱弥斯",
    ) -> None:
        self.platform = platform
        self.memory = memory
        self.clock = clock
        self.persona_id = str(persona_id or "").strip()
        if not self.persona_id:
            raise ValueError("persona_id is required")
        self.character_name = character_name

    async def deliver(
        self,
        plan: DeliveryPlan,
        *,
        kind: str = "reply",
        still_valid: Optional[Callable[[], bool]] = None,
        sent_reason: str = "sent",
    ) -> WorkflowOutcome:
        text = self._text_for_plan(plan)
        enqueue_task = asyncio.create_task(self._enqueue(plan, text, kind))
        try:
            inserted = await asyncio.shield(enqueue_task)
        except asyncio.CancelledError:
            inserted = await enqueue_task
            if inserted:
                await self._transition(
                    plan.decision_id,
                    OutboxStatus.PENDING,
                    OutboxStatus.EXPIRED,
                    failure_code="cancelled_during_enqueue",
                )
            raise
        if not inserted:
            return WorkflowOutcome(plan.decision_id, False, "duplicate_outbox")
        try:
            if plan.delay_seconds > 0:
                await asyncio.sleep(plan.delay_seconds)
        except asyncio.CancelledError:
            await self._transition(
                plan.decision_id,
                OutboxStatus.PENDING,
                OutboxStatus.EXPIRED,
                failure_code="cancelled_before_send",
            )
            raise
        now = self.clock.now()
        if (
            not delivery_still_valid(plan, now)
            or (still_valid is not None and not still_valid())
        ):
            await self._transition(
                plan.decision_id,
                OutboxStatus.PENDING,
                OutboxStatus.EXPIRED,
                failure_code="delivery_expired",
            )
            return WorkflowOutcome(plan.decision_id, False, "delivery_expired")
        claimed = await self._transition(
            plan.decision_id,
            OutboxStatus.PENDING,
            OutboxStatus.SENDING,
            increment_attempt=True,
        )
        if not claimed:
            return WorkflowOutcome(plan.decision_id, False, "outbox_not_pending")
        try:
            result = await self._send(plan)
        except asyncio.CancelledError:
            await self._transition(
                plan.decision_id,
                OutboxStatus.SENDING,
                OutboxStatus.UNKNOWN,
                failure_code="send_cancelled",
            )
            raise
        except asyncio.TimeoutError as exc:
            await self._transition(
                plan.decision_id,
                OutboxStatus.SENDING,
                OutboxStatus.UNKNOWN,
                failure_code="send_timeout",
                failure_detail=str(exc),
            )
            return WorkflowOutcome(plan.decision_id, False, "send_unknown")
        except Exception as exc:
            await self._transition(
                plan.decision_id,
                OutboxStatus.SENDING,
                OutboxStatus.FAILED,
                failure_code="send_error",
                failure_detail=exc.__class__.__name__ + ":" + str(exc),
            )
            return WorkflowOutcome(
                plan.decision_id,
                False,
                "send_error:" + exc.__class__.__name__,
            )
        if not isinstance(result, SendResult):
            await self._transition(
                plan.decision_id,
                OutboxStatus.SENDING,
                OutboxStatus.FAILED,
                failure_code="invalid_platform_result",
                failure_detail=type(result).__name__,
            )
            return WorkflowOutcome(plan.decision_id, False, "send_failed")
        if result.kind is SendReceiptKind.FAILED:
            await self._transition(
                plan.decision_id,
                OutboxStatus.SENDING,
                OutboxStatus.FAILED,
                failure_code=result.error_code or "platform_failed",
                failure_detail=result.error_detail,
            )
            return WorkflowOutcome(plan.decision_id, False, "send_failed")
        if result.kind is SendReceiptKind.UNKNOWN:
            await self._transition(
                plan.decision_id,
                OutboxStatus.SENDING,
                OutboxStatus.UNKNOWN,
                failure_code=result.error_code or "no_receipt",
                failure_detail=result.error_detail,
            )
            return WorkflowOutcome(plan.decision_id, False, "send_unknown")

        sent_at = self.clock.now()
        outbound = tuple(plan.outbound or ())
        segment_types = (
            tuple(item.kind.value for item in outbound)
            if outbound
            else ("text",)
        )
        image_urls = tuple(
            item.media_ref
            for item in outbound
            if item.kind is OutboundKind.IMAGE
        )
        media_ids = [
            item.media_id
            for item in outbound
            if item.kind is OutboundKind.IMAGE
        ]
        bot_message = ChatMessage(
            message_id="bot-" + plan.decision_id,
            group_id=plan.group_id,
            sender_id="__bot__",
            sender_name=self.character_name,
            text=text,
            timestamp=sent_at,
            is_bot=True,
            image_urls=image_urls,
            segment_types=segment_types,
            origin=MessageOrigin.BOT_DELIVERY,
            decision_id=plan.decision_id,
            ingested_at=sent_at,
            metadata={
                "origin": "bot_delivery",
                "decision_id": plan.decision_id,
                "delivery_kind": kind,
                "media_ids": media_ids,
            },
        )
        finalized = await self._finalize(
            plan.decision_id, sent_at, bot_message, sent_reason
        )
        if not finalized:
            return WorkflowOutcome(plan.decision_id, False, "finalize_failed")
        return WorkflowOutcome(plan.decision_id, True, sent_reason, text)

    async def _send(self, plan: DeliveryPlan):
        outbound = plan.outbound or tuple(
            OutboundSegment(OutboundKind.TEXT, text=segment)
            for segment in plan.segments
        )
        return await self.platform.send_outbound(
            plan.group_id,
            outbound,
            plan.decision_id,
            plan.quote_message_id,
        )

    async def _enqueue(self, plan: DeliveryPlan, text: str, kind: str) -> bool:
        return bool(
            await self.memory.enqueue_outbox_async(
                self.persona_id,
                plan.decision_id,
                plan.group_id,
                text,
                self.clock.now(),
                plan.expires_at,
                quote_message_id=plan.quote_message_id,
                segments=plan.segments,
                outbound=plan.outbound,
                kind=kind,
            )
        )

    @staticmethod
    def _text_for_plan(plan: DeliveryPlan) -> str:
        if plan.outbound:
            return "\n".join(
                item.text
                for item in plan.outbound
                if item.kind is OutboundKind.TEXT and item.text
            )
        return "\n".join(plan.segments)

    async def _transition(self, decision_id, expected, status, **kwargs) -> bool:
        return bool(
            await self.memory.transition_outbox_async(
                self.persona_id,
                decision_id,
                expected.value,
                status.value,
                **kwargs,
            )
        )

    async def _finalize(self, decision_id, sent_at, bot_message, reason) -> bool:
        return bool(
            await self.memory.finalize_delivery_async(
                self.persona_id,
                decision_id,
                sent_at,
                bot_message,
                reason,
            )
        )
