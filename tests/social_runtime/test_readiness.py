from __future__ import annotations

import pytest

from groupmate.social_runtime.readiness import ReadinessEvidence, ReadinessGate


def test_missing_live_evidence_returns_structured_failed_report(tmp_path):
    gate = ReadinessGate(
        tmp_path / "groupmate-social-runtime-v2.db",
        persona_id="aemeath",
        allowlisted_group_ids=("test-group",),
    )

    report = gate.evaluate("test-group")

    assert report.passed is False
    assert report.evidence_kind == "missing"
    assert report.report_hash
    assert report.failed_checks == (
        "gate_a",
        "gate_b",
        "gate_c",
        "gate_d",
        "installed_live_shadow",
        "frozen_holdout",
        "shadow_coverage",
        "shadow_quality",
        "safety_zero",
        "unknown_outbox_zero",
        "expired_shadow_backlog_zero",
        "governance_page_workflow",
        "capacity_budgets",
        "shadow_24h",
        "old_instance_stopped",
    )


def _evidence(**overrides):
    values = {
        "persona_id": "aemeath",
        "group_id": "test-group",
        "evidence_kind": "installed_live_shadow",
        "gate_results": {"A": True, "B": True, "C": True, "D": True},
        "shadow_started_at": 1_000,
        "observed_at": 1_000 + 24 * 60 * 60,
        "reviewed_decisions": 100,
        "frozen_holdout": True,
        "holdout_thresholds_passed": True,
        "scene_coverage_passed": True,
        "safety_event_count": 0,
        "unknown_outbox_count": 0,
        "expired_shadow_backlog_count": 0,
        "governance_page_workflow_passed": True,
        "capacity_evidence_kind": "installed_live_shadow",
        "capacity_budgets": {
            "actor_backlog": {"applicable": True, "pass": True},
            "unknown_delivery_rate": {"applicable": True, "pass": True},
        },
        "config_version": 0,
    }
    values.update(overrides)
    return ReadinessEvidence(**values)


@pytest.mark.parametrize(
    ("evidence_kind", "capacity_kind", "config_version", "failed_check"),
    (
        (
            "historical_bootstrap",
            "installed_live_shadow",
            0,
            "installed_live_shadow",
        ),
        ("installed_live_shadow", "synthetic_preflight", 0, "capacity_budgets"),
        ("installed_live_shadow", "installed_live_shadow", 1, "shadow_quality"),
    ),
)
def test_non_production_evidence_cannot_pass_readiness(
    tmp_path, evidence_kind, capacity_kind, config_version, failed_check
):
    gate = ReadinessGate(
        tmp_path / "groupmate-social-runtime-v2.db",
        persona_id="aemeath",
        allowlisted_group_ids=("test-group",),
    )
    gate.record_evidence(
        _evidence(
            evidence_kind=evidence_kind,
            capacity_evidence_kind=capacity_kind,
            config_version=config_version,
        )
    )
    gate.confirm_old_instance_stopped(
        "test-group",
        operator_id="admin:root",
        expected_version=0,
        confirmation_token="old-instance-stopped:deployment-1",
        no_inflight_external_effects=True,
        confirmed_at=90_000,
    )

    report = gate.evaluate("test-group")

    assert report.passed is False
    assert failed_check in report.failed_checks


def test_complete_live_evidence_produces_immutable_version_bound_report(tmp_path):
    gate = ReadinessGate(
        tmp_path / "groupmate-social-runtime-v2.db",
        persona_id="aemeath",
        allowlisted_group_ids=("test-group",),
    )
    gate.record_evidence(_evidence())
    gate.confirm_old_instance_stopped(
        "test-group",
        operator_id="admin:root",
        expected_version=0,
        confirmation_token="old-instance-stopped:deployment-1",
        no_inflight_external_effects=True,
        confirmed_at=90_000,
    )

    report = gate.evaluate("test-group")
    reloaded = gate.load_report(report.report_hash)

    assert report.passed is True
    assert report.failed_checks == ()
    assert report.expected_version == 0
    assert report.config_version == 0
    assert reloaded == report
