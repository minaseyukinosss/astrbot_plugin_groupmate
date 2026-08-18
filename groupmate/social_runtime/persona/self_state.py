"""Code-owned evidence policy for bounded global self-state effects."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from ..contracts import GlobalSelfState, GlobalStateEffect


@dataclass(frozen=True)
class StateEvidence:
    kind: str
    event_id: str
    occurred_at: int
    amount: int = 1


class SelfStatePolicy:
    COOLDOWN_SECONDS = 30
    NEGATIVE_EVIDENCE_THRESHOLD = 3

    def propose(
        self,
        state: GlobalSelfState,
        evidence: tuple[StateEvidence, ...],
        *,
        now: int,
    ) -> tuple[GlobalStateEffect, ...]:
        now = int(now)
        if state.last_transition_at and now - state.last_transition_at < self.COOLDOWN_SECONDS:
            return ()
        unique = self._unique(evidence)

        workload = tuple(item for item in unique if item.kind == "workload")
        if workload:
            amount = max(-50, min(50, sum(item.amount for item in workload)))
            return (self._effect(state, workload, "cognitive_load_delta", amount),)

        negative = tuple(
            item
            for item in unique
            if item.kind == "negative_interaction" and now - item.occurred_at <= 300
        )
        if len(negative) >= self.NEGATIVE_EVIDENCE_THRESHOLD:
            selected = negative[: self.NEGATIVE_EVIDENCE_THRESHOLD]
            amount = max(1, min(10, sum(item.amount for item in selected)))
            return (self._effect(state, selected, "irritation_delta", amount),)

        # A reaction or the absence of a reply is never sufficient evidence.
        return ()

    def decay(
        self, state: GlobalSelfState, *, now: int
    ) -> tuple[GlobalStateEffect, ...]:
        if int(now) - state.last_transition_at < 300:
            return ()
        if state.cognitive_load > 0:
            evidence = (
                StateEvidence("time_decay", f"clock:{int(now) // 300}", int(now)),
            )
            return (
                self._effect(
                    state,
                    evidence,
                    "cognitive_load_delta",
                    -min(10, state.cognitive_load),
                ),
            )
        if state.irritation != 0:
            evidence = (
                StateEvidence("time_decay", f"clock:{int(now) // 300}", int(now)),
            )
            amount = (
                -min(10, state.irritation)
                if state.irritation > 0
                else min(10, -state.irritation)
            )
            return (self._effect(state, evidence, "irritation_delta", amount),)
        return ()

    @staticmethod
    def _unique(evidence: tuple[StateEvidence, ...]) -> tuple[StateEvidence, ...]:
        seen = set()
        result = []
        for item in evidence:
            if item.event_id and item.event_id not in seen:
                seen.add(item.event_id)
                result.append(item)
        return tuple(result)

    @staticmethod
    def _effect(
        state: GlobalSelfState,
        evidence: tuple[StateEvidence, ...],
        kind: str,
        amount: int,
    ) -> GlobalStateEffect:
        event_ids = tuple(item.event_id for item in evidence)
        identity = {
            "persona_id": state.persona_id,
            "expected_version": state.version,
            "kind": kind,
            "amount": amount,
            "evidence": event_ids,
        }
        digest = hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:24]
        return GlobalStateEffect(
            effect_id=f"state-effect:{digest}",
            source_event_id=event_ids[-1],
            expected_version=state.version,
            kind=kind,
            amount=int(amount),
            evidence_event_ids=event_ids,
        )


__all__ = ("GlobalSelfState", "SelfStatePolicy", "StateEvidence")
