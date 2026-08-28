"""Pure evidence-admission decisions for bounded game knowledge claims."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Iterable

from .contracts import EvidenceLevel, KnowledgeClaimCandidate


@dataclass(frozen=True)
class AdmissionDecision:
    """A persistence-free decision for one normalized claim candidate."""

    outcome: str
    reason_code: str
    superseded_claim_ids: tuple[str, ...] = ()


class KnowledgeAdmissionPolicy:
    """Apply the evidence ladder without changing stored claim history."""

    def evaluate(
        self,
        candidate: KnowledgeClaimCandidate,
        existing: Iterable[KnowledgeClaimCandidate],
    ) -> AdmissionDecision:
        if not isinstance(candidate, KnowledgeClaimCandidate):
            raise TypeError("candidate must be KnowledgeClaimCandidate")
        existing_items = tuple(existing)
        if any(
            not isinstance(item, KnowledgeClaimCandidate)
            for item in existing_items
        ):
            raise TypeError("existing must contain KnowledgeClaimCandidate")
        base_decision = self._ladder_decision(candidate)
        if base_decision.outcome != "activate":
            return base_decision
        comparable = tuple(
            item
            for item in existing_items
            if (
                self._same_claim_scope(candidate, item)
                and self._ladder_decision(item).outcome == "activate"
            )
        )

        official_existing = tuple(
            item
            for item in comparable
            if item.evidence_level is EvidenceLevel.OFFICIAL
        )
        if candidate.evidence_level is EvidenceLevel.OFFICIAL:
            newest_official_at = max(
                (item.checked_at for item in official_existing), default=None
            )
            if (
                newest_official_at is not None
                and candidate.checked_at < newest_official_at
            ):
                return AdmissionDecision("reject", "older_official_evidence")
            equal_time_official = tuple(
                item
                for item in official_existing
                if item.checked_at == candidate.checked_at
            )
            if any(
                self._conflicts(candidate, item)
                for item in equal_time_official
            ):
                return AdmissionDecision("dispute", "equal_evidence_conflict")
            if equal_time_official:
                return AdmissionDecision(
                    "keep_pending", "official_evidence_not_newer"
                )
            older_official = tuple(
                item
                for item in official_existing
                if item.checked_at < candidate.checked_at
            )
            if older_official:
                return AdmissionDecision(
                    "supersede",
                    "newer_official_supersedes",
                    tuple(item.candidate_id for item in older_official),
                )
            if any(
                item.evidence_level is not EvidenceLevel.OFFICIAL
                for item in comparable
            ):
                return AdmissionDecision(
                    "activate", "official_release_outranks_nonofficial"
                )

        if any(
            item.evidence_level is candidate.evidence_level
            and self._conflicts(candidate, item)
            for item in comparable
        ):
            return AdmissionDecision("dispute", "equal_evidence_conflict")

        return base_decision

    @staticmethod
    def _ladder_decision(candidate: KnowledgeClaimCandidate) -> AdmissionDecision:
        if candidate.evidence_level is EvidenceLevel.BUNDLED:
            if candidate.claim_kind.value == "stable_semantic":
                return AdmissionDecision("activate", "bundled_stable_semantic")
            return AdmissionDecision(
                "reject", "bundled_requires_stable_semantic"
            )
        if candidate.evidence_level is EvidenceLevel.OFFICIAL:
            if candidate.claim_kind.value != "public_fact":
                return AdmissionDecision("reject", "official_requires_public_fact")
            return AdmissionDecision("activate", "official_public_fact")
        if candidate.evidence_level is EvidenceLevel.CORROBORATED:
            if candidate.claim_kind.value == "stable_semantic":
                return AdmissionDecision(
                    "activate", "corroborated_stable_semantic"
                )
            return AdmissionDecision(
                "keep_pending", "corroborated_requires_stable_semantic"
            )
        if candidate.evidence_level is EvidenceLevel.SECONDARY:
            return AdmissionDecision(
                "keep_pending", "secondary_requires_corroboration"
            )
        if candidate.evidence_level is EvidenceLevel.UNOFFICIAL:
            if candidate.claim_kind.value == "rumor":
                return AdmissionDecision("activate", "unofficial_rumor")
            return AdmissionDecision("reject", "unofficial_requires_rumor")
        raise ValueError("candidate evidence level is unsupported")

    @classmethod
    def _conflicts(
        cls, candidate: KnowledgeClaimCandidate, existing: KnowledgeClaimCandidate
    ) -> bool:
        return cls._overlaps(candidate, existing) and (
            cls._normalized_value(candidate.safe_summary)
            != cls._normalized_value(existing.safe_summary)
        )

    @staticmethod
    def _overlaps(
        candidate: KnowledgeClaimCandidate, existing: KnowledgeClaimCandidate
    ) -> bool:
        candidate_start = (
            candidate.valid_from
            if candidate.valid_from is not None
            else candidate.checked_at
        )
        existing_start = (
            existing.valid_from
            if existing.valid_from is not None
            else existing.checked_at
        )
        return (
            existing.valid_until is None or candidate_start < existing.valid_until
        ) and (
            candidate.valid_until is None or existing_start < candidate.valid_until
        )

    @staticmethod
    def _normalized_value(summary: str) -> str:
        normalized = "".join(
            unicodedata.normalize("NFKC", summary).casefold().split()
        )
        start = 0
        end = len(normalized)
        while (
            start < end
            and unicodedata.category(normalized[start]).startswith("P")
        ):
            start += 1
        while (
            start < end
            and unicodedata.category(normalized[end - 1]).startswith("P")
        ):
            end -= 1
        return "".join(
            character
            for character in normalized[start:end]
            if (
                not unicodedata.category(character).startswith("P")
                or character in {".", "%", "-", "/"}
            )
        )

    @staticmethod
    def _same_claim_scope(
        candidate: KnowledgeClaimCandidate, existing: KnowledgeClaimCandidate
    ) -> bool:
        return (
            candidate.subject_entity_id,
            candidate.predicate,
            candidate.applies_to_version_slot_id,
            candidate.region,
            candidate.platform,
        ) == (
            existing.subject_entity_id,
            existing.predicate,
            existing.applies_to_version_slot_id,
            existing.region,
            existing.platform,
        )


__all__ = ("AdmissionDecision", "KnowledgeAdmissionPolicy")
