"""Scoped relationship memories derived only from accepted relationship events."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace

from ..society.relationship_events import RelationshipEventDecision
from ..society.relationships import RelationshipStage


_MEMORY_WORTHY = frozenset(
    {
        "warm_exchange",
        "trust_confirmed",
        "play_accepted",
        "reliable_help",
        "care_permission",
        "boundary_pressure",
        "repair_attempt",
        "repair_confirmed",
    }
)
_RELATIONAL_REQUESTS = (
    "陪我",
    "理我",
    "帮我",
    "喜欢我",
    "讨厌我",
    "原谅",
    "道歉",
    "对不起",
    "上次",
    "之前",
    "还记得",
    "又来",
)
_PRIVATE_NUMBER = re.compile(r"\d{5,}")


@dataclass(frozen=True)
class RelationshipMemory:
    memory_id: str
    relationship_event_id: str
    persona_id: str
    group_id: str
    subject_id: str
    kind: str
    summary: str
    sensitivity: str
    confidence: float
    severity: str
    occurred_at: int
    resolved_at: int | None = None
    resolved_by: str | None = None

    def resolve(self, *, at: int, by: str) -> "RelationshipMemory":
        if self.resolved_at is not None:
            return self
        return replace(self, resolved_at=max(0, int(at)), resolved_by=str(by))


def relationship_memory_from_decision(
    decision: RelationshipEventDecision,
) -> RelationshipMemory | None:
    if decision.outcome != "ACCEPT":
        return None
    proposal = decision.proposal
    if proposal.kind not in _MEMORY_WORTHY:
        return None
    if proposal.severity == "minor" and proposal.kind not in {
        "trust_confirmed",
        "reliable_help",
        "repair_confirmed",
    }:
        return None
    identity = "\0".join(
        (
            proposal.persona_id,
            proposal.group_id,
            proposal.subject_id,
            proposal.event_id,
        )
    )
    return RelationshipMemory(
        memory_id=f"relationship-memory:{hashlib.sha256(identity.encode()).hexdigest()[:24]}",
        relationship_event_id=proposal.event_id,
        persona_id=proposal.persona_id,
        group_id=proposal.group_id,
        subject_id=proposal.subject_id,
        kind=proposal.kind,
        summary=" ".join(proposal.summary.split())[:160],
        sensitivity=proposal.sensitivity,
        confidence=proposal.confidence,
        severity=proposal.severity,
        occurred_at=proposal.occurred_at,
    )


class RelationshipMemorySelector:
    """Return at most two safe cues that are relevant to the current message."""

    _BOUNDARY_KINDS = frozenset({"boundary_pressure"})
    _REPAIR_KINDS = frozenset({"repair_attempt", "repair_confirmed"})

    def select(
        self,
        records: tuple[RelationshipMemory, ...],
        *,
        text: str,
        stage: RelationshipStage,
        now: int,
    ) -> tuple[str, ...]:
        normalized_text = " ".join(str(text or "").split())[:240]
        relation_request = any(
            marker in normalized_text for marker in _RELATIONAL_REQUESTS
        )
        ranked: list[tuple[float, RelationshipMemory]] = []
        for record in records:
            if (
                record.resolved_at is not None
                or record.sensitivity != "normal"
                or record.confidence < 0.82
                or not record.summary
                or _PRIVATE_NUMBER.search(record.summary)
            ):
                continue
            overlap = self._overlap(normalized_text, record.summary)
            relevant = overlap > 0.18
            if (
                record.kind in self._BOUNDARY_KINDS
                and stage in {RelationshipStage.DISTANT, RelationshipStage.GUARDED}
                and relation_request
            ):
                relevant = True
                overlap += 0.6
            if record.kind in self._REPAIR_KINDS and relation_request:
                relevant = True
                overlap += 0.3
            if not relevant:
                continue
            age_days = max(0, int(now) - record.occurred_at) / 86400
            recency = 1.0 / (1.0 + age_days)
            ranked.append((overlap + recency + record.confidence, record))
        ranked.sort(key=lambda item: (-item[0], -item[1].occurred_at, item[1].memory_id))
        return tuple(self._cue(record) for _, record in ranked[:2])

    @staticmethod
    def _cue(record: RelationshipMemory) -> str:
        if record.kind == "boundary_pressure":
            prefix = "未修复边界事件"
        elif record.kind in {"repair_attempt", "repair_confirmed"}:
            prefix = "关系修复记忆"
        else:
            prefix = "正向关系记忆"
        return f"{prefix}：{record.summary}"

    @staticmethod
    def _overlap(text: str, summary: str) -> float:
        def grams(value: str) -> set[str]:
            chars = [item for item in value.casefold() if item.isalnum()]
            return {
                "".join(chars[index : index + 2])
                for index in range(max(0, len(chars) - 1))
            }

        query = grams(text)
        memory = grams(summary)
        if not query or not memory:
            return 0.0
        return len(query & memory) / max(1, min(len(query), len(memory)))


__all__ = (
    "RelationshipMemory",
    "RelationshipMemorySelector",
    "relationship_memory_from_decision",
)
