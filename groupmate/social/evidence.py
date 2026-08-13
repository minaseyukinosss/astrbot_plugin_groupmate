"""Context-validated relationship evidence extraction and persistence."""

from __future__ import annotations

from typing import Optional, Sequence
from uuid import NAMESPACE_URL, uuid5

from ..core.response_act import ResponseAct, ResponseActPlan
from ..models import (
    AddresseeKind,
    SocialEvent,
    SocialEventKind,
    SocialEventStatus,
    TargetingDecision,
    TopicSnapshot,
    TriggerKind,
)

EXTRACTOR_VERSION = "context-llm-v1"

_MIN_CONFIDENCE = {
    SocialEventKind.THANKS: 0.86,
    SocialEventKind.PRAISE: 0.88,
    SocialEventKind.HELP_REQUEST: 0.90,
    SocialEventKind.HELPED: 0.90,
    SocialEventKind.FRIENDLY_TEASE: 0.90,
    SocialEventKind.CORRECTION: 0.88,
    SocialEventKind.BOUNDARY_PUSH: 0.92,
    SocialEventKind.HARASSMENT: 0.95,
    SocialEventKind.APOLOGY: 0.90,
}
_POSITIVE_KINDS = frozenset(
    {
        SocialEventKind.THANKS,
        SocialEventKind.PRAISE,
        SocialEventKind.HELPED,
        SocialEventKind.FRIENDLY_TEASE,
    }
)
_NEGATIVE_KINDS = frozenset(
    {SocialEventKind.BOUNDARY_PUSH, SocialEventKind.HARASSMENT}
)


class RelationshipEvidenceWriter:
    """Accepts only source-grounded, single-owner social evidence."""

    def __init__(
        self,
        store,
        model,
        *,
        persona_id: str,
        active_groups: Sequence[str] = (),
        min_reviewed_samples: int = 20,
        max_error_rate: float = 0.10,
    ) -> None:
        self.store = store
        self.model = model
        self.persona_id = str(persona_id or "").strip()
        if not self.persona_id:
            raise ValueError("persona_id is required")
        self.active_groups = frozenset(str(item) for item in active_groups)
        self.min_reviewed_samples = max(1, int(min_reviewed_samples))
        self.max_error_rate = max(0.0, min(1.0, float(max_error_rate)))

    async def process(
        self,
        topic: TopicSnapshot,
        targeting: TargetingDecision,
        *,
        trigger: TriggerKind,
        decision_id: str,
        now: int,
        response_act: ResponseActPlan,
        reply_text: str,
        participation_reasons: Sequence[str] = (),
        configured_relationship: str = "",
    ) -> Optional[SocialEvent]:
        latest = topic.latest
        target = targeting.social_target
        extractor = getattr(self.model, "extract_relationship_evidence", None)
        if (
            latest is None
            or latest.is_bot
            or not latest.text.strip()
            or target.kind is not AddresseeKind.USER
            or len(target.target_user_ids) != 1
            or str(target.target_user_ids[0]) != str(latest.sender_id)
            or float(target.confidence) < 0.7
            or not callable(extractor)
        ):
            return None
        raw = await extractor(
            topic=topic,
            targeting=targeting,
            trigger=trigger,
            response_act=response_act,
            reply_text=reply_text,
        )
        event = self._validated_event(
            raw,
            topic=topic,
            decision_id=decision_id,
            response_act=response_act,
            participation_reasons=participation_reasons,
        )
        if event is None:
            return None
        self.store.record_social_interaction(
            self.persona_id,
            event,
            configured_relationship=configured_relationship or None,
            now=now,
        )
        return event

    def _validated_event(
        self,
        raw,
        *,
        topic: TopicSnapshot,
        decision_id: str,
        response_act: ResponseActPlan,
        participation_reasons: Sequence[str],
    ) -> Optional[SocialEvent]:
        if not isinstance(raw, dict):
            return None
        kind_raw = str(raw.get("kind") or "").strip().upper()
        if not kind_raw or kind_raw in {"NONE", "NEUTRAL"}:
            return None
        try:
            kind = SocialEventKind(kind_raw)
        except ValueError:
            return None
        if kind is SocialEventKind.NEUTRAL:
            return None
        try:
            confidence = float(raw.get("confidence") or 0)
        except (TypeError, ValueError):
            return None
        if confidence < _MIN_CONFIDENCE.get(kind, 1.0):
            return None
        latest = topic.latest
        if latest is None:
            return None
        quote = " ".join(str(raw.get("evidence_quote") or "").split())[:160]
        source = " ".join(str(latest.text or "").split())
        if len(quote) < 2 or quote not in source:
            return None
        act = response_act.act
        previous = self.store.list_social_events(
            self.persona_id,
            topic.group_id,
            user_id=latest.sender_id,
            limit=200,
        )
        accepted_previous = [
            item for item in previous if item.status is SocialEventStatus.ACCEPTED
        ]
        prior_negative = any(item.kind in _NEGATIVE_KINDS for item in accepted_previous)
        reasons = set(str(item) for item in (participation_reasons or ()))
        pressure_boundary = bool(
            reasons.intersection(
                {
                    "poke_boundary_silence",
                    "pressure:pester",
                    "pressure:after_boundary",
                    "direct_pressure:pester",
                    "direct_pressure:after_boundary",
                }
            )
        )
        if kind in _POSITIVE_KINDS and act is ResponseAct.BOUNDARY:
            return None
        if kind is SocialEventKind.FRIENDLY_TEASE and act is not ResponseAct.PLAYFUL_REPLY:
            return None
        if kind is SocialEventKind.HELP_REQUEST and act not in {
            ResponseAct.ANSWER,
            ResponseAct.CLARIFY,
            ResponseAct.TASK_HANDOFF,
            ResponseAct.TASK_UNSUPPORTED,
        }:
            return None
        if kind is SocialEventKind.BOUNDARY_PUSH and act is not ResponseAct.BOUNDARY:
            return None
        if kind is SocialEventKind.HARASSMENT and not (
            act is ResponseAct.BOUNDARY and (prior_negative or pressure_boundary)
        ):
            return None
        if kind is SocialEventKind.APOLOGY and not prior_negative:
            return None
        event_id = str(
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
        return SocialEvent(
            event_id=event_id,
            group_id=topic.group_id,
            user_id=latest.sender_id,
            kind=kind,
            source_message_id=latest.message_id,
            confidence=max(0.0, min(1.0, confidence)),
            occurred_at=latest.timestamp,
            decision_id=decision_id,
            evidence_text=quote,
            reason_code=str(raw.get("reason_code") or "context_verified")[:80],
            extractor_version=EXTRACTOR_VERSION,
            status=self._initial_status(topic.group_id),
        )

    def _initial_status(self, group_id: str) -> SocialEventStatus:
        if str(group_id) not in self.active_groups:
            return SocialEventStatus.PENDING
        quality_reader = getattr(self.store, "relationship_learning_quality", None)
        if not callable(quality_reader):
            return SocialEventStatus.PENDING
        quality = quality_reader(self.persona_id, str(group_id))
        if (
            int(quality.get("reviewed_count") or 0) >= self.min_reviewed_samples
            and float(quality.get("error_rate") or 0.0) <= self.max_error_rate
        ):
            return SocialEventStatus.ACCEPTED
        return SocialEventStatus.PENDING
