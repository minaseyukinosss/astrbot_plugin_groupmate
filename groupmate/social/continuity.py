"""Source-grounded lifecycle for plans, promises, and follow-ups."""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional
from uuid import NAMESPACE_URL, uuid5

from ..models import (
    AddresseeKind,
    ContinuityItem,
    ContinuityKind,
    ContinuityStatus,
    TargetingDecision,
    TopicSnapshot,
)

logger = logging.getLogger(__name__)

EXTRACTOR_VERSION = "context-llm-v1"
MIN_OPEN_CONFIDENCE = 0.90
MIN_RESOLUTION_CONFIDENCE = 0.88


class ContinuityWriter:
    """Extract and update only auditable, single-owner continuity items."""

    def __init__(
        self,
        store,
        model,
        *,
        persona_id: str,
        on_error: Optional[Callable[[BaseException], None]] = None,
    ) -> None:
        self.store = store
        self.model = model
        self.persona_id = str(persona_id or "").strip()
        if not self.persona_id:
            raise ValueError("persona_id is required")
        self.on_error = on_error

    def schedule_after_send(
        self,
        topic: TopicSnapshot,
        targeting: TargetingDecision,
        *,
        decision_id: str,
        now: int,
        reply_text: str = "",
    ) -> None:
        async def _safe() -> None:
            try:
                await self.process(
                    topic,
                    targeting,
                    decision_id=decision_id,
                    now=now,
                    reply_text=reply_text,
                )
            except Exception as exc:  # noqa: BLE001 - never break a sent reply
                logger.exception("ContinuityWriter failed: %s", exc)
                if self.on_error is not None:
                    try:
                        self.on_error(exc)
                    except Exception:  # noqa: BLE001
                        pass

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(_safe())
            return
        task = loop.create_task(_safe())
        task.add_done_callback(lambda _: None)

    async def process(
        self,
        topic: TopicSnapshot,
        targeting: TargetingDecision,
        *,
        decision_id: str,
        now: int,
        reply_text: str = "",
    ) -> Optional[ContinuityItem]:
        latest = topic.latest
        target = targeting.memory_subject
        extractor = getattr(self.model, "extract_continuity_update", None)
        if (
            latest is None
            or latest.is_bot
            or not latest.text.strip()
            or target.kind is not AddresseeKind.USER
            or len(target.target_user_ids) != 1
            or str(target.target_user_ids[0]) != str(latest.sender_id)
            or float(target.confidence) < 0.7
            or "no_personal_memory" in target.reason_codes
            or not callable(extractor)
        ):
            return None
        subject_id = str(target.target_user_ids[0])
        subject_ids = self.store.member_subject_ids(
            self.persona_id, topic.group_id, subject_id
        )
        open_items = self.store.list_continuity_items(
            self.persona_id,
            group_id=topic.group_id,
            subject_ids=subject_ids,
            statuses=(ContinuityStatus.OPEN,),
            limit=12,
        )
        raw = await extractor(
            topic=topic,
            targeting=targeting,
            open_items=open_items,
            reply_text=reply_text,
        )
        return self._apply_validated(
            raw,
            topic=topic,
            subject_id=subject_id,
            open_items=open_items,
            decision_id=decision_id,
            now=now,
        )

    def _apply_validated(
        self,
        raw,
        *,
        topic: TopicSnapshot,
        subject_id: str,
        open_items,
        decision_id: str,
        now: int,
    ) -> Optional[ContinuityItem]:
        del decision_id
        if not isinstance(raw, dict):
            return None
        action = str(raw.get("action") or "").strip().upper()
        if action in {"", "NONE"}:
            return None
        try:
            confidence = float(raw.get("confidence") or 0)
        except (TypeError, ValueError):
            return None
        latest = topic.latest
        if latest is None:
            return None
        quote = " ".join(str(raw.get("evidence_quote") or "").split())[:180]
        source = " ".join(str(latest.text or "").split())
        if len(quote) < 2 or quote not in source:
            return None

        if action == "OPEN":
            if confidence < MIN_OPEN_CONFIDENCE:
                return None
            summary = " ".join(str(raw.get("summary") or "").split())[:240]
            if len(summary) < 4:
                return None
            try:
                kind = ContinuityKind(str(raw.get("kind") or "").strip().lower())
            except ValueError:
                return None
            item_id = str(
                uuid5(
                    NAMESPACE_URL,
                    "groupmate:{}:{}:{}:{}".format(
                        self.persona_id,
                        topic.group_id,
                        latest.message_id,
                        kind.value,
                    ),
                )
            )
            return self.store.append_continuity_item(
                self.persona_id,
                ContinuityItem(
                    item_id=item_id,
                    group_id=topic.group_id,
                    subject_id=subject_id,
                    kind=kind,
                    summary=summary,
                    source_message_id=latest.message_id,
                    source_quote=quote,
                    created_at=int(now),
                    updated_at=int(now),
                    due_at=self._optional_timestamp(raw.get("due_at")),
                    confidence=confidence,
                    extractor_version=EXTRACTOR_VERSION,
                ),
            )

        if action not in {"COMPLETE", "CANCEL"} or confidence < MIN_RESOLUTION_CONFIDENCE:
            return None
        item_id = str(raw.get("item_id") or "").strip()
        allowed = {item.item_id for item in open_items}
        if item_id not in allowed:
            return None
        status = (
            ContinuityStatus.COMPLETED
            if action == "COMPLETE"
            else ContinuityStatus.CANCELLED
        )
        return self.store.resolve_continuity_item(
            self.persona_id,
            item_id,
            status=status,
            resolution_message_id=latest.message_id,
            resolution_quote=quote,
            resolved_at=int(now),
        )

    @staticmethod
    def _optional_timestamp(value) -> Optional[int]:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None
