"""Cost-governed orchestration of stateless cognitive workers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ..attention import AttentionFrame
from .blackboard import CognitionBlackboard, ObservationRejected
from .contracts import CognitiveContext, CognitiveObservation, CognitiveWorker


@dataclass(frozen=True)
class CognitionBudget:
    max_worker_calls: int
    max_cost_units: int

    def __post_init__(self) -> None:
        if self.max_worker_calls < 0 or self.max_cost_units < 0:
            raise ValueError("cognition budget must not be negative")


class LevelZeroRuleWorker:
    name = "level0.rules"

    async def observe(
        self, frame: AttentionFrame, context: CognitiveContext
    ) -> tuple[CognitiveObservation, ...]:
        if not frame.focus_event_ids:
            return ()
        observations = []
        if frame.candidate_audiences:
            observations.append(
                CognitiveObservation.create(
                    worker=self.name,
                    kind="fact.target",
                    proposition={
                        "subject_id": frame.candidate_audiences[0],
                        "value": "platform_explicit",
                    },
                    confidence=1.0,
                    evidence_event_ids=(frame.focus_event_ids[0],),
                    scene_version=frame.scene_version,
                    expires_at=context.now + 30,
                    uncertainty=(),
                )
            )
        if frame.urgency == "critical":
            observations.append(
                CognitiveObservation.create(
                    worker=self.name,
                    kind="fact.safety",
                    proposition={"value": "hard_review_required"},
                    confidence=1.0,
                    evidence_event_ids=(frame.focus_event_ids[0],),
                    scene_version=frame.scene_version,
                    expires_at=context.now + 30,
                    uncertainty=(),
                )
            )
        return tuple(observations)


class CognitionService:
    def __init__(
        self,
        *,
        workers: Mapping[str, CognitiveWorker],
        budget: CognitionBudget,
        rule_worker: CognitiveWorker | None = None,
        critic_worker: CognitiveWorker | None = None,
    ) -> None:
        self.workers = dict(workers)
        self.budget = budget
        self.rule_worker = rule_worker or LevelZeroRuleWorker()
        self.critic_worker = critic_worker

    async def evaluate(self, frame: AttentionFrame, context: CognitiveContext):
        self._validate_context(frame, context)
        board = CognitionBlackboard(frame, now=context.now)
        diagnostics: list[str] = []
        await self._run_worker(self.rule_worker, frame, context, board, diagnostics)

        cost_level = self._cost_level(frame)
        selected: list[tuple[CognitiveWorker, int]] = []
        for name in frame.requested_workers:
            worker = self.workers.get(name)
            if worker is not None:
                selected.append((worker, 1))
            if cost_level == 1 and selected:
                break
        if cost_level >= 3 and self.critic_worker is not None:
            selected.append((self.critic_worker, 2))

        used_calls = 0
        used_cost = 0
        degraded = False
        for worker, cost in selected:
            if (
                used_calls + 1 > self.budget.max_worker_calls
                or used_cost + cost > self.budget.max_cost_units
            ):
                degraded = True
                diagnostics.append("cognition_budget_exhausted")
                break
            await self._run_worker(worker, frame, context, board, diagnostics)
            used_calls += 1
            used_cost += cost
        if len(selected) > used_calls:
            degraded = True
        return board.snapshot(
            cost_level=cost_level,
            degraded=degraded,
            diagnostics=tuple(diagnostics),
        )

    @staticmethod
    async def _run_worker(worker, frame, context, board, diagnostics) -> None:
        try:
            observations = await worker.observe(frame, context)
        except Exception as exc:
            diagnostics.append(f"worker_error:{worker.name}:{type(exc).__name__}")
            return
        for observation in observations:
            try:
                board.add(observation)
            except ObservationRejected as exc:
                diagnostics.append(f"observation_rejected:{worker.name}:{exc}")

    @staticmethod
    def _cost_level(frame: AttentionFrame) -> int:
        if frame.urgency == "critical":
            return 3
        if frame.trigger_kind == "AMBIENT" and len(frame.focus_topic_ids) > 1:
            return 2
        return 1

    @staticmethod
    def _validate_context(frame: AttentionFrame, context: CognitiveContext) -> None:
        expected = (
            frame.group_id,
            frame.scene_version,
            frame.persona_state_version,
            frame.config_version,
        )
        actual = (
            context.group_id,
            context.scene_version,
            context.persona_state_version,
            context.config_version,
        )
        if actual != expected:
            raise ValueError("cognitive context does not match frozen frame versions")


__all__ = ("CognitionBudget", "CognitionService", "LevelZeroRuleWorker")
