"""Evidence, privacy, conflict, authority, and tombstone memory decisions."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass


_PERSISTENCE_IMPORTANCE_THRESHOLD = 0.2


@dataclass(frozen=True)
class MemoryCandidate:
    persona_id: str
    group_id: str
    subject_id: str | None
    kind: str
    content: str
    evidence_event_ids: tuple[str, ...]
    source_authority: str
    sensitivity: str
    confidence: float
    importance: float
    occurred_at: int


@dataclass(frozen=True)
class MemoryRecord:
    memory_id: str
    persona_id: str
    group_id: str
    subject_id: str | None
    kind: str
    summary: str
    normalized_content: str
    content_hash: str
    evidence_event_ids: tuple[str, ...]
    source_authority: str
    sensitivity: str
    confidence: float
    importance: float
    occurred_at: int
    conflict: bool


@dataclass(frozen=True)
class MemoryDecision:
    outcome: str
    reason_codes: tuple[str, ...]
    record: MemoryRecord | None
    conflicting_memory_ids: tuple[str, ...]


class MemoryPipeline:
    def decide(
        self,
        candidate: MemoryCandidate,
        *,
        existing: tuple[MemoryRecord, ...] = (),
        tombstone_hashes: tuple[str, ...] = (),
    ) -> MemoryDecision:
        self._validate(candidate)
        content_hash = self.content_hash(candidate)
        if content_hash in tombstone_hashes:
            return MemoryDecision("REJECT", ("tombstone_blocked",), None, ())
        if candidate.kind == "user_fact" and candidate.source_authority == "bot_output":
            return MemoryDecision(
                "REJECT", ("bot_output_not_user_evidence",), None, ()
            )
        if (
            candidate.sensitivity == "sensitive"
            and candidate.source_authority != "admin_confirmed"
        ):
            return MemoryDecision("REVIEW", ("sensitive_requires_review",), None, ())
        if candidate.importance < _PERSISTENCE_IMPORTANCE_THRESHOLD:
            return MemoryDecision(
                "REVIEW", ("below_persistence_threshold",), None, ()
            )
        if (
            candidate.source_authority == "model_inference"
            and candidate.confidence < 0.8
        ):
            return MemoryDecision("REVIEW", ("low_authority_inference",), None, ())

        normalized = self.normalize(candidate.content)
        conflicts = tuple(
            item.memory_id
            for item in existing
            if item.persona_id == candidate.persona_id
            and item.group_id == candidate.group_id
            and item.subject_id == candidate.subject_id
            and item.kind == candidate.kind
            and self._is_potential_conflict(item.normalized_content, normalized)
        )
        identity = "\0".join(
            (
                candidate.persona_id,
                candidate.group_id,
                candidate.subject_id or "",
                candidate.kind,
                content_hash,
                ",".join(candidate.evidence_event_ids),
            )
        )
        memory_id = f"memory:{hashlib.sha256(identity.encode()).hexdigest()[:24]}"
        record = MemoryRecord(
            memory_id=memory_id,
            persona_id=candidate.persona_id,
            group_id=candidate.group_id,
            subject_id=candidate.subject_id,
            kind=candidate.kind,
            summary=candidate.content.strip(),
            normalized_content=normalized,
            content_hash=content_hash,
            evidence_event_ids=candidate.evidence_event_ids,
            source_authority=candidate.source_authority,
            sensitivity=candidate.sensitivity,
            confidence=candidate.confidence,
            importance=candidate.importance,
            occurred_at=candidate.occurred_at,
            conflict=bool(conflicts),
        )
        reasons = ("conflict_preserved",) if conflicts else ("evidence_accepted",)
        return MemoryDecision("ACCEPT", reasons, record, conflicts)

    def content_hash(self, candidate: MemoryCandidate) -> str:
        scope = "\0".join(
            (
                candidate.persona_id,
                candidate.group_id,
                candidate.subject_id or "",
                candidate.kind,
                self.normalize(candidate.content),
            )
        )
        return hashlib.sha256(scope.encode()).hexdigest()

    @staticmethod
    def normalize(content: str) -> str:
        return re.sub(r"\s+", " ", content.strip()).casefold()

    @staticmethod
    def _is_potential_conflict(existing: str, candidate: str) -> bool:
        if existing == candidate:
            return False
        existing_chars = set(existing.replace(" ", ""))
        candidate_chars = set(candidate.replace(" ", ""))
        union = existing_chars | candidate_chars
        similarity = len(existing_chars & candidate_chars) / max(1, len(union))
        return similarity >= 0.45

    @staticmethod
    def _validate(candidate: MemoryCandidate) -> None:
        if not candidate.persona_id or not candidate.group_id or not candidate.kind:
            raise ValueError("memory persona, group, and kind are required")
        if not candidate.content.strip() or not candidate.evidence_event_ids:
            raise ValueError("memory content and evidence are required")
        if not 0 <= candidate.confidence <= 1 or not 0 <= candidate.importance <= 1:
            raise ValueError("memory confidence and importance must be between 0 and 1")
        if candidate.sensitivity not in {"normal", "sensitive", "restricted"}:
            raise ValueError("unknown memory sensitivity")


__all__ = ("MemoryCandidate", "MemoryDecision", "MemoryPipeline", "MemoryRecord")
