from __future__ import annotations

import pytest

from groupmate.social_runtime.control.commands import (
    AdvanceRollout,
    CommandContext,
    CommandValidationError,
    ExpectedVersionConflict,
    PauseRuntime,
    SetRuntimeMode,
)
from groupmate.social_runtime.contracts import RuntimeGovernanceState
from groupmate.social_runtime.control.commands import CommandService
from groupmate.social_runtime.delivery.outbox import OutboxService
from groupmate.social_runtime.readiness import ReadinessEvidence, ReadinessGate


def _context(*, expected_version=0, admin_id="admin:root", group_id="group-1"):
    return CommandContext(
        admin_id=admin_id,
        persona_id="aemeath",
        group_id=group_id,
        expected_version=expected_version,
        reason="explicit production ownership handoff",
        confirmed=True,
    )


def _ready_gate(path, *, groups=("group-1",), operator="admin:root"):
    gate = ReadinessGate(
        path,
        persona_id="aemeath",
        allowlisted_group_ids=groups,
    )
    gate.record_evidence(
        ReadinessEvidence(
            persona_id="aemeath",
            group_id="group-1",
            evidence_kind="installed_live_shadow",
            gate_results={"A": True, "B": True, "C": True, "D": True},
            shadow_started_at=1_000,
            observed_at=87_400,
            reviewed_decisions=100,
            frozen_holdout=True,
            holdout_thresholds_passed=True,
            scene_coverage_passed=True,
            safety_event_count=0,
            unknown_outbox_count=0,
            expired_shadow_backlog_count=0,
            governance_page_workflow_passed=True,
            capacity_evidence_kind="installed_live_shadow",
            capacity_budgets={
                "actor_backlog": {"applicable": True, "pass": True},
                "unknown_delivery_rate": {"applicable": True, "pass": True},
            },
            config_version=0,
        )
    )
    gate.confirm_old_instance_stopped(
        "group-1",
        operator_id=operator,
        expected_version=0,
        confirmation_token="old-instance-stopped:deployment-1",
        no_inflight_external_effects=True,
        confirmed_at=90_000,
    )
    return gate


def test_social_runtime_handoff_consumes_scoped_token_without_sending(tmp_path):
    path = tmp_path / "groupmate-social-runtime-v2.db"
    gate = _ready_gate(path)
    report = gate.evaluate("group-1")
    service = CommandService(
        path,
        persona_id="aemeath",
        group_ids=("group-1",),
        admin_ids=("admin:root",),
        readiness_gate=gate,
    )
    command = SetRuntimeMode(
        "SOCIAL_RUNTIME",
        report.report_hash,
        "old-instance-stopped:deployment-1",
        command_id="handoff:1",
    )

    result = service.execute(command, _context())
    replay = service.execute(command, _context())

    assert replay == result
    assert result.event.event_type == "control.runtime_mode_set"
    assert result.data == {
        "runtime_mode": "SOCIAL_RUNTIME",
        "rollout_phase": "SUPERVISED",
        "readiness_report_hash": report.report_hash,
    }
    assert OutboxService(path).count() == 0
    with pytest.raises(CommandValidationError, match="consumed"):
        service.execute(
            SetRuntimeMode(
                "SOCIAL_RUNTIME",
                report.report_hash,
                "old-instance-stopped:deployment-1",
                command_id="handoff:replay-token",
            ),
            _context(expected_version=1),
        )


def test_handoff_rejects_stale_report_and_different_operator(tmp_path):
    path = tmp_path / "groupmate-social-runtime-v2.db"
    gate = _ready_gate(path, operator="admin:owner")
    report = gate.evaluate("group-1")
    service = CommandService(
        path,
        persona_id="aemeath",
        group_ids=("group-1",),
        admin_ids=("admin:root", "admin:owner"),
        readiness_gate=gate,
    )

    with pytest.raises(CommandValidationError, match="operator"):
        service.execute(
            SetRuntimeMode(
                "SOCIAL_RUNTIME",
                report.report_hash,
                "old-instance-stopped:deployment-1",
                command_id="handoff:wrong-operator",
            ),
            _context(),
        )

    service.execute(
        PauseRuntime(paused=True, command_id="pause:before-handoff"),
        _context(),
    )
    with pytest.raises(ExpectedVersionConflict):
        service.execute(
            SetRuntimeMode(
                "SOCIAL_RUNTIME",
                report.report_hash,
                "old-instance-stopped:deployment-1",
                command_id="handoff:stale",
            ),
            _context(expected_version=0, admin_id="admin:owner"),
        )


def test_rollout_stages_enforce_server_time_and_audit_each_expansion(tmp_path):
    path = tmp_path / "groupmate-social-runtime-v2.db"
    clock = [100_000]
    gate = _ready_gate(path)
    report = gate.evaluate("group-1")
    service = CommandService(
        path,
        persona_id="aemeath",
        group_ids=("group-1",),
        admin_ids=("admin:root",),
        readiness_gate=gate,
        clock=lambda: clock[0],
    )
    service.execute(
        SetRuntimeMode(
            "SOCIAL_RUNTIME",
            report.report_hash,
            "old-instance-stopped:deployment-1",
            command_id="handoff:rollout",
        ),
        _context(),
    )

    clock[0] += 2 * 60 * 60 - 1
    with pytest.raises(CommandValidationError, match="2h supervised"):
        service.execute(
            AdvanceRollout(report.report_hash, command_id="advance:too-early"),
            _context(expected_version=1),
        )
    clock[0] += 1
    canary = service.execute(
        AdvanceRollout(report.report_hash, command_id="advance:canary"),
        _context(expected_version=1),
    )
    assert canary.data["rollout_phase"] == "CANARY"

    clock[0] += 24 * 60 * 60 - 1
    with pytest.raises(CommandValidationError, match="24h observation"):
        service.execute(
            AdvanceRollout(report.report_hash, command_id="advance:canary-early"),
            _context(expected_version=2),
        )

    phases = ("EXPANSION_1", "EXPANSION_3", "EXPANSION_10", "EXPANSION_ALL")
    for offset, phase in enumerate(phases, start=2):
        clock[0] += 1 if phase == "EXPANSION_1" else 24 * 60 * 60
        result = service.execute(
            AdvanceRollout(
                report.report_hash,
                command_id=f"advance:{phase.casefold()}",
            ),
            _context(expected_version=offset),
        )
        assert result.data["rollout_phase"] == phase

    audit = gate.rollout_audit("group-1")
    assert [entry.phase for entry in audit] == [
        "SUPERVISED",
        "CANARY",
        *phases,
    ]
    assert all(entry.report_hash == report.report_hash for entry in audit)
    assert all(entry.operator_id == "admin:root" for entry in audit)
    assert all(entry.reason == "explicit production ownership handoff" for entry in audit)
    assert [entry.expected_version for entry in audit] == list(range(6))


@pytest.mark.parametrize(
    "hazard",
    (
        {"safety_event_count": 1},
        {"dual_sender_event_count": 1},
        {"unknown_outbox_count": 1},
    ),
)
def test_rollout_hazards_automatically_pause_without_legacy_fallback(
    tmp_path, hazard
):
    path = tmp_path / "groupmate-social-runtime-v2.db"
    paused = []
    gate = _ready_gate(path)
    gate.pause_runtime = lambda group_id, state: paused.append((group_id, state))
    report = gate.evaluate("group-1")
    service = CommandService(
        path,
        persona_id="aemeath",
        group_ids=("group-1",),
        admin_ids=("admin:root",),
        readiness_gate=gate,
        clock=lambda: 100_000,
    )
    service.execute(
        SetRuntimeMode(
            "SOCIAL_RUNTIME",
            report.report_hash,
            "old-instance-stopped:deployment-1",
            command_id="handoff:hazard",
        ),
        _context(),
    )

    state = gate.observe_runtime_health(
        "group-1",
        observed_at=100_001,
        safety_event_count=hazard.get("safety_event_count", 0),
        dual_sender_event_count=hazard.get("dual_sender_event_count", 0),
        unknown_outbox_count=hazard.get("unknown_outbox_count", 0),
    )

    assert state.phase == "PAUSED"
    assert state.legacy_fallback is False
    assert paused == [("group-1", RuntimeGovernanceState(paused=True))]
