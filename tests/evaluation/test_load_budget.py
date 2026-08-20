from __future__ import annotations

import asyncio

import pytest

from eval.report import EvaluationReport
from eval.safety import SafetyReport
from groupmate.settings import SocialRuntimeSettings
from groupmate.social_runtime.attention import AttentionFrame
from groupmate.social_runtime.cognition.contracts import (
    CognitiveContext,
    CognitiveObservation,
)
from groupmate.social_runtime.cognition.service import (
    CognitionBudget,
    CognitionService,
)
from groupmate.social_runtime.persistence.schema import connect_database


def _frame(index: int, *, trigger_kind: str = "FAST") -> AttentionFrame:
    return AttentionFrame(
        frame_id=f"attention:load:{index}",
        group_id=f"group-{index}",
        scene_version=1,
        trigger_kind=trigger_kind,
        focus_topic_ids=(f"topic-{index}",),
        focus_event_ids=(f"event-{index}",),
        candidate_audiences=(f"user-{index}",),
        urgency="high" if trigger_kind == "FAST" else "normal",
        deadline=100,
        requested_workers=("load-worker",),
        persona_state_version=1,
        config_version=1,
    )


def _context(index: int) -> CognitiveContext:
    return CognitiveContext.create(
        group_id=f"group-{index}",
        scene_version=1,
        persona_state_version=1,
        config_version=1,
        now=100,
        focus_events=({"event_id": f"event-{index}"},),
        world_summary={"load": True},
        constraints=("no_side_effects",),
        token_budget=128,
    )


def _evaluation_report() -> EvaluationReport:
    return EvaluationReport(
        lanes={},
        excluded_unknown_count=0,
        latency_ms={"count": 2, "mean": 150.0, "p95": 200.0},
        cost={"tokens": 30, "usd": 0.125},
        safety=SafetyReport(()),
        model_facts=(),
        kind="frozen_shadow",
        production_readiness_eligible=True,
        readiness_reason="fixture",
        candidate_digest="0" * 64,
    )


def test_fake_load_report_is_exact_machine_readable_and_bitwise_deterministic():
    from eval.load_runner import run_fake_load

    first = run_fake_load(evaluation_reports=(_evaluation_report(),))
    second = run_fake_load(evaluation_reports=(_evaluation_report(),))
    payload = first.to_dict()

    assert first.to_json() == second.to_json()
    assert payload["workload"] == {
        "groups": 50,
        "messages_per_group_second": 5,
        "virtual_seconds": 1_800,
        "messages": 450_000,
        "concurrent_long_tasks": 10,
    }
    assert payload["event_accounting"] == {
        "ingested": 450_000,
        "committed": 450_000,
        "dropped": 0,
    }
    assert payload["percentile_algorithm"] == "nearest-rank-ceiling"
    assert payload["denominators"] == {
        "fast_decision_latency_ms": 3_000,
        "ambient_decision_latency_ms": 18_000,
        "ambient_expiry": 18_000,
        "projection_lag_ms": 21_000,
        "unknown_delivery_rate": 0,
    }
    assert payload["source_evaluation_facts"] == {
        "reports": 1,
        "latency_ms": [{"count": 2, "mean": 150.0, "p95": 200.0}],
        "cost": [{"tokens": 30, "usd": 0.125}],
        "safety_issue_counts": [0],
    }
    assert "lanes" not in payload["source_evaluation_facts"]


def test_every_public_budget_exposes_observed_budget_applicability_and_verdict():
    from eval.load_runner import run_fake_load

    payload = run_fake_load().to_dict()
    assert set(payload["budgets"]) == {
        "actor_backlog",
        "worker_concurrency",
        "worker_cost_units_per_virtual_second",
        "fast_decision_latency_ms_p95",
        "ambient_decision_latency_ms_p95",
        "ambient_decision_latency_ms_p99",
        "ambient_expired_rate",
        "projection_lag_ms_p95",
        "unknown_delivery_rate",
    }
    for name, result in payload["budgets"].items():
        assert set(result) == {"observed", "budget", "applicable", "pass"}, name
        if name == "unknown_delivery_rate":
            assert result == {
                "observed": 0.0,
                "budget": 0.001,
                "applicable": False,
                "pass": None,
            }
        else:
            assert result["applicable"] is True
            assert result["pass"] is True

    fast = payload["latency_ms"]["fast_decision"]
    assert fast["p50"] <= fast["p95"] <= fast["p99"]
    assert fast["p95"] <= 2_500
    assert payload["latency_ms"]["ambient_decision"]["p95"] <= 8_000
    assert payload["latency_ms"]["projection_lag"]["p95"] <= 5_000


def test_virtual_load_uses_runtime_priority_queue_and_hard_cap():
    from eval.load_runner import run_fake_load

    payload = run_fake_load(worker_concurrency_limit=12).to_dict()

    assert payload["scheduling"]["direct_starvation_count"] == 0
    assert payload["scheduling"]["direct_admitted_ahead_of_ambient"] > 0
    assert payload["scheduling"]["peak_worker_concurrency"] == 12
    assert payload["scheduling"]["peak_long_task_concurrency"] == 10
    assert payload["budgets"]["actor_backlog"]["observed"] <= 100
    assert payload["budgets"]["worker_concurrency"] == {
        "observed": 12,
        "budget": 12,
        "applicable": True,
        "pass": True,
    }


def test_worker_concurrency_hard_limit_is_config_visible_and_cross_group_enforced():
    settings = SocialRuntimeSettings.from_mapping(
        {"worker_concurrency_limit": 2}
    )
    assert settings.worker_concurrency_limit == 2

    class BlockingWorker:
        name = "load-worker"

        def __init__(self):
            self.active = 0
            self.peak = 0
            self.at_limit = asyncio.Event()
            self.release = asyncio.Event()

        async def observe(self, frame, context):
            self.active += 1
            self.peak = max(self.peak, self.active)
            if self.active == settings.worker_concurrency_limit:
                self.at_limit.set()
            await self.release.wait()
            self.active -= 1
            return (
                CognitiveObservation.create(
                    worker=self.name,
                    kind="load.fact",
                    proposition={"value": frame.group_id},
                    confidence=1.0,
                    evidence_event_ids=(frame.focus_event_ids[0],),
                    scene_version=frame.scene_version,
                    expires_at=context.now + 1,
                    uncertainty=(),
                ),
            )

    async def scenario():
        worker = BlockingWorker()
        service = CognitionService(
            workers={worker.name: worker},
            budget=CognitionBudget(
                max_worker_calls=1,
                max_cost_units=1,
                max_worker_concurrency=settings.worker_concurrency_limit,
            ),
        )
        evaluations = [
            asyncio.create_task(service.evaluate(_frame(index), _context(index)))
            for index in range(8)
        ]
        await asyncio.wait_for(worker.at_limit.wait(), timeout=1)
        assert worker.active == settings.worker_concurrency_limit
        worker.release.set()
        await asyncio.gather(*evaluations)
        return worker.peak, service.peak_worker_concurrency

    assert asyncio.run(scenario()) == (2, 2)


def test_nearest_rank_percentiles_have_a_fixed_small_sample_definition():
    from eval.load_runner import nearest_rank_percentiles

    assert nearest_rank_percentiles((4, 1, 3, 2)) == {
        "p50": 2,
        "p95": 4,
        "p99": 4,
    }


def test_streaming_faults_drive_accounting_backlog_and_projection_verdicts():
    from eval.load_runner import run_fake_load

    try:
        report = run_fake_load(
            faults={
                "drop_every": 100_000,
                "actor_capacity_per_second": 200,
                "projection_capacity_per_second": 1,
            }
        )
    except TypeError as exc:
        pytest.fail(f"run_fake_load requires a streaming fault seam: {exc}")
    payload = report.to_dict()

    assert payload["event_accounting"] == {
        "ingested": 450_000,
        "committed": 449_996,
        "dropped": 4,
    }
    assert payload["budgets"]["actor_backlog"]["observed"] > 100
    assert payload["budgets"]["actor_backlog"]["pass"] is False
    assert payload["budgets"]["projection_lag_ms_p95"]["observed"] > 5_000
    assert payload["budgets"]["projection_lag_ms_p95"]["pass"] is False


def test_long_tasks_are_real_durable_taskruntime_runs(tmp_path):
    from eval.load_runner import run_fake_load

    runtime_path = tmp_path / "load-task-runtime.db"
    try:
        payload = run_fake_load(runtime_path=runtime_path).to_dict()
    except TypeError as exc:
        pytest.fail(f"load runner must accept a real TaskRuntime path: {exc}")

    assert payload["task_accounting"] == {
        "proposed": 10,
        "running": 10,
        "peak_concurrent": 10,
    }
    with connect_database(runtime_path) as db:
        statuses = db.execute(
            "SELECT status, COUNT(*) FROM tasks GROUP BY status"
        ).fetchall()
        event_count = db.execute("SELECT COUNT(*) FROM task_events").fetchone()[0]
    assert [tuple(row) for row in statuses] == [("running", 10)]
    assert event_count == 30


def test_load_direct_priority_is_measured_through_production_cognition_gate(
    tmp_path,
):
    from eval.load_runner import run_fake_load

    try:
        payload = run_fake_load(
            runtime_path=tmp_path / "gate-probe.db",
            worker_concurrency_limit=12,
        ).to_dict()
    except TypeError as exc:
        pytest.fail(f"load runner must expose production gate evidence: {exc}")

    assert payload["scheduling"]["production_gate"] == {
        "submitted": 15,
        "peak_worker_concurrency": 12,
        "direct_started_before_queued_ambient": True,
    }


def test_ambient_tail_expires_fail_closed_instead_of_hiding_behind_p95():
    from eval.load_runner import run_fake_load

    payload = run_fake_load(worker_concurrency_limit=1).to_dict()

    assert payload["denominators"]["ambient_expiry"] == 18_000
    assert payload["scheduling"]["ambient_expired_count"] >= 498
    assert payload["budgets"]["ambient_expired_rate"]["observed"] > 0
    assert payload["budgets"]["ambient_expired_rate"]["pass"] is False
    assert payload["budgets"]["ambient_decision_latency_ms_p99"]["pass"] is True
    assert payload["worker_cost_units"] == (
        payload["denominators"]["fast_decision_latency_ms"]
        + payload["denominators"]["ambient_decision_latency_ms"] * 2
    )
