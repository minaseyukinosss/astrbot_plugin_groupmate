"""Memory authority 仲裁：冲突、tombstone、supersede。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Sequence
from uuid import uuid4

from ..models import (
    CandidateStatus,
    MemoryCandidate,
    MemoryItem,
    MemoryKind,
    MemoryScope,
    MemoryStatus,
    Sensitivity,
)
from .privacy import claim_hash


@dataclass(frozen=True)
class ArbiterDecision:
    status: CandidateStatus
    reason: str
    memory: Optional[MemoryItem] = None
    superseded_memory_id: Optional[str] = None


class MemoryArbiter:
    """比较 authority；tombstone 命中则拒绝；低权威不覆盖高权威。"""

    OVERLAP_THRESHOLD = 0.45

    def decide(
        self,
        candidate: MemoryCandidate,
        *,
        existing: Sequence[MemoryItem],
        has_tombstone: bool,
        now: int,
        authority: int,
        importance: float = 0.6,
    ) -> ArbiterDecision:
        if has_tombstone:
            return ArbiterDecision(
                CandidateStatus.REJECTED, "tombstone_blocks_replay"
            )
        if candidate.sensitivity is not Sensitivity.NONE:
            return ArbiterDecision(
                CandidateStatus.REJECTED,
                "sensitivity:" + candidate.sensitivity.value,
            )
        claim = (candidate.claim or "").strip()
        if not claim:
            return ArbiterDecision(CandidateStatus.REJECTED, "empty_claim")

        conflict = self._find_conflict(candidate, existing)
        if conflict is not None:
            if int(authority) < int(conflict.authority):
                return ArbiterDecision(
                    CandidateStatus.CONFLICTED,
                    "lower_authority_than:{}".format(conflict.memory_id),
                )
            memory = self._to_memory(
                candidate,
                now=now,
                authority=authority,
                importance=importance,
                supersedes_memory_id=conflict.memory_id,
            )
            return ArbiterDecision(
                CandidateStatus.ACCEPTED,
                "supersede:{}".format(conflict.memory_id),
                memory=memory,
                superseded_memory_id=conflict.memory_id,
            )

        memory = self._to_memory(
            candidate,
            now=now,
            authority=authority,
            importance=importance,
            supersedes_memory_id=None,
        )
        return ArbiterDecision(CandidateStatus.ACCEPTED, "accepted", memory=memory)

    def _find_conflict(
        self,
        candidate: MemoryCandidate,
        existing: Sequence[MemoryItem],
    ) -> Optional[MemoryItem]:
        candidate_hash = candidate.claim_hash or claim_hash(candidate.claim)
        best: Optional[MemoryItem] = None
        best_score = 0.0
        for item in existing:
            if item.status is not MemoryStatus.ACCEPTED:
                continue
            if item.group_id != candidate.group_id:
                continue
            if item.subject_id != candidate.subject_id:
                continue
            if item.scope != candidate.scope:
                continue
            item_hash = claim_hash(item.text)
            if item_hash == candidate_hash:
                return item
            score = self._overlap(candidate.claim, item.text)
            if score >= self.OVERLAP_THRESHOLD and score > best_score:
                best = item
                best_score = score
        return best

    @staticmethod
    def _overlap(left: str, right: str) -> float:
        left_grams = _char_ngrams(left)
        right_grams = _char_ngrams(right)
        if not left_grams or not right_grams:
            return 0.0
        return len(left_grams & right_grams) / max(1, len(left_grams))

    @staticmethod
    def _to_memory(
        candidate: MemoryCandidate,
        *,
        now: int,
        authority: int,
        importance: float,
        supersedes_memory_id: Optional[str],
    ) -> MemoryItem:
        source_ids = tuple(
            str(item) for item in candidate.source_message_ids if str(item).strip()
        )
        return MemoryItem(
            memory_id=str(uuid4()),
            group_id=candidate.group_id,
            subject_id=candidate.subject_id,
            kind=candidate.kind
            if isinstance(candidate.kind, MemoryKind)
            else MemoryKind.EPISODIC,
            text=candidate.claim.strip(),
            created_at=int(now),
            expires_at=candidate.proposed_expires_at,
            confidence=max(0.0, min(1.0, float(candidate.confidence))),
            importance=max(0.0, min(1.0, float(importance))),
            authority=max(0, int(authority)),
            source_message_id=source_ids[0] if source_ids else None,
            status=MemoryStatus.ACCEPTED,
            scope=candidate.scope
            if isinstance(candidate.scope, MemoryScope)
            else MemoryScope.USER_IN_GROUP,
            sensitivity=candidate.sensitivity
            if isinstance(candidate.sensitivity, Sensitivity)
            else Sensitivity.NONE,
            extractor_version=candidate.extractor_version or "rules-v1",
            supersedes_memory_id=supersedes_memory_id,
            source_message_ids=source_ids,
        )


def _char_ngrams(text: str, size: int = 3) -> set:
    cleaned = re.sub(r"\s+", "", (text or "").lower())
    if len(cleaned) < size:
        return set(cleaned) if cleaned else set()
    return {
        cleaned[index : index + size] for index in range(len(cleaned) - size + 1)
    }
