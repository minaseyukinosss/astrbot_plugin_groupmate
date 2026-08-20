"""Cost-governed orchestration of stateless cognitive workers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable, Mapping, TypeVar

from ..attention import AttentionFrame
from .blackboard import CognitionBlackboard, ObservationRejected
from .contracts import CognitiveContext, CognitiveObservation, CognitiveWorker
from .scheduling import WorkerAdmissionQueue


T = TypeVar("T")


@dataclass(frozen=True)
class CognitionBudget:
    max_worker_calls: int
    max_cost_units: int
    worker_timeout_seconds: float = 10.0
    max_worker_concurrency: int = 12

    def __post_init__(self) -> None:
        if self.max_worker_calls < 0 or self.max_cost_units < 0:
            raise ValueError("cognition budget must not be negative")
        if self.worker_timeout_seconds <= 0:
            raise ValueError("worker timeout must be positive")
        if self.max_worker_concurrency < 1:
            raise ValueError("worker concurrency must be positive")


class _WorkerConcurrencyGate:
    def __init__(self, limit: int) -> None:
        self.limit = int(limit)
        self.active = 0
        self.peak = 0
        self._condition = asyncio.Condition()
        self._queue: WorkerAdmissionQueue[object] = WorkerAdmissionQueue()

    @property
    def waiting(self) -> int:
        return len(self._queue)

    async def run(self, lane: str, operation: Callable[[], Awaitable[T]]) -> T:
        token = object()
        async with self._condition:
            admission = self._queue.enqueue(lane, token)
            try:
                while self.active >= self.limit or self._queue.peek() != admission:
                    await self._condition.wait()
            except BaseException:
                self._queue.discard(admission)
                self._condition.notify_all()
                raise
            self._queue.dequeue()
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            return await operation()
        finally:
            async with self._condition:
                self.active -= 1
                self._condition.notify_all()


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
        self._worker_gate = _WorkerConcurrencyGate(budget.max_worker_concurrency)

    @property
    def peak_worker_concurrency(self) -> int:
        return self._worker_gate.peak

    @property
    def active_worker_count(self) -> int:
        return self._worker_gate.active

    @property
    def waiting_worker_count(self) -> int:
        return self._worker_gate.waiting

    async def evaluate(self, frame: AttentionFrame, context: CognitiveContext):
        self._validate_context(frame, context)
        board = CognitionBlackboard(frame, now=context.now)
        diagnostics: list[str] = []
        rule_completed = await self._run_worker(
            self.rule_worker,
            frame,
            context,
            board,
            diagnostics,
        )

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
        degraded = not rule_completed
        for worker, cost in selected:
            if (
                used_calls + 1 > self.budget.max_worker_calls
                or used_cost + cost > self.budget.max_cost_units
            ):
                degraded = True
                diagnostics.append("cognition_budget_exhausted")
                break
            completed = await self._run_worker(
                worker,
                frame,
                context,
                board,
                diagnostics,
            )
            degraded = degraded or not completed
            used_calls += 1
            used_cost += cost
        if len(selected) > used_calls:
            degraded = True
        return board.snapshot(
            cost_level=cost_level,
            degraded=degraded,
            diagnostics=tuple(diagnostics),
        )

    async def _run_worker(
        self, worker, frame, context, board, diagnostics
    ) -> bool:
        try:
            observations = await self._worker_gate.run(
                frame.trigger_kind,
                lambda: asyncio.wait_for(
                    worker.observe(frame, context),
                    timeout=self.budget.worker_timeout_seconds,
                ),
            )
        except TimeoutError:
            diagnostics.append(f"worker_timeout:{worker.name}")
            return False
        except Exception as exc:
            diagnostics.append(f"worker_error:{worker.name}:{type(exc).__name__}")
            return False
        for observation in observations:
            try:
                board.add(observation)
            except ObservationRejected as exc:
                diagnostics.append(f"observation_rejected:{worker.name}:{exc}")
        return True

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
