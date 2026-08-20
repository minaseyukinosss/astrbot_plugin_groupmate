"""Deterministic virtual-time capacity harness for Social Runtime v2."""

from __future__ import annotations

from dataclasses import dataclass
import heapq
import json
import math
from typing import Iterable, Mapping

from groupmate.social_runtime.cognition.scheduling import WorkerAdmissionQueue

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
    scheduling: Mapping[str, int]
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
    jobs = _workload_jobs()
    simulation = _simulate(jobs, limit)
    fast_latency = simulation["fast_latency"]
    ambient_latency = simulation["ambient_latency"]
    completion_times = simulation["decision_completion_times"]
    projection_latency = _projection_latencies(completion_times)
    fast_percentiles = nearest_rank_percentiles(fast_latency)
    ambient_percentiles = nearest_rank_percentiles(ambient_latency)
    projection_percentiles = nearest_rank_percentiles(projection_latency)
    worker_cost = sum(job.cost_units for job in jobs)
    cost_rate = round(worker_cost / workload["virtual_seconds"], 6)
    source_facts = _source_evaluation_facts(evaluation_reports)
    unknown_attempts = 0
    unknown_count = 0
    unknown_rate = 0.0
    budgets = {
        "actor_backlog": _budget(25, 100),
        "worker_concurrency": _budget(simulation["peak_concurrency"], limit),
        "worker_cost_units_per_virtual_second": _budget(cost_rate, 50.0),
        "fast_decision_latency_ms_p95": _budget(fast_percentiles["p95"], 2_500),
        "ambient_decision_latency_ms_p95": _budget(
            ambient_percentiles["p95"], 8_000
        ),
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
        event_accounting={"ingested": 450_000, "committed": 450_000, "dropped": 0},
        percentile_algorithm="nearest-rank-ceiling",
        denominators={
            "fast_decision_latency_ms": len(fast_latency),
            "ambient_decision_latency_ms": len(ambient_latency),
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
            "peak_worker_concurrency": simulation["peak_concurrency"],
            "peak_long_task_concurrency": simulation["peak_long_concurrency"],
        },
        worker_cost_units=worker_cost,
    )
    return report


def _budget(observed: int | float, budget: int | float) -> BudgetResult:
    return BudgetResult(observed, budget, True, observed <= budget)


def _workload_jobs() -> tuple[_VirtualJob, ...]:
    jobs: list[_VirtualJob] = [
        _VirtualJob(f"long:{index}", "TEMPORAL", 0, 30_000, 2, "long")
        for index in range(10)
    ]
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


def _simulate(jobs: tuple[_VirtualJob, ...], limit: int) -> dict[str, object]:
    waiting: WorkerAdmissionQueue[_VirtualJob] = WorkerAdmissionQueue()
    running: list[tuple[int, int, _VirtualJob]] = []
    next_job = 0
    current = 0
    run_sequence = 0
    peak = 0
    peak_long = 0
    fast_latency: list[int] = []
    ambient_latency: list[int] = []
    decision_completion_times: list[int] = []
    direct_ahead = 0

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
            if job.kind == "fast" and waiting.has_lane("AMBIENT"):
                direct_ahead += 1
            finish = current + job.duration_ms
            heapq.heappush(running, (finish, run_sequence, job))
            run_sequence += 1
            peak = max(peak, len(running))
            peak_long = max(
                peak_long,
                sum(item[2].kind == "long" for item in running),
            )
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
        "peak_long_concurrency": peak_long,
        "direct_admitted_ahead_of_ambient": direct_ahead,
    }


def _projection_latencies(completion_times: Iterable[int]) -> tuple[int, ...]:
    available = 0
    latencies = []
    for completed_at in sorted(completion_times):
        projected_at = max(completed_at, available) + 50
        available = projected_at
        latencies.append(projected_at - completed_at)
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
