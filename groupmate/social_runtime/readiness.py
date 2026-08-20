"""Fail-closed production ownership readiness for Social Runtime v2."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from .contracts import RuntimeGovernanceState
from .persistence.schema import connect_database, initialize_database


READINESS_CHECKS = (
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


@dataclass(frozen=True)
class ReadinessEvidence:
    persona_id: str
    group_id: str
    evidence_kind: str
    gate_results: Mapping[str, bool]
    shadow_started_at: int
    observed_at: int
    reviewed_decisions: int
    frozen_holdout: bool
    holdout_thresholds_passed: bool
    scene_coverage_passed: bool
    safety_event_count: int
    unknown_outbox_count: int
    expired_shadow_backlog_count: int
    governance_page_workflow_passed: bool
    capacity_evidence_kind: str
    capacity_budgets: Mapping[str, Mapping[str, object]]
    config_version: int


@dataclass(frozen=True)
class ReadinessReport:
    persona_id: str
    group_id: str
    evidence_kind: str
    checks: Mapping[str, bool]
    passed: bool
    failed_checks: tuple[str, ...]
    expected_version: int
    config_version: int
    report_hash: str
    evidence_digest: str = ""
    old_instance_confirmation_ref: str = ""


@dataclass(frozen=True)
class RolloutAuditEntry:
    phase: str
    started_at: int
    report_hash: str
    operator_id: str
    reason: str
    expected_version: int


@dataclass(frozen=True)
class RolloutState:
    phase: str
    phase_started_at: int
    report_hash: str
    legacy_fallback: bool = False


class ReadinessGate:
    def __init__(
        self,
        path: Path,
        *,
        persona_id: str,
        allowlisted_group_ids: tuple[str, ...],
        pause_runtime: Callable[[str, RuntimeGovernanceState], object] | None = None,
    ) -> None:
        self.path = Path(path)
        initialize_database(self.path)
        self.persona_id = self._required_text(persona_id, "persona_id")
        self.allowlisted_group_ids = frozenset(
            self._required_text(group_id, "group_id")
            for group_id in allowlisted_group_ids
        )
        if not self.allowlisted_group_ids:
            raise ValueError("readiness requires an explicit group allowlist")
        self.first_test_group_id = next(
            group_id
            for group_id in allowlisted_group_ids
            if str(group_id).strip()
        )
        self.pause_runtime = pause_runtime
        self._ensure_tables()

    def record_evidence(self, evidence: ReadinessEvidence) -> None:
        if not isinstance(evidence, ReadinessEvidence):
            raise ValueError("structured readiness evidence is required")
        self._require_scope(evidence.persona_id, evidence.group_id)
        normalized = self._evidence_dict(evidence)
        with connect_database(self.path) as db:
            db.execute(
                "INSERT INTO readiness_evidence(persona_id, group_id, evidence_json) "
                "VALUES(?, ?, ?) ON CONFLICT(persona_id, group_id) DO UPDATE SET "
                "evidence_json=excluded.evidence_json",
                (
                    self.persona_id,
                    evidence.group_id,
                    self._canonical(normalized),
                ),
            )

    def confirm_old_instance_stopped(
        self,
        group_id: str,
        *,
        operator_id: str,
        expected_version: int,
        confirmation_token: str,
        no_inflight_external_effects: bool,
        confirmed_at: int,
    ) -> None:
        group = self._require_scope(self.persona_id, group_id)
        operator = self._required_text(operator_id, "operator_id")
        token = self._required_text(confirmation_token, "confirmation_token")
        self._non_negative(expected_version, "expected_version")
        self._non_negative(confirmed_at, "confirmed_at")
        digest = hashlib.sha256(token.encode()).hexdigest()
        with connect_database(self.path) as db:
            try:
                db.execute(
                    "INSERT INTO old_instance_confirmations("
                    "token_hash, persona_id, group_id, operator_id, expected_version, "
                    "no_inflight_external_effects, confirmed_at, consumed_at"
                    ") VALUES(?, ?, ?, ?, ?, ?, ?, NULL)",
                    (
                        digest,
                        self.persona_id,
                        group,
                        operator,
                        int(expected_version),
                        int(no_inflight_external_effects is True),
                        int(confirmed_at),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("old-instance confirmation token is already registered") from exc

    def evaluate(self, group_id: str) -> ReadinessReport:
        group = self._require_scope(self.persona_id, group_id)
        with connect_database(self.path) as db:
            row = db.execute(
                "SELECT evidence_json FROM readiness_evidence "
                "WHERE persona_id=? AND group_id=?",
                (self.persona_id, group),
            ).fetchone()
            expected_version = self._control_version_on(db, group)
            config_version = self._config_version_on(db, group)
            confirmation = db.execute(
                "SELECT token_hash FROM old_instance_confirmations "
                "WHERE persona_id=? AND group_id=? AND expected_version=? "
                "AND no_inflight_external_effects=1 AND consumed_at IS NULL "
                "ORDER BY confirmed_at DESC LIMIT 1",
                (self.persona_id, group, expected_version),
            ).fetchone()
        evidence = None if row is None else json.loads(str(row[0]))
        checks = self._checks(
            evidence,
            config_version=config_version,
            old_instance_stopped=confirmation is not None,
        )
        failed = tuple(name for name in READINESS_CHECKS if not checks[name])
        evidence_kind = (
            "missing" if evidence is None else str(evidence["evidence_kind"])
        )
        evidence_digest = (
            "" if evidence is None else hashlib.sha256(self._canonical(evidence).encode()).hexdigest()
        )
        confirmation_ref = "" if confirmation is None else str(confirmation[0])
        core = {
            "persona_id": self.persona_id,
            "group_id": group,
            "evidence_kind": evidence_kind,
            "checks": checks,
            "passed": not failed,
            "failed_checks": list(failed),
            "expected_version": expected_version,
            "config_version": config_version,
            "evidence_digest": evidence_digest,
            "old_instance_confirmation_ref": confirmation_ref,
        }
        report_hash = hashlib.sha256(self._canonical(core).encode()).hexdigest()
        report = ReadinessReport(
            persona_id=self.persona_id,
            group_id=group,
            evidence_kind=evidence_kind,
            checks=checks,
            passed=not failed,
            failed_checks=failed,
            expected_version=expected_version,
            config_version=config_version,
            report_hash=report_hash,
            evidence_digest=evidence_digest,
            old_instance_confirmation_ref=confirmation_ref,
        )
        with connect_database(self.path) as db:
            db.execute(
                "INSERT OR IGNORE INTO readiness_reports("
                "report_hash, persona_id, group_id, report_json"
                ") VALUES(?, ?, ?, ?)",
                (
                    report_hash,
                    self.persona_id,
                    group,
                    self._canonical(self._report_dict(report)),
                ),
            )
        return report

    def load_report(self, report_hash: str) -> ReadinessReport:
        digest = self._required_text(report_hash, "report_hash")
        with connect_database(self.path) as db:
            row = db.execute(
                "SELECT report_json FROM readiness_reports WHERE report_hash=? "
                "AND persona_id=?",
                (digest, self.persona_id),
            ).fetchone()
        if row is None:
            raise LookupError("readiness report is not available")
        value = json.loads(str(row[0]))
        return ReadinessReport(
            persona_id=str(value["persona_id"]),
            group_id=str(value["group_id"]),
            evidence_kind=str(value["evidence_kind"]),
            checks=dict(value["checks"]),
            passed=bool(value["passed"]),
            failed_checks=tuple(value["failed_checks"]),
            expected_version=int(value["expected_version"]),
            config_version=int(value["config_version"]),
            report_hash=str(value["report_hash"]),
            evidence_digest=str(value.get("evidence_digest") or ""),
            old_instance_confirmation_ref=str(
                value.get("old_instance_confirmation_ref") or ""
            ),
        )

    def authorize_social_runtime_on(
        self,
        db: sqlite3.Connection,
        *,
        persona_id: str,
        group_id: str,
        operator_id: str,
        reason: str,
        expected_version: int,
        report_hash: str,
        confirmation_token: str,
        now: int,
    ) -> dict[str, object]:
        group = self._require_scope(persona_id, group_id)
        operator = self._required_text(operator_id, "operator_id")
        audit_reason = self._required_text(reason, "reason")
        digest = hashlib.sha256(
            self._required_text(confirmation_token, "confirmation_token").encode()
        ).hexdigest()
        token_row = db.execute(
            "SELECT persona_id, group_id, operator_id, expected_version, "
            "no_inflight_external_effects, consumed_at FROM old_instance_confirmations "
            "WHERE token_hash=?",
            (digest,),
        ).fetchone()
        if token_row is None:
            raise ValueError("old-instance confirmation token is not available")
        if token_row[5] is not None:
            raise ValueError("old-instance confirmation token was already consumed")
        if (
            str(token_row[0]) != self.persona_id
            or str(token_row[1]) != group
            or int(token_row[3]) != int(expected_version)
            or int(token_row[4]) != 1
        ):
            raise ValueError("old-instance confirmation token scope or version differs")
        if str(token_row[2]) != operator:
            raise ValueError("old-instance confirmation operator differs")

        report_row = db.execute(
            "SELECT report_json FROM readiness_reports WHERE report_hash=? "
            "AND persona_id=? AND group_id=?",
            (self._required_text(report_hash, "readiness_report_hash"), self.persona_id, group),
        ).fetchone()
        if report_row is None:
            raise ValueError("immutable readiness report is not available")
        report = json.loads(str(report_row[0]))
        if report.get("passed") is not True:
            raise ValueError("readiness report did not pass")
        if int(report.get("expected_version", -1)) != int(expected_version):
            raise ValueError("readiness report expected version is stale")
        current_config = self._config_version_on(db, group)
        if int(report.get("config_version", -1)) != current_config:
            raise ValueError("readiness report config version is stale")
        evidence_row = db.execute(
            "SELECT evidence_json FROM readiness_evidence "
            "WHERE persona_id=? AND group_id=?",
            (self.persona_id, group),
        ).fetchone()
        current_evidence_digest = (
            ""
            if evidence_row is None
            else hashlib.sha256(str(evidence_row[0]).encode()).hexdigest()
        )
        if str(report.get("evidence_digest") or "") != current_evidence_digest:
            raise ValueError("readiness evidence changed after report publication")
        if str(report.get("old_instance_confirmation_ref") or "") != digest:
            raise ValueError("readiness report is not bound to this confirmation token")
        active = db.execute(
            "SELECT group_id FROM readiness_rollout_state "
            "WHERE persona_id=? AND phase NOT IN ('PAUSED','COMPLETE')",
            (self.persona_id,),
        ).fetchall()
        if not active and group != self.first_test_group_id:
            raise ValueError("first rollout is limited to the explicit test group")
        db.execute(
            "UPDATE old_instance_confirmations SET consumed_at=? "
            "WHERE token_hash=? AND consumed_at IS NULL",
            (int(now), digest),
        )
        db.execute(
            "INSERT INTO readiness_rollout_state("
            "persona_id, group_id, phase, phase_started_at, report_hash, "
            "operator_id, reason, expected_version"
            ") VALUES(?, ?, 'SUPERVISED', ?, ?, ?, ?, ?) "
            "ON CONFLICT(persona_id, group_id) DO UPDATE SET "
            "phase='SUPERVISED', phase_started_at=excluded.phase_started_at, "
            "report_hash=excluded.report_hash, operator_id=excluded.operator_id, "
            "expected_version=excluded.expected_version",
            (
                self.persona_id,
                group,
                int(now),
                str(report_hash),
                operator,
                audit_reason,
                int(expected_version),
            ),
        )
        db.execute(
            "INSERT INTO readiness_rollout_audit("
            "persona_id, group_id, phase, started_at, report_hash, "
            "operator_id, reason, expected_version"
            ") VALUES(?, ?, 'SUPERVISED', ?, ?, ?, ?, ?)",
            (
                self.persona_id,
                group,
                int(now),
                str(report_hash),
                operator,
                audit_reason,
                int(expected_version),
            ),
        )
        return {
            "runtime_mode": "SOCIAL_RUNTIME",
            "rollout_phase": "SUPERVISED",
            "readiness_report_hash": str(report_hash),
        }

    def advance_rollout_on(
        self,
        db: sqlite3.Connection,
        *,
        persona_id: str,
        group_id: str,
        operator_id: str,
        reason: str,
        expected_version: int,
        report_hash: str,
        now: int,
    ) -> dict[str, object]:
        group = self._require_scope(persona_id, group_id)
        operator = self._required_text(operator_id, "operator_id")
        audit_reason = self._required_text(reason, "reason")
        digest = self._required_text(report_hash, "readiness_report_hash")
        report = db.execute(
            "SELECT report_json FROM readiness_reports WHERE report_hash=? "
            "AND persona_id=? AND group_id=?",
            (digest, self.persona_id, group),
        ).fetchone()
        if report is None or json.loads(str(report[0])).get("passed") is not True:
            raise ValueError("passing immutable readiness report is required")
        row = db.execute(
            "SELECT phase, phase_started_at, report_hash FROM readiness_rollout_state "
            "WHERE persona_id=? AND group_id=?",
            (self.persona_id, group),
        ).fetchone()
        if row is None:
            raise ValueError("rollout ownership handoff has not started")
        phase = str(row[0])
        started_at = int(row[1])
        if str(row[2]) != digest:
            raise ValueError("rollout report hash differs from ownership handoff")
        transitions = {
            "SUPERVISED": ("CANARY", 2 * 60 * 60, "2h supervised"),
            "CANARY": ("EXPANSION_1", 24 * 60 * 60, "24h observation"),
            "EXPANSION_1": ("EXPANSION_3", 24 * 60 * 60, "24h observation"),
            "EXPANSION_3": ("EXPANSION_10", 24 * 60 * 60, "24h observation"),
            "EXPANSION_10": ("EXPANSION_ALL", 24 * 60 * 60, "24h observation"),
            "EXPANSION_ALL": ("COMPLETE", 24 * 60 * 60, "24h observation"),
        }
        transition = transitions.get(phase)
        if transition is None:
            raise ValueError("paused or complete rollout cannot advance")
        next_phase, minimum_seconds, label = transition
        if int(now) - started_at < minimum_seconds:
            raise ValueError(f"rollout requires {label}")
        db.execute(
            "UPDATE readiness_rollout_state SET phase=?, phase_started_at=?, "
            "operator_id=?, reason=?, expected_version=? "
            "WHERE persona_id=? AND group_id=?",
            (
                next_phase,
                int(now),
                operator,
                audit_reason,
                int(expected_version),
                self.persona_id,
                group,
            ),
        )
        db.execute(
            "INSERT INTO readiness_rollout_audit("
            "persona_id, group_id, phase, started_at, report_hash, "
            "operator_id, reason, expected_version"
            ") VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (
                self.persona_id,
                group,
                next_phase,
                int(now),
                digest,
                operator,
                audit_reason,
                int(expected_version),
            ),
        )
        return {
            "runtime_mode": "SOCIAL_RUNTIME",
            "rollout_phase": next_phase,
            "readiness_report_hash": digest,
        }

    def observe_runtime_health(
        self,
        group_id: str,
        *,
        observed_at: int,
        safety_event_count: int,
        dual_sender_event_count: int,
        unknown_outbox_count: int,
    ) -> RolloutState:
        group = self._require_scope(self.persona_id, group_id)
        self._non_negative(observed_at, "observed_at")
        counts = {
            "safety": self._non_negative(safety_event_count, "safety_event_count"),
            "dual_sender": self._non_negative(
                dual_sender_event_count, "dual_sender_event_count"
            ),
            "unknown": self._non_negative(
                unknown_outbox_count, "unknown_outbox_count"
            ),
        }
        with connect_database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT phase, phase_started_at, report_hash FROM readiness_rollout_state "
                "WHERE persona_id=? AND group_id=?",
                (self.persona_id, group),
            ).fetchone()
            if row is None:
                raise LookupError("rollout state is not available")
            phase, started_at, report_hash = str(row[0]), int(row[1]), str(row[2])
            reasons = tuple(name for name, count in counts.items() if count > 0)
            if reasons and phase != "PAUSED":
                expected_version = self._control_version_on(db, group)
                reason = "automatic_pause:" + ",".join(reasons)
                db.execute(
                    "UPDATE readiness_rollout_state SET phase='PAUSED', "
                    "phase_started_at=?, operator_id='system:readiness', reason=?, "
                    "expected_version=? WHERE persona_id=? AND group_id=?",
                    (
                        int(observed_at),
                        reason,
                        expected_version,
                        self.persona_id,
                        group,
                    ),
                )
                db.execute(
                    "INSERT INTO readiness_rollout_audit("
                    "persona_id, group_id, phase, started_at, report_hash, "
                    "operator_id, reason, expected_version"
                    ") VALUES(?, ?, 'PAUSED', ?, ?, 'system:readiness', ?, ?)",
                    (
                        self.persona_id,
                        group,
                        int(observed_at),
                        report_hash,
                        reason,
                        expected_version,
                    ),
                )
                phase, started_at = "PAUSED", int(observed_at)
            db.commit()
        if reasons and self.pause_runtime is not None:
            self.pause_runtime(group, RuntimeGovernanceState(paused=True))
        return RolloutState(phase, started_at, report_hash, legacy_fallback=False)

    def rollout_audit(self, group_id: str) -> tuple[RolloutAuditEntry, ...]:
        group = self._require_scope(self.persona_id, group_id)
        with connect_database(self.path) as db:
            rows = db.execute(
                "SELECT phase, started_at, report_hash, operator_id, reason, "
                "expected_version FROM readiness_rollout_audit "
                "WHERE persona_id=? AND group_id=? ORDER BY audit_id",
                (self.persona_id, group),
            ).fetchall()
        return tuple(
            RolloutAuditEntry(
                phase=str(row[0]),
                started_at=int(row[1]),
                report_hash=str(row[2]),
                operator_id=str(row[3]),
                reason=str(row[4]),
                expected_version=int(row[5]),
            )
            for row in rows
        )

    def _checks(
        self,
        evidence: Mapping[str, object] | None,
        *,
        config_version: int,
        old_instance_stopped: bool,
    ) -> dict[str, bool]:
        if evidence is None:
            return {name: False for name in READINESS_CHECKS}
        gates = evidence.get("gate_results")
        gates = gates if isinstance(gates, Mapping) else {}
        budgets = evidence.get("capacity_budgets")
        budgets = budgets if isinstance(budgets, Mapping) else {}
        capacity_passed = bool(budgets) and all(
            isinstance(value, Mapping)
            and value.get("applicable") is True
            and value.get("pass") is True
            for value in budgets.values()
        )
        return {
            "gate_a": gates.get("A") is True,
            "gate_b": gates.get("B") is True,
            "gate_c": gates.get("C") is True,
            "gate_d": gates.get("D") is True,
            "installed_live_shadow": evidence.get("evidence_kind") == "installed_live_shadow",
            "frozen_holdout": evidence.get("frozen_holdout") is True
            and int(evidence.get("reviewed_decisions", 0)) >= 100,
            "shadow_coverage": evidence.get("scene_coverage_passed") is True,
            "shadow_quality": evidence.get("holdout_thresholds_passed") is True
            and int(evidence.get("config_version", -1)) == int(config_version),
            "safety_zero": int(evidence.get("safety_event_count", -1)) == 0,
            "unknown_outbox_zero": int(evidence.get("unknown_outbox_count", -1)) == 0,
            "expired_shadow_backlog_zero": int(
                evidence.get("expired_shadow_backlog_count", -1)
            ) == 0,
            "governance_page_workflow": evidence.get(
                "governance_page_workflow_passed"
            )
            is True,
            "capacity_budgets": evidence.get("capacity_evidence_kind")
            == "installed_live_shadow"
            and capacity_passed,
            "shadow_24h": int(evidence.get("observed_at", 0))
            - int(evidence.get("shadow_started_at", 0))
            >= 24 * 60 * 60,
            "old_instance_stopped": old_instance_stopped,
        }

    def _require_scope(self, persona_id: object, group_id: object) -> str:
        persona = self._required_text(persona_id, "persona_id")
        group = self._required_text(group_id, "group_id")
        if persona != self.persona_id or group not in self.allowlisted_group_ids:
            raise LookupError("readiness scope is not available")
        return group

    @staticmethod
    def _evidence_dict(evidence: ReadinessEvidence) -> dict[str, object]:
        return {
            "persona_id": evidence.persona_id,
            "group_id": evidence.group_id,
            "evidence_kind": evidence.evidence_kind,
            "gate_results": dict(evidence.gate_results),
            "shadow_started_at": evidence.shadow_started_at,
            "observed_at": evidence.observed_at,
            "reviewed_decisions": evidence.reviewed_decisions,
            "frozen_holdout": evidence.frozen_holdout,
            "holdout_thresholds_passed": evidence.holdout_thresholds_passed,
            "scene_coverage_passed": evidence.scene_coverage_passed,
            "safety_event_count": evidence.safety_event_count,
            "unknown_outbox_count": evidence.unknown_outbox_count,
            "expired_shadow_backlog_count": evidence.expired_shadow_backlog_count,
            "governance_page_workflow_passed": evidence.governance_page_workflow_passed,
            "capacity_evidence_kind": evidence.capacity_evidence_kind,
            "capacity_budgets": {
                str(name): dict(value)
                for name, value in evidence.capacity_budgets.items()
            },
            "config_version": evidence.config_version,
        }

    @staticmethod
    def _report_dict(report: ReadinessReport) -> dict[str, object]:
        return {
            "persona_id": report.persona_id,
            "group_id": report.group_id,
            "evidence_kind": report.evidence_kind,
            "checks": dict(report.checks),
            "passed": report.passed,
            "failed_checks": list(report.failed_checks),
            "expected_version": report.expected_version,
            "config_version": report.config_version,
            "report_hash": report.report_hash,
            "evidence_digest": report.evidence_digest,
            "old_instance_confirmation_ref": report.old_instance_confirmation_ref,
        }

    def _control_version_on(self, db: sqlite3.Connection, group_id: str) -> int:
        rows = db.execute(
            "SELECT action_json FROM governance_actions WHERE persona_id=? AND group_id=?",
            (self.persona_id, group_id),
        ).fetchall()
        return max(
            (int(json.loads(str(row[0])).get("control_version", 0)) for row in rows),
            default=0,
        )

    def _config_version_on(self, db: sqlite3.Connection, group_id: str) -> int:
        row = db.execute(
            "SELECT COALESCE(MAX(version), 0) FROM config_versions "
            "WHERE status='PUBLISHED' AND persona_id=? AND group_id=?",
            (self.persona_id, group_id),
        ).fetchone()
        return int(row[0])

    def _ensure_tables(self) -> None:
        with connect_database(self.path) as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS readiness_evidence (
                    persona_id TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    PRIMARY KEY(persona_id, group_id)
                );
                CREATE TABLE IF NOT EXISTS old_instance_confirmations (
                    token_hash TEXT PRIMARY KEY,
                    persona_id TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    operator_id TEXT NOT NULL,
                    expected_version INTEGER NOT NULL,
                    no_inflight_external_effects INTEGER NOT NULL,
                    confirmed_at INTEGER NOT NULL,
                    consumed_at INTEGER
                );
                CREATE TABLE IF NOT EXISTS readiness_reports (
                    report_hash TEXT PRIMARY KEY,
                    persona_id TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    report_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS readiness_rollout_state (
                    persona_id TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    phase_started_at INTEGER NOT NULL,
                    report_hash TEXT NOT NULL,
                    operator_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    expected_version INTEGER NOT NULL,
                    PRIMARY KEY(persona_id, group_id)
                );
                CREATE TABLE IF NOT EXISTS readiness_rollout_audit (
                    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    persona_id TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    started_at INTEGER NOT NULL,
                    report_hash TEXT NOT NULL,
                    operator_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    expected_version INTEGER NOT NULL
                );
                """
            )

    @staticmethod
    def _required_text(value: object, label: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError(f"{label} must not be empty")
        return normalized

    @staticmethod
    def _non_negative(value: object, label: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{label} must be a non-negative integer")
        return value

    @staticmethod
    def _canonical(value: object) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )


__all__ = (
    "READINESS_CHECKS",
    "ReadinessEvidence",
    "ReadinessGate",
    "ReadinessReport",
    "RolloutAuditEntry",
    "RolloutState",
)
