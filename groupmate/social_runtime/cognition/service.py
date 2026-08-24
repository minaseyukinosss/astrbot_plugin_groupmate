"""Cost-governed orchestration of stateless cognitive workers."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, replace
from typing import Awaitable, Callable, Mapping, TypeVar

from ..attention import AttentionFrame
from .blackboard import CognitionBlackboard, ObservationRejected
from .contracts import (
    CognitiveContext,
    CognitiveObservation,
    CognitiveWorker,
    CognitiveWorkerDiagnostic,
    CognitiveWorkerResult,
)
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


@dataclass(frozen=True)
class _WorkerRun:
    completed: bool
    observations: tuple[CognitiveObservation, ...]
    diagnostic: CognitiveWorkerDiagnostic
    messages: tuple[str, ...] = ()


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
        worker_diagnostics: list[CognitiveWorkerDiagnostic] = []
        rule_run = await self._invoke_worker(
            self.rule_worker,
            frame,
            context,
        )
        rule_completed, rule_diagnostic = self._integrate_worker(
            rule_run, board, diagnostics
        )
        worker_diagnostics.append(rule_diagnostic)

        cost_level = self._cost_level(frame)
        selected: list[tuple[CognitiveWorker, int]] = []
        missing_workers: list[str] = []
        for name in frame.requested_workers:
            worker = self.workers.get(name)
            if worker is not None:
                selected.append((worker, 1))
            else:
                missing_workers.append(name)
        if cost_level >= 3 and self.critic_worker is not None:
            selected.append((self.critic_worker, 2))

        used_calls = 0
        used_cost = 0
        diagnostics.extend(
            f"worker_missing:{name}" for name in missing_workers
        )
        worker_diagnostics.extend(
            self._instant_diagnostic(name, "MISSING", "worker_missing")
            for name in missing_workers
        )
        degraded = not rule_completed or bool(missing_workers)
        admitted: list[CognitiveWorker] = []
        budget_denied: CognitiveWorker | None = None
        for worker, cost in selected:
            if (
                used_calls + 1 > self.budget.max_worker_calls
                or used_cost + cost > self.budget.max_cost_units
            ):
                degraded = True
                budget_denied = worker
                break
            admitted.append(worker)
            used_calls += 1
            used_cost += cost
        runs = await asyncio.gather(
            *(
                self._invoke_worker(worker, frame, context)
                for worker in admitted
            )
        )
        for run in runs:
            completed, worker_diagnostic = self._integrate_worker(
                run, board, diagnostics
            )
            worker_diagnostics.append(worker_diagnostic)
            degraded = degraded or not completed
        if budget_denied is not None:
            diagnostics.append("cognition_budget_exhausted")
            worker_diagnostics.append(
                self._instant_diagnostic(
                    budget_denied.name,
                    "BUDGET_EXHAUSTED",
                    "cognition_budget_exhausted",
                )
            )
        if len(selected) > used_calls:
            degraded = True
        return board.snapshot(
            cost_level=cost_level,
            degraded=degraded,
            diagnostics=tuple(diagnostics),
            worker_diagnostics=tuple(worker_diagnostics),
        )

    async def _invoke_worker(self, worker, frame, context) -> _WorkerRun:
        started_at = int(time.time() * 1000)
        started = time.monotonic_ns()
        try:
            result = await self._worker_gate.run(
                frame.trigger_kind,
                lambda: asyncio.wait_for(
                    self._observe(worker, frame, context),
                    timeout=self.budget.worker_timeout_seconds,
                ),
            )
        except TimeoutError:
            return _WorkerRun(
                completed=False,
                observations=(),
                diagnostic=self._diagnostic(
                    worker.name,
                    "TIMED_OUT",
                    "worker_timeout",
                    started_at,
                    started,
                ),
                messages=(f"worker_timeout:{worker.name}",),
            )
        except Exception as exc:
            code = f"worker_error:{type(exc).__name__}"
            return _WorkerRun(
                completed=False,
                observations=(),
                diagnostic=self._diagnostic(
                    worker.name, "FAILED", code, started_at, started
                ),
                messages=(f"{code}:{worker.name}",),
            )
        observations = result.observations
        if result.diagnostic_code:
            code = str(result.diagnostic_code)
            status = "MODEL_FAILED" if code == "model_call_failed" else "INVALID_OUTPUT"
            return _WorkerRun(
                completed=False,
                observations=(),
                diagnostic=self._diagnostic(
                    worker.name, status, code, started_at, started
                ),
                messages=(f"{code}:{worker.name}",),
            )
        return _WorkerRun(
            completed=True,
            observations=observations,
            diagnostic=self._diagnostic(
                worker.name, "SUCCEEDED", None, started_at, started
            ),
        )

    @staticmethod
    def _integrate_worker(
        run: _WorkerRun,
        board: CognitionBlackboard,
        diagnostics: list[str],
    ) -> tuple[bool, CognitiveWorkerDiagnostic]:
        diagnostics.extend(run.messages)
        if not run.completed:
            return False, run.diagnostic
        rejected = False
        for observation in run.observations:
            try:
                board.add(observation)
            except ObservationRejected as exc:
                rejected = True
                diagnostics.append(
                    f"observation_rejected:{run.diagnostic.worker}:{exc}"
                )
        if rejected:
            return False, replace(
                run.diagnostic,
                status="REJECTED",
                diagnostic_code="observation_rejected",
            )
        return True, run.diagnostic

    @staticmethod
    async def _observe(worker, frame, context) -> CognitiveWorkerResult:
        operation = getattr(worker, "observe_with_result", None)
        if callable(operation):
            result = await operation(frame, context)
            if isinstance(result, CognitiveWorkerResult):
                return result
        return CognitiveWorkerResult(tuple(await worker.observe(frame, context)))

    @staticmethod
    def _diagnostic(worker, status, code, started_at, started) -> CognitiveWorkerDiagnostic:
        completed_at = int(time.time() * 1000)
        latency_ms = max(0, (time.monotonic_ns() - started) // 1_000_000)
        return CognitiveWorkerDiagnostic(
            worker=str(worker),
            status=str(status),
            started_at=int(started_at),
            completed_at=max(int(started_at), completed_at),
            latency_ms=int(latency_ms),
            diagnostic_code=None if code is None else str(code),
        )

    @staticmethod
    def _instant_diagnostic(worker, status, code) -> CognitiveWorkerDiagnostic:
        now = int(time.time() * 1000)
        return CognitiveWorkerDiagnostic(
            worker=str(worker),
            status=str(status),
            started_at=now,
            completed_at=now,
            latency_ms=0,
            diagnostic_code=str(code),
        )

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
