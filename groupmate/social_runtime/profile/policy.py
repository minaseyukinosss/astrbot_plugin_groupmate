"""Local evidence policy for model-proposed member cognition."""

from __future__ import annotations

from dataclasses import replace

from ...profile_vocabulary import FACT_CATEGORIES, RELATION_TYPES
from .contracts import (
    ProfileCorrection,
    ProfileEpisode,
    ProfileFact,
    ProfileFactCandidate,
    SocialEdge,
    SocialEdgeCandidate,
)


class ProfileEvidencePolicy:
    SELF_CONFIRM_THRESHOLD = 0.86
    PATTERN_CONFIRM_THRESHOLD = 0.88
    PATTERN_MIN_EVIDENCE = 3
    MAX_STORED_EVIDENCE = 16

    @classmethod
    def _merge_evidence(
        cls, old: tuple[str, ...], new: tuple[str, ...]
    ) -> tuple[tuple[str, ...], int]:
        """Merge independent event references while honoring contract bounds."""

        all_ids = tuple(dict.fromkeys((*old, *new)))
        return all_ids[-cls.MAX_STORED_EVIDENCE :], len(all_ids)

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
        elif candidate.source_kind in {
            "system",
            "admin_correction",
            "self_correction",
        }:
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

    def reinforce_fact(
        self, old: ProfileFact, incoming: ProfileFact
    ) -> ProfileFact:
        """Combine a stable claim seen in separate model batches."""

        if (
            old.fact_id,
            old.persona_id,
            old.group_id,
            old.subject_id,
            old.category,
            old.source_kind,
        ) != (
            incoming.fact_id,
            incoming.persona_id,
            incoming.group_id,
            incoming.subject_id,
            incoming.category,
            incoming.source_kind,
        ):
            raise ValueError("profile fact reinforcement scope mismatch")
        evidence, total_count = self._merge_evidence(
            old.source_event_ids, incoming.source_event_ids
        )
        candidate = ProfileFactCandidate(
            candidate_id=old.fact_id,
            persona_id=old.persona_id,
            group_id=old.group_id,
            subject_id=old.subject_id,
            category=old.category,
            # Keep the first accepted wording; only the identity key is normalized.
            summary=old.summary,
            source_kind=old.source_kind,
            source_actor_id=old.source_actor_id,
            source_event_ids=evidence,
            confidence=max(old.confidence, incoming.confidence),
            evidence_count=max(
                old.evidence_count
                + len(
                    set(incoming.source_event_ids)
                    - set(old.source_event_ids)
                ),
                incoming.evidence_count,
                total_count,
            ),
            observed_at=min(old.valid_from, incoming.valid_from),
        )
        reinforced = self.decide(candidate, allowed_event_ids=set(evidence))
        if old.status in {"stale", "rejected", "superseded"}:
            return replace(
                reinforced,
                status=old.status,
                injectable=False,
                valid_until=old.valid_until,
                supersedes_fact_id=old.supersedes_fact_id,
            )
        if old.status == "confirmed" and reinforced.status != "confirmed":
            return replace(reinforced, status="confirmed", injectable=True)
        return reinforced

    def reinforce_edge(
        self, old: SocialEdge, incoming: SocialEdge
    ) -> SocialEdge:
        """Combine repeated observations of the same scoped relationship."""

        if (
            old.edge_id,
            old.persona_id,
            old.group_id,
            old.source_member_id,
            old.target_member_id,
            old.relation_type,
            old.direction,
        ) != (
            incoming.edge_id,
            incoming.persona_id,
            incoming.group_id,
            incoming.source_member_id,
            incoming.target_member_id,
            incoming.relation_type,
            incoming.direction,
        ):
            raise ValueError("social edge reinforcement scope mismatch")
        evidence, _ = self._merge_evidence(
            old.source_event_ids, incoming.source_event_ids
        )
        candidate = SocialEdgeCandidate(
            candidate_id=old.edge_id,
            persona_id=old.persona_id,
            group_id=old.group_id,
            source_member_id=old.source_member_id,
            target_member_id=old.target_member_id,
            relation_type=old.relation_type,
            direction=old.direction,
            strength=max(old.strength, incoming.strength),
            confidence=max(old.confidence, incoming.confidence),
            source_event_ids=evidence,
            observed_at=max(old.last_observed_at, incoming.last_observed_at),
        )
        reinforced = replace(
            self.decide_edge(candidate, allowed_event_ids=set(evidence)),
            valid_from=min(old.valid_from, incoming.valid_from),
            valid_until=old.valid_until,
        )
        if old.status in {"rejected", "stale"}:
            return replace(reinforced, status=old.status)
        if old.status == "confirmed" and reinforced.status != "confirmed":
            return replace(reinforced, status="confirmed")
        return reinforced

    def reinforce_episode(
        self, old: ProfileEpisode, incoming: ProfileEpisode
    ) -> ProfileEpisode:
        """Combine later evidence for the same scoped episode."""

        if (
            old.episode_id,
            old.persona_id,
            old.group_id,
        ) != (
            incoming.episode_id,
            incoming.persona_id,
            incoming.group_id,
        ):
            raise ValueError("profile episode reinforcement scope mismatch")
        evidence, _ = self._merge_evidence(
            old.source_event_ids, incoming.source_event_ids
        )
        confidence = max(old.confidence, incoming.confidence)
        status = (
            "confirmed"
            if (
                old.status == "confirmed"
                or (
                    len(set(evidence)) >= 2
                    and confidence >= self.PATTERN_CONFIRM_THRESHOLD
                )
            )
            else old.status
        )
        if old.status in {"rejected", "stale"}:
            status = old.status
        return ProfileEpisode(
            episode_id=old.episode_id,
            persona_id=old.persona_id,
            group_id=old.group_id,
            title=old.title,
            summary=old.summary,
            participants=tuple(dict.fromkeys((*old.participants, *incoming.participants))),
            source_event_ids=evidence,
            episode_type=old.episode_type,
            valence=incoming.valence,
            importance=max(old.importance, incoming.importance),
            confidence=confidence,
            status=status,
            occurred_at=min(old.occurred_at, incoming.occurred_at),
            last_reinforced_at=max(
                old.last_reinforced_at, incoming.last_reinforced_at
            ),
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
