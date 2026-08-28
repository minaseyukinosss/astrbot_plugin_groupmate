"""Pure evidence-admission decisions for bounded game knowledge claims."""

from __future__ import annotations

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
        comparable = tuple(
            item
            for item in existing_items
            if self._same_claim_scope(candidate, item)
        )

        official_existing = tuple(
            item
            for item in comparable
            if item.evidence_level is EvidenceLevel.OFFICIAL
        )
        if candidate.evidence_level is EvidenceLevel.OFFICIAL:
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
                item.safe_summary != candidate.safe_summary
                and item.checked_at == candidate.checked_at
                for item in official_existing
            ):
                return AdmissionDecision("dispute", "equal_evidence_conflict")
            if any(
                item.evidence_level is not EvidenceLevel.OFFICIAL
                for item in comparable
            ):
                return AdmissionDecision(
                    "activate", "official_release_outranks_nonofficial"
                )

        if any(
            item.evidence_level is candidate.evidence_level
            and item.safe_summary != candidate.safe_summary
            for item in comparable
        ):
            return AdmissionDecision("dispute", "equal_evidence_conflict")

        if candidate.evidence_level is EvidenceLevel.BUNDLED:
            if candidate.claim_kind.value == "stable_semantic":
                return AdmissionDecision("activate", "bundled_stable_semantic")
            return AdmissionDecision(
                "reject", "bundled_requires_stable_semantic"
            )
        if candidate.evidence_level is EvidenceLevel.OFFICIAL:
            if candidate.claim_kind.value == "rumor":
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
