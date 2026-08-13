"""Source-grounded event matching for natural continuity follow-ups."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Optional
from uuid import NAMESPACE_URL, uuid5

from ..models import (
    ContinuityFollowupEvent,
    ContinuityFollowupOutcome,
    ContinuityStatus,
    TopicSnapshot,
    TriggerKind,
)
from .reminder_infer import looks_like_timed_reminder_continuity

EXTRACTOR_VERSION = "context-llm-v1"
LEXICAL_EXTRACTOR_VERSION = "lexical-v1"
FOLLOWUP_EXTRACT_TIMEOUT_SECONDS = 6.0
MIN_MATCH_CONFIDENCE = 0.92
MIN_SPEAK_CONFIDENCE = 0.97
_COMPLETE_HINT = re.compile(r"(完了|结束了|搞定了|出结果|发挥|取消了|不做了|不去了)")
_CANCEL_HINT = re.compile(r"(取消了|不做了|不去了|算了不用)")


@dataclass(frozen=True)
class FollowupMatch:
    event: ContinuityFollowupEvent
    item_summary: str

    @property
    def should_speak(self) -> bool:
        return self.event.response_policy == "speak"

    @property
    def contribution(self) -> str:
        outcome = {
            ContinuityFollowupOutcome.PROGRESS: "出现了明确的新进展",
            ContinuityFollowupOutcome.COMPLETED: "对方明确报告事情已有结果",
            ContinuityFollowupOutcome.CANCELLED: "对方明确说这件事取消了",
        }[self.event.outcome]
        return (
            "你记得对方之前提过「{}」，现在{}。自然接住对方刚说的内容，"
            "一句短回应即可；不要说自己在跟踪、匹配或读取记录，不要盘问清单。"
        ).format(self.item_summary[:160], outcome)


class ContinuityFollowupMatcher:
    """Match a new member message to one open item without manufacturing facts."""

    def __init__(self, store, model, *, persona_id: str) -> None:
        self.store = store
        self.model = model
        self.persona_id = str(persona_id or "").strip()
        if not self.persona_id:
            raise ValueError("persona_id is required")

    async def match(
        self,
        topic: TopicSnapshot,
        *,
        trigger: TriggerKind,
        decision_id: str,
        now: int,
    ) -> Optional[FollowupMatch]:
        latest = topic.latest
        extractor = getattr(self.model, "extract_continuity_followup", None)
        if latest is None or latest.is_bot or not latest.text.strip():
            return None
        subject_ids = self.store.member_subject_ids(
            self.persona_id, topic.group_id, latest.sender_id
        )
        open_items = tuple(
            item
            for item in self.store.list_continuity_items(
                self.persona_id,
                group_id=topic.group_id,
                subject_ids=subject_ids,
                statuses=(ContinuityStatus.OPEN,),
                limit=12,
            )
            if not looks_like_timed_reminder_continuity(
                item.summary, item.source_quote
            )
        )
        related = tuple(
            item
            for item in open_items
            if self._possibly_related(latest.text, (item,))
        )
        if not related:
            return None
        raw = self._lexical_followup(latest.text, related)
        if raw is None and callable(extractor):
            try:
                raw = await asyncio.wait_for(
                    extractor(topic=topic, open_items=related),
                    timeout=FOLLOWUP_EXTRACT_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                raw = None
        if not isinstance(raw, dict):
            return None
        item_id = str(raw.get("item_id") or "").strip()
        item = next((entry for entry in open_items if entry.item_id == item_id), None)
        if item is None:
            return None
        try:
            confidence = float(raw.get("confidence") or 0)
            outcome = ContinuityFollowupOutcome(
                str(raw.get("outcome") or "").strip().lower()
            )
        except (TypeError, ValueError):
            return None
        quote = " ".join(str(raw.get("evidence_quote") or "").split())[:180]
        source = " ".join(str(latest.text or "").split())
        if confidence < MIN_MATCH_CONFIDENCE or len(quote) < 2 or quote not in source:
            return None
        requested_policy = str(raw.get("response_policy") or "observe").lower()
        decisive = outcome in {
            ContinuityFollowupOutcome.COMPLETED,
            ContinuityFollowupOutcome.CANCELLED,
        }
        response_policy = (
            "speak"
            if (requested_policy == "speak" or decisive)
            and confidence >= MIN_SPEAK_CONFIDENCE
            and trigger is TriggerKind.CANDIDATE
            and not latest.reply_to_message_id
            and not latest.mentioned_user_ids
            else "observe"
        )
        event_id = str(
            uuid5(
                NAMESPACE_URL,
                "groupmate:{}:{}:{}:{}".format(
                    self.persona_id,
                    topic.group_id,
                    latest.message_id,
                    item.item_id,
                ),
            )
        )
        event = self.store.append_continuity_followup(
            self.persona_id,
            ContinuityFollowupEvent(
                event_id=event_id,
                item_id=item.item_id,
                group_id=topic.group_id,
                subject_id=latest.sender_id,
                source_message_id=latest.message_id,
                evidence_quote=quote,
                outcome=outcome,
                response_policy=response_policy,
                confidence=confidence,
                occurred_at=int(now),
                decision_id=decision_id,
                extractor_version=str(
                    raw.get("extractor_version") or EXTRACTOR_VERSION
                ),
            ),
        )
        return FollowupMatch(event, item.summary) if event is not None else None

    @staticmethod
    def _lexical_followup(text: str, related_items):
        if len(related_items) != 1:
            return None
        source = " ".join(str(text or "").split())
        hit = _COMPLETE_HINT.search(source)
        if hit is None:
            return None
        quote = hit.group(0)
        if quote not in source:
            return None
        outcome = (
            "cancelled" if _CANCEL_HINT.search(source) else "completed"
        )
        return {
            "item_id": related_items[0].item_id,
            "outcome": outcome,
            "response_policy": "speak",
            "evidence_quote": quote,
            "confidence": 0.99,
            "extractor_version": LEXICAL_EXTRACTOR_VERSION,
        }

    @staticmethod
    def _possibly_related(text: str, open_items) -> bool:
        ignored = set("的了呢吗啊呀吧我你他她它是有在就也都和与把被这那一个会要后再")

        def signals(value: str):
            cleaned = "".join(
                char.lower()
                for char in str(value or "")
                if char.isalnum() and char not in ignored
            )
            chars = set(cleaned)
            grams = {cleaned[index : index + 2] for index in range(len(cleaned) - 1)}
            return chars, grams

        text_chars, text_grams = signals(text)
        for item in open_items:
            item_chars, item_grams = signals(
                "{} {}".format(item.summary, item.source_quote)
            )
            if len(text_chars.intersection(item_chars)) >= 2:
                return True
            if text_grams.intersection(item_grams):
                return True
        return False
