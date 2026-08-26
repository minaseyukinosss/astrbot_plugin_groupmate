"""Local evidence policy for model-proposed member cognition."""

from __future__ import annotations

from dataclasses import replace

from .contracts import (
    ProfileCorrection,
    ProfileFact,
    ProfileFactCandidate,
    SocialEdge,
    SocialEdgeCandidate,
)


FACT_CATEGORIES = frozenset(
    {
        "identity",
        "preference",
        "dislike",
        "boundary",
        "interest",
        "skill",
        "speech_style",
        "behavior_pattern",
        "group_role",
    }
)
RELATION_TYPES = frozenset(
    {
        "frequent_interaction",
        "familiar",
        "supportive",
        "technical_peer",
        "teasing",
        "conflict",
        "avoidance",
        "custom",
    }
)


class ProfileEvidencePolicy:
    SELF_CONFIRM_THRESHOLD = 0.86
    PATTERN_CONFIRM_THRESHOLD = 0.88
    PATTERN_MIN_EVIDENCE = 3

    def decide(
        self,
        candidate: ProfileFactCandidate,
        *,
        allowed_event_ids: set[str] | frozenset[str],
    ) -> ProfileFact:
        evidence_allowed = set(candidate.source_event_ids) <= set(
            allowed_event_ids
        )
        if candidate.category not in FACT_CATEGORIES or not evidence_allowed:
            status, injectable = "rejected", False
        elif candidate.source_kind in {"system", "admin_correction"}:
            status, injectable = "confirmed", True
        elif (
            candidate.source_kind == "self_statement"
            and candidate.source_actor_id == candidate.subject_id
            and candidate.confidence >= self.SELF_CONFIRM_THRESHOLD
        ):
            status, injectable = "confirmed", True
        elif (
            candidate.source_kind == "observed_pattern"
            and candidate.evidence_count >= self.PATTERN_MIN_EVIDENCE
            and len(set(candidate.source_event_ids))
            >= self.PATTERN_MIN_EVIDENCE
            and candidate.confidence >= self.PATTERN_CONFIRM_THRESHOLD
        ):
            status, injectable = "confirmed", True
        else:
            status, injectable = "proposed", False
        return ProfileFact(
            fact_id=candidate.candidate_id,
            persona_id=candidate.persona_id,
            group_id=candidate.group_id,
            subject_id=candidate.subject_id,
            category=candidate.category,
            summary=candidate.summary,
            source_kind=candidate.source_kind,
            source_actor_id=candidate.source_actor_id,
            source_event_ids=candidate.source_event_ids,
            confidence=candidate.confidence,
            status=status,
            evidence_count=candidate.evidence_count,
            valid_from=candidate.observed_at,
            injectable=injectable,
        )

    def decide_edge(
        self,
        candidate: SocialEdgeCandidate,
        *,
        allowed_event_ids: set[str] | frozenset[str],
    ) -> SocialEdge:
        evidence_allowed = set(candidate.source_event_ids) <= set(
            allowed_event_ids
        )
        confirmed = (
            candidate.relation_type in RELATION_TYPES
            and evidence_allowed
            and len(set(candidate.source_event_ids)) >= 3
            and candidate.confidence >= self.PATTERN_CONFIRM_THRESHOLD
        )
        status = (
            "confirmed"
            if confirmed
            else (
                "proposed"
                if candidate.relation_type in RELATION_TYPES and evidence_allowed
                else "rejected"
            )
        )
        return SocialEdge(
            edge_id=candidate.candidate_id,
            persona_id=candidate.persona_id,
            group_id=candidate.group_id,
            source_member_id=candidate.source_member_id,
            target_member_id=candidate.target_member_id,
            relation_type=candidate.relation_type,
            direction=candidate.direction,
            strength=candidate.strength,
            confidence=candidate.confidence,
            source_event_ids=candidate.source_event_ids,
            status=status,
            valid_from=candidate.observed_at,
            valid_until=None,
            last_observed_at=candidate.observed_at,
        )

    def correct(
        self, old: ProfileFact, replacement: ProfileFactCandidate
    ) -> ProfileCorrection:
        if (
            old.persona_id,
            old.group_id,
            old.subject_id,
        ) != (
            replacement.persona_id,
            replacement.group_id,
            replacement.subject_id,
        ):
            raise ValueError("profile correction must keep the original scope")
        corrected = self.decide(
            replacement,
            allowed_event_ids=set(replacement.source_event_ids),
        )
        if corrected.status != "confirmed":
            raise ValueError("profile correction must be authoritative")
        return ProfileCorrection(
            old=replace(
                old,
                status="superseded",
                injectable=False,
                valid_until=replacement.observed_at,
            ),
            new=replace(corrected, supersedes_fact_id=old.fact_id),
        )


__all__ = (
    "FACT_CATEGORIES",
    "ProfileEvidencePolicy",
    "RELATION_TYPES",
)
