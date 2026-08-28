"""Bounded local retrieval and fail-closed fresh-evidence assessment."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import KnowledgeNeed, TopicUnderstandingFrame
from .repository import KnowledgeRepository


@dataclass(frozen=True)
class KnowledgeHit:
    knowledge_id: str
    entity_id: str
    safe_summary: str
    knowledge_kind: str
    evidence_level: str
    checked_at: int | None

    def __post_init__(self) -> None:
        if not self.knowledge_id or not self.entity_id or not self.safe_summary:
            raise ValueError("knowledge hit identity and summary are required")
        if len(self.safe_summary) > 500:
            raise ValueError("knowledge hit summary is too long")


class KnowledgeRetriever:
    def __init__(self, repository: KnowledgeRepository) -> None:
        self.repository = repository

    def retrieve(
        self,
        frame: TopicUnderstandingFrame,
        group_id: str,
        now: int,
        *,
        limit: int = 8,
    ) -> tuple[KnowledgeHit, ...]:
        del group_id, now
        maximum = max(1, min(8, int(limit)))
        entity_ids = tuple(
            dict.fromkeys(
                (
                    *frame.game_ids,
                    *(item.entity_id for item in frame.resolved_entities),
                    *(item.term_id for item in frame.resolved_terms),
                )
            )
        )
        claims = self.repository.active_claims(entity_ids, limit=maximum)
        return tuple(
            KnowledgeHit(
                knowledge_id=claim.claim_id,
                entity_id=claim.subject_entity_id,
                safe_summary=claim.safe_summary,
                knowledge_kind=claim.claim_kind,
                evidence_level=claim.evidence_level,
                checked_at=claim.checked_at,
            )
            for claim in claims[:maximum]
        )


class KnowledgeNeedAssessor:
    _QUERY_INTENTS = {
        "risk:version_state": "verify_version_state",
        "risk:date_time": "verify_date_or_schedule",
        "risk:entity_list": "verify_current_entity_list",
        "risk:numeric": "verify_current_numeric_fact",
        "risk:official_status": "verify_official_status",
        "risk:rumor_status": "verify_rumor_status",
    }

    def assess(
        self,
        frame: TopicUnderstandingFrame,
        hits: tuple[KnowledgeHit, ...],
        *,
        now: int,
    ) -> KnowledgeNeed:
        timestamp = int(now)
        codes = tuple(frame.ambiguity_codes)
        if "direct_unresolved" in codes:
            return KnowledgeNeed.create(
                outcome="unresolvable",
                gap_codes=("direct_knowledge_target_unresolved",),
                entity_ids=frame.game_ids,
                query_intents=(),
                expires_at=timestamp + 60,
            )
        risk_codes = tuple(code for code in codes if code.startswith("risk:"))
        if risk_codes or frame.version_reference is not None:
            query_intents = tuple(
                dict.fromkeys(
                    self._QUERY_INTENTS[code]
                    for code in risk_codes
                    if code in self._QUERY_INTENTS
                )
            )[:2]
            if not query_intents:
                query_intents = ("verify_version_state",)
            return KnowledgeNeed.create(
                outcome="fresh_evidence_required",
                gap_codes=risk_codes or ("fresh_version_evidence_required",),
                entity_ids=frame.game_ids,
                query_intents=query_intents,
                expires_at=timestamp + 300,
            )
        if "unknown_game_entity" in codes:
            return KnowledgeNeed.create(
                outcome="background_learning",
                gap_codes=("unknown_game_entity",),
                entity_ids=(),
                query_intents=("learn_unknown_game",),
                expires_at=timestamp + 24 * 60 * 60,
            )
        if hits or frame.game_ids or frame.resolved_terms:
            return KnowledgeNeed.create(
                outcome="local_sufficient",
                gap_codes=(),
                entity_ids=frame.game_ids,
                query_intents=(),
                expires_at=timestamp + 60 * 60,
            )
        return KnowledgeNeed.create(
            outcome="none",
            gap_codes=(),
            entity_ids=(),
            query_intents=(),
            expires_at=timestamp,
        )


__all__ = (
    "KnowledgeHit",
    "KnowledgeNeedAssessor",
    "KnowledgeRetriever",
)
