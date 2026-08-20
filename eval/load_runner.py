"""Deterministic virtual-time capacity harness for Social Runtime v2."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
import heapq
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterable, Mapping

from groupmate.social_runtime.attention import (
    AMBIENT_DECISION_BUDGET_SECONDS,
    AttentionFrame,
)
from groupmate.social_runtime.cognition.contracts import CognitiveContext
from groupmate.social_runtime.cognition.scheduling import WorkerAdmissionQueue
from groupmate.social_runtime.cognition.service import CognitionBudget, CognitionService
from groupmate.social_runtime.tasks.contracts import (
    CapabilityDescriptor,
    CapabilityField,
    CapabilityRequest,
    ConfirmationPolicy,
    RiskLevel,
    TaskStatus,
)
from groupmate.social_runtime.tasks.runtime import TaskRuntime

from .report import EvaluationReport


@dataclass(frozen=True)
class BudgetResult:
    observed: int | float
    budget: int | float
    applicable: bool
    passed: bool | None

    def to_dict(self) -> dict[str, object]:
        return {
            "observed": self.observed,
            "budget": self.budget,
            "applicable": self.applicable,
            "pass": self.passed,
        }


@dataclass(frozen=True)
class LoadReport:
    workload: Mapping[str, int]
    event_accounting: Mapping[str, int]
    percentile_algorithm: str
    denominators: Mapping[str, int]
    source_evaluation_facts: Mapping[str, object]
    budgets: Mapping[str, BudgetResult]
    latency_ms: Mapping[str, Mapping[str, int | float]]
    scheduling: Mapping[str, object]
    task_accounting: Mapping[str, int]
    worker_cost_units: int

    def to_dict(self) -> dict[str, object]:
        return {
            "workload": dict(self.workload),
            "event_accounting": dict(self.event_accounting),
            "percentile_algorithm": self.percentile_algorithm,
            "denominators": dict(self.denominators),
            "source_evaluation_facts": dict(self.source_evaluation_facts),
            "budgets": {
                name: self.budgets[name].to_dict()
                for name in sorted(self.budgets)
            },
            "latency_ms": {
                name: dict(self.latency_ms[name]) for name in sorted(self.latency_ms)
            },
            "scheduling": dict(self.scheduling),
            "task_accounting": dict(self.task_accounting),
            "worker_cost_units": self.worker_cost_units,
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )


@dataclass(frozen=True)
class _VirtualJob:
    job_id: str
    lane: str
    arrived_at: int
    duration_ms: int
    cost_units: int
    kind: str


@dataclass(frozen=True)
class _StreamingState:
    ingested: int
    committed: int
    dropped: int
    peak_actor_backlog: int


def nearest_rank_percentiles(values: Iterable[int | float]) -> dict[str, int | float]:
    ordered = sorted(values)
    if not ordered:
        return {"p50": 0, "p95": 0, "p99": 0}

    def percentile(percent: int) -> int | float:
        return ordered[max(0, math.ceil(len(ordered) * percent / 100) - 1)]

    return {"p50": percentile(50), "p95": percentile(95), "p99": percentile(99)}


def run_fake_load(
    *,
    worker_concurrency_limit: int = 12,
    evaluation_reports: Iterable[EvaluationReport] = (),
    faults: Mapping[str, int] | None = None,
    runtime_path: Path | None = None,
) -> LoadReport:
    limit = int(worker_concurrency_limit)
    if limit < 1:
        raise ValueError("worker_concurrency_limit must be positive")
    workload = {
        "groups": 50,
        "messages_per_group_second": 5,
        "virtual_seconds": 1_800,
        "messages": 450_000,
        "concurrent_long_tasks": 10,
    }
    fault_profile = _fault_profile(faults)
    streaming = _stream_workload(workload, fault_profile)
    if runtime_path is None:
        with TemporaryDirectory() as directory:
            task_accounting = _run_long_tasks(Path(directory) / "load-tasks.db")
    else:
        task_accounting = _run_long_tasks(Path(runtime_path))
    gate_evidence = asyncio.run(_exercise_production_gate(limit))
    jobs = _workload_jobs()
    simulation = _simulate(jobs, limit)
    fast_latency = simulation["fast_latency"]
    ambient_latency = simulation["ambient_latency"]
    completion_times = simulation["decision_completion_times"]
    projection_latency = _projection_latencies(
        completion_times,
        capacity_per_second=fault_profile["projection_capacity_per_second"],
    )
    fast_percentiles = nearest_rank_percentiles(fast_latency)
    ambient_percentiles = nearest_rank_percentiles(ambient_latency)
    projection_percentiles = nearest_rank_percentiles(projection_latency)
    worker_cost = simulation["worker_cost_units"]
    cost_rate = round(worker_cost / workload["virtual_seconds"], 6)
    source_facts = _source_evaluation_facts(evaluation_reports)
    unknown_attempts = 0
    unknown_count = 0
    unknown_rate = 0.0
    ambient_total = len(ambient_latency) + simulation["ambient_expired_count"]
    ambient_expired_rate = round(
        simulation["ambient_expired_count"] / ambient_total,
        12,
    )
    budgets = {
        "actor_backlog": _budget(streaming.peak_actor_backlog, 100),
        "worker_concurrency": _budget(simulation["peak_concurrency"], limit),
        "worker_cost_units_per_virtual_second": _budget(cost_rate, 50.0),
        "fast_decision_latency_ms_p95": _budget(fast_percentiles["p95"], 2_500),
        "ambient_decision_latency_ms_p95": _budget(
            ambient_percentiles["p95"], 8_000
        ),
        "ambient_decision_latency_ms_p99": _budget(
            ambient_percentiles["p99"], 8_000
        ),
        "ambient_expired_rate": _budget(ambient_expired_rate, 0.0),
        "projection_lag_ms_p95": _budget(
            projection_percentiles["p95"], 5_000
        ),
        "unknown_delivery_rate": BudgetResult(
            unknown_rate,
            0.001,
            unknown_attempts > 0,
            unknown_rate < 0.001 if unknown_attempts > 0 else None,
        ),
    }
    report = LoadReport(
        workload=workload,
        event_accounting={
            "ingested": streaming.ingested,
            "committed": streaming.committed,
            "dropped": streaming.dropped,
        },
        percentile_algorithm="nearest-rank-ceiling",
        denominators={
            "fast_decision_latency_ms": len(fast_latency),
            "ambient_decision_latency_ms": len(ambient_latency),
            "ambient_expiry": ambient_total,
            "projection_lag_ms": len(projection_latency),
            "unknown_delivery_rate": unknown_attempts,
        },
        source_evaluation_facts=source_facts,
        budgets=budgets,
        latency_ms={
            "fast_decision": fast_percentiles,
            "ambient_decision": ambient_percentiles,
            "projection_lag": projection_percentiles,
        },
        scheduling={
            "direct_starvation_count": sum(
                latency > 2_500 for latency in fast_latency
            ),
            "direct_admitted_ahead_of_ambient": simulation[
                "direct_admitted_ahead_of_ambient"
            ],
            "ambient_expired_count": simulation["ambient_expired_count"],
            "peak_worker_concurrency": simulation["peak_concurrency"],
            "peak_long_task_concurrency": task_accounting["peak_concurrent"],
            "production_gate": gate_evidence,
        },
        task_accounting=task_accounting,
        worker_cost_units=worker_cost,
    )
    return report


def _budget(observed: int | float, budget: int | float) -> BudgetResult:
    return BudgetResult(observed, budget, True, observed <= budget)


def _fault_profile(faults: Mapping[str, int] | None) -> dict[str, int]:
    supplied = dict(faults or {})
    defaults = {
        "drop_every": 0,
        "actor_capacity_per_second": 250,
        "projection_capacity_per_second": 20,
    }
    unknown = sorted(set(supplied) - set(defaults))
    if unknown:
        raise ValueError(f"unsupported load fault: {unknown[0]}")
    profile = {
        name: int(supplied.get(name, value))
        for name, value in defaults.items()
    }
    if profile["drop_every"] < 0:
        raise ValueError("drop_every must not be negative")
    for name in ("actor_capacity_per_second", "projection_capacity_per_second"):
        if profile[name] < 1:
            raise ValueError(f"{name} must be positive")
    return profile


def _stream_workload(
    workload: Mapping[str, int], faults: Mapping[str, int]
) -> _StreamingState:
    groups = int(workload["groups"])
    per_group_second = int(workload["messages_per_group_second"])
    seconds = int(workload["virtual_seconds"])
    actor_backlog = [0] * groups
    ingested = committed = dropped = peak = event_number = 0
    drop_every = int(faults["drop_every"])
    actor_capacity = int(faults["actor_capacity_per_second"])

    for second in range(seconds):
        for group in range(groups):
            for _ in range(per_group_second):
                event_number += 1
                ingested += 1
                if drop_every and event_number % drop_every == 0:
                    dropped += 1
                    continue
                committed += 1
                actor_backlog[group] += 1
                peak = max(peak, actor_backlog[group])
        start_group = second % groups
        for slot in range(actor_capacity):
            group = (start_group + slot) % groups
            if actor_backlog[group]:
                actor_backlog[group] -= 1
    peak = max(peak, max(actor_backlog, default=0))
    return _StreamingState(ingested, committed, dropped, peak)


def _workload_jobs() -> tuple[_VirtualJob, ...]:
    jobs: list[_VirtualJob] = []
    direct_index = 0
    for second in range(1_800):
        for group in range(50):
            if second % 30 == group % 30:
                duration = 300 if direct_index % 97 == 0 else 200
                jobs.append(
                    _VirtualJob(
                        f"fast:{second}:{group}",
                        "FAST",
                        second * 1_000 + group * 2,
                        duration,
                        1,
                        "fast",
                    )
                )
                direct_index += 1
    for window in range(360):
        for group in range(50):
            jobs.append(
                _VirtualJob(
                    f"ambient:{window}:{group}",
                    "AMBIENT",
                    (window + 1) * 5_000 + group * 2,
                    800,
                    2,
                    "ambient",
                )
            )
    return tuple(sorted(jobs, key=lambda item: (item.arrived_at, item.job_id)))


def _run_long_tasks(path: Path) -> dict[str, int]:
    runtime = TaskRuntime(path)
    descriptor = CapabilityDescriptor.create(
        capability_id="load.long_task",
        provider_id="load.fake_provider",
        input_schema=(CapabilityField("task_number", "integer"),),
        output_schema=(CapabilityField("result", "string"),),
        risk_level=RiskLevel.READ_ONLY,
        required_scopes=("load.read",),
        idempotent=True,
        cancellable=True,
        supports_progress=True,
        expected_latency_ms=30_000,
        media_output_kinds=(),
        confirmation_policy=ConfirmationPolicy.NEVER,
    )
    tasks = []
    for index in range(10):
        proposed = runtime.propose(
            descriptor,
            CapabilityRequest.create(
                requester_id="load-user",
                persona_id="load-persona",
                group_id=f"load-group-{index}",
                topic_id=f"load-topic-{index}",
                input_payload={"task_number": index},
                authorization_scopes=("load.read",),
                idempotency_key=f"load-long-task-{index}",
                correlation_id=f"load-long-task:{index}",
                expires_at=1_800,
            ),
            now=0,
        )
        queued = runtime.start(proposed.task_id, now=1)
        tasks.append(runtime.start(queued.task_id, now=2))
    running = sum(task.status is TaskStatus.RUNNING for task in tasks)
    return {"proposed": len(tasks), "running": running, "peak_concurrent": running}


class _GateProbeWorker:
    name = "load-worker"

    def __init__(self, initial_count: int) -> None:
        self.initial_count = initial_count
        self.started: list[str] = []
        self.initial_full = asyncio.Event()
        self.release = asyncio.Event()

    async def observe(self, frame, context):
        del context
        self.started.append(frame.trigger_kind)
        if len(self.started) <= self.initial_count:
            if len(self.started) == self.initial_count:
                self.initial_full.set()
            await self.release.wait()
        return ()


async def _exercise_production_gate(limit: int) -> dict[str, object]:
    worker = _GateProbeWorker(limit)
    service = CognitionService(
        workers={worker.name: worker},
        budget=CognitionBudget(
            max_worker_calls=1,
            max_cost_units=1,
            max_worker_concurrency=limit,
        ),
    )
    tasks = [
        asyncio.create_task(
            service.evaluate(_gate_frame(index, "AMBIENT"), _gate_context(index))
        )
        for index in range(limit)
    ]
    await asyncio.wait_for(worker.initial_full.wait(), timeout=1)
    tasks.extend(
        asyncio.create_task(
            service.evaluate(_gate_frame(index, "AMBIENT"), _gate_context(index))
        )
        for index in range(limit, limit + 2)
    )
    await _wait_for_gate_queue(service, 2)
    tasks.append(
        asyncio.create_task(
            service.evaluate(
                _gate_frame(limit + 2, "FAST"), _gate_context(limit + 2)
            )
        )
    )
    await _wait_for_gate_queue(service, 3)
    worker.release.set()
    await asyncio.gather(*tasks)
    return {
        "submitted": len(tasks),
        "peak_worker_concurrency": service.peak_worker_concurrency,
        "direct_started_before_queued_ambient": (
            worker.started[limit] == "FAST"
        ),
    }


async def _wait_for_gate_queue(service: CognitionService, count: int) -> None:
    async def wait_until_ready() -> None:
        while service.waiting_worker_count < count:
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_until_ready(), timeout=1)


def _gate_frame(index: int, lane: str) -> AttentionFrame:
    return AttentionFrame(
        frame_id=f"load-gate:{index}",
        group_id=f"load-group-{index}",
        scene_version=1,
        trigger_kind=lane,
        focus_topic_ids=(f"topic-{index}",),
        focus_event_ids=(f"event-{index}",),
        candidate_audiences=(f"user-{index}",),
        urgency="high" if lane == "FAST" else "normal",
        deadline=0,
        requested_workers=("load-worker",),
        persona_state_version=1,
        config_version=1,
    )


def _gate_context(index: int) -> CognitiveContext:
    return CognitiveContext.create(
        group_id=f"load-group-{index}",
        scene_version=1,
        persona_state_version=1,
        config_version=1,
        now=0,
        focus_events=({"event_id": f"event-{index}"},),
        world_summary={"load_probe": True},
        constraints=("no_side_effects",),
        token_budget=1,
    )


def _simulate(jobs: tuple[_VirtualJob, ...], limit: int) -> dict[str, object]:
    waiting: WorkerAdmissionQueue[_VirtualJob] = WorkerAdmissionQueue()
    running: list[tuple[int, int, _VirtualJob]] = []
    next_job = 0
    current = 0
    run_sequence = 0
    peak = 0
    fast_latency: list[int] = []
    ambient_latency: list[int] = []
    decision_completion_times: list[int] = []
    direct_ahead = 0
    ambient_expired = 0
    worker_cost_units = 0

    while next_job < len(jobs) or running or len(waiting):
        if not running and not len(waiting) and next_job < len(jobs):
            current = jobs[next_job].arrived_at
        while next_job < len(jobs) and jobs[next_job].arrived_at <= current:
            job = jobs[next_job]
            waiting.enqueue(job.lane, job)
            next_job += 1
        while running and running[0][0] <= current:
            finished_at, _, job = heapq.heappop(running)
            latency = finished_at - job.arrived_at
            if job.kind == "fast":
                fast_latency.append(latency)
                decision_completion_times.append(finished_at)
            elif job.kind == "ambient":
                ambient_latency.append(latency)
                decision_completion_times.append(finished_at)
        while len(running) < limit and len(waiting):
            admission = waiting.dequeue()
            job = admission.payload
            if (
                job.kind == "ambient"
                and current + job.duration_ms
                > job.arrived_at + AMBIENT_DECISION_BUDGET_SECONDS * 1_000
            ):
                ambient_expired += 1
                continue
            if job.kind == "fast" and waiting.has_lane("AMBIENT"):
                direct_ahead += 1
            finish = current + job.duration_ms
            heapq.heappush(running, (finish, run_sequence, job))
            run_sequence += 1
            worker_cost_units += job.cost_units
            peak = max(peak, len(running))
        candidates = []
        if running:
            candidates.append(running[0][0])
        if next_job < len(jobs):
            candidates.append(jobs[next_job].arrived_at)
        if candidates:
            next_time = min(value for value in candidates if value >= current)
            if next_time == current and running and running[0][0] > current:
                next_time = running[0][0]
            current = next_time

    return {
        "fast_latency": tuple(fast_latency),
        "ambient_latency": tuple(ambient_latency),
        "decision_completion_times": tuple(decision_completion_times),
        "peak_concurrency": peak,
        "direct_admitted_ahead_of_ambient": direct_ahead,
        "ambient_expired_count": ambient_expired,
        "worker_cost_units": worker_cost_units,
    }


def _projection_latencies(
    completion_times: Iterable[int], *, capacity_per_second: int
) -> tuple[int, ...]:
    completions = tuple(sorted(completion_times))
    waiting: deque[int] = deque()
    latencies = []
    next_completion = 0
    second = 0
    while next_completion < len(completions) or waiting:
        projected_at = (second + 1) * 1_000
        while (
            next_completion < len(completions)
            and completions[next_completion] < projected_at
        ):
            waiting.append(completions[next_completion])
            next_completion += 1
        for _ in range(min(capacity_per_second, len(waiting))):
            committed_at = waiting.popleft()
            latencies.append(projected_at - committed_at)
        second += 1
    return tuple(latencies)


def _source_evaluation_facts(
    reports: Iterable[EvaluationReport],
) -> dict[str, object]:
    values = tuple(reports)
    if any(not isinstance(report, EvaluationReport) for report in values):
        raise ValueError("evaluation_reports must contain EvaluationReport values")
    return {
        "reports": len(values),
        "latency_ms": [dict(report.latency_ms) for report in values],
        "cost": [dict(report.cost) for report in values],
        "safety_issue_counts": [len(report.safety.issues) for report in values],
    }


__all__ = (
    "BudgetResult",
    "LoadReport",
    "nearest_rank_percentiles",
    "run_fake_load",
)
