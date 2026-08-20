"""Server-authorized CQRS commands that emit auditable domain events."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping

from ..contracts import SocialEventEnvelope
from ..persistence.schema import connect_database, initialize_database
from .config_versions import ConfigNotFound, ConfigVersionRepository


class CommandError(RuntimeError):
    status_code = 400


class CommandValidationError(CommandError, ValueError):
    status_code = 400


class CommandConfirmationRequired(CommandValidationError):
    pass


class CommandForbidden(CommandError, PermissionError):
    status_code = 403


class CommandNotFound(CommandError, LookupError):
    status_code = 404


class ExpectedVersionConflict(CommandError):
    status_code = 409

    def __init__(self, expected_version: int, current_version: int) -> None:
        self.expected_version = int(expected_version)
        self.current_version = int(current_version)
        super().__init__(
            f"expected version {self.expected_version}, "
            f"current version {self.current_version}"
        )


class CommandIdentityConflict(CommandError):
    status_code = 409


@dataclass(frozen=True)
class CommandContext:
    admin_id: str
    persona_id: str
    group_id: str | None
    expected_version: int
    reason: str
    confirmed: bool


@dataclass(frozen=True)
class PauseRuntime:
    paused: bool
    command_id: str | None = None


@dataclass(frozen=True)
class ResetState:
    target: str
    command_id: str | None = None


@dataclass(frozen=True)
class CreateConfigDraft:
    config_id: str
    config: Mapping[str, object]
    command_id: str | None = None


@dataclass(frozen=True)
class ValidateConfig:
    config_id: str
    command_id: str | None = None


@dataclass(frozen=True)
class DryRunConfig:
    config_id: str
    historical_events: tuple[Mapping[str, object], ...] = ()
    worker_outputs: tuple[Mapping[str, object], ...] = ()
    command_id: str | None = None


@dataclass(frozen=True)
class PublishConfig:
    config_id: str
    command_id: str | None = None


@dataclass(frozen=True)
class RestoreConfig:
    config_id: str
    source_version: int
    command_id: str | None = None


@dataclass(frozen=True)
class ReviewEvidence:
    entity_ref: str
    decision: str
    command_id: str | None = None


@dataclass(frozen=True)
class ReviewShadowDecision:
    entity_ref: str
    decision: str
    categories: tuple[str, ...] = ()
    correction: Mapping[str, object] | None = None
    command_id: str | None = None


@dataclass(frozen=True)
class ForgetMemory:
    entity_ref: str
    command_id: str | None = None


@dataclass(frozen=True)
class CorrectSocialState:
    entity_ref: str
    correction: Mapping[str, object]
    command_id: str | None = None


@dataclass(frozen=True)
class LinkIdentity:
    source_ref: str
    target_ref: str
    allowed_data_types: tuple[str, ...]
    command_id: str | None = None


@dataclass(frozen=True)
class CancelTask:
    entity_ref: str
    command_id: str | None = None


@dataclass(frozen=True)
class ApproveCalibration:
    entity_ref: str
    command_id: str | None = None


ControlCommand = (
    PauseRuntime
    | ResetState
    | CreateConfigDraft
    | ValidateConfig
    | DryRunConfig
    | PublishConfig
    | RestoreConfig
    | ReviewEvidence
    | ReviewShadowDecision
    | ForgetMemory
    | CorrectSocialState
    | LinkIdentity
    | CancelTask
    | ApproveCalibration
)


@dataclass(frozen=True)
class CommandResult:
    action_id: str
    command_id: str
    version: int
    data: dict[str, object]
    event: SocialEventEnvelope


_HIGH_IMPACT = (
    ResetState,
    PublishConfig,
    RestoreConfig,
    ForgetMemory,
    CorrectSocialState,
    LinkIdentity,
    CancelTask,
    ApproveCalibration,
    ReviewShadowDecision,
)
_CONFIG_COMMANDS = (
    CreateConfigDraft,
    ValidateConfig,
    DryRunConfig,
    PublishConfig,
    RestoreConfig,
)
_ALL_PROJECTIONS = (
    "runtime",
    "activity",
    "scenes",
    "people",
    "culture",
    "tasks",
    "persona",
    "governance",
    "evaluation",
    "health",
)


class CommandService:
    def __init__(
        self,
        path: Path,
        *,
        persona_id: str,
        group_ids: tuple[str, ...],
        admin_ids: tuple[str, ...],
        config_repository: ConfigVersionRepository | None = None,
        shadow_repository: object | None = None,
    ) -> None:
        self.path = Path(path)
        initialize_database(self.path)
        self.persona_id = self._required_text(persona_id, "persona_id")
        self.group_ids = frozenset(
            self._required_text(group_id, "group_id") for group_id in group_ids
        )
        self.admin_ids = frozenset(
            self._required_text(admin_id, "admin_id") for admin_id in admin_ids
        )
        if not self.group_ids or not self.admin_ids:
            raise ValueError("command service requires group and administrator scope")
        self.config_repository = config_repository or ConfigVersionRepository(
            self.path
        )
        self.shadow_repository = shadow_repository
        if self.config_repository.path != self.path:
            raise ValueError("config repository must share the command database")

    def execute(
        self, command: ControlCommand, context: CommandContext
    ) -> CommandResult:
        self._validate_context(command, context)
        payload = self._command_payload(command)
        fingerprint = self._fingerprint(command, context, payload)
        command_id = self._resolve_command_id(command, context, fingerprint)
        now = int(time.time())
        db = connect_database(self.path)
        try:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT action_json FROM governance_actions WHERE command_id=?",
                (command_id,),
            ).fetchone()
            if existing is not None:
                stored = json.loads(str(existing[0]))
                if str(stored.get("fingerprint") or "") != fingerprint:
                    raise CommandIdentityConflict(
                        "command id belongs to different content or scope"
                    )
                db.commit()
                return self._result_from_dict(stored["result"])

            current_version = self._expected_version_on(db, command, context)
            if context.expected_version != current_version:
                raise ExpectedVersionConflict(
                    context.expected_version, current_version
                )
            try:
                data, event_type = self._execute_on(db, command, context, now)
            except ConfigNotFound as exc:
                raise CommandNotFound("command target is not available") from exc
            control_version = self._control_version_on(db, context) + 1
            result_version = (
                int(data["version"])
                if isinstance(command, (PublishConfig, RestoreConfig))
                else control_version
            )
            action_type = event_type.removeprefix("control.")
            action_id = self._opaque_id(
                "governance-action",
                command_id,
                context.persona_id,
                context.group_id or "",
            )
            event_summary = {
                key: data[key]
                for key in (
                    "paused",
                    "status",
                    "decision",
                    "entity_ref",
                    "categories",
                )
                if key in data
            }
            if "config_version" in data:
                event_summary["config_version"] = data["config_version"]
            if isinstance(command, _CONFIG_COMMANDS) and "version" in data:
                event_summary["config_version"] = data["version"]
            event = SocialEventEnvelope.create(
                event_id=f"control:{action_id}",
                event_type=event_type,
                occurred_at=now,
                received_at=now,
                persona_id=context.persona_id,
                group_id=context.group_id,
                actor_id=context.admin_id,
                source_message_id=None,
                correlation_id=command_id,
                causation_id=None,
                payload={
                    "action_ref": action_id,
                    "reason": context.reason.strip(),
                    "version": result_version,
                    "control_version": control_version,
                    "result": data,
                    **event_summary,
                },
            )
            result = CommandResult(
                action_id=action_id,
                command_id=command_id,
                version=result_version,
                data=data,
                event=event,
            )
            stored = {
                "fingerprint": fingerprint,
                "control_version": control_version,
                "result": self._result_to_dict(result),
            }
            db.execute(
                "INSERT INTO governance_actions("
                "action_id, command_id, persona_id, group_id, actor_id, "
                "action_type, reason, action_json, created_at"
                ") VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    action_id,
                    command_id,
                    context.persona_id,
                    context.group_id,
                    context.admin_id,
                    action_type,
                    context.reason.strip(),
                    self._canonical_json(stored),
                    now,
                ),
            )
            db.commit()
            return result
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _execute_on(
        self,
        db: sqlite3.Connection,
        command: ControlCommand,
        context: CommandContext,
        now: int,
    ) -> tuple[dict[str, object], str]:
        if isinstance(command, PauseRuntime):
            return {"paused": bool(command.paused)}, (
                "control.runtime_paused"
                if command.paused
                else "control.runtime_resumed"
            )
        if isinstance(command, ResetState):
            return {
                "target": self._required_text(command.target, "reset target")
            }, "control.state_reset"
        if isinstance(command, CreateConfigDraft):
            draft = self.config_repository._create_draft_on(
                db,
                command.config_id,
                command.config,
                persona_id=context.persona_id,
                group_id=context.group_id,
                now=now,
            )
            return self._config_data(draft), "control.config_draft_created"
        if isinstance(command, ValidateConfig):
            validated = self.config_repository._validate_on(
                db,
                command.config_id,
                persona_id=context.persona_id,
                group_id=context.group_id,
            )
            return self._config_data(validated), "control.config_validated"
        if isinstance(command, DryRunConfig):
            return self.config_repository._dry_run_on(
                db,
                command.config_id,
                persona_id=context.persona_id,
                group_id=context.group_id,
                historical_events=command.historical_events,
                worker_outputs=command.worker_outputs,
            ), "control.config_dry_run"
        if isinstance(command, PublishConfig):
            published = self.config_repository._publish_on(
                db,
                command.config_id,
                persona_id=context.persona_id,
                group_id=context.group_id,
                expected_version=context.expected_version,
            )
            return self._config_data(published), "control.config_published"
        if isinstance(command, RestoreConfig):
            restored = self.config_repository._restore_on(
                db,
                command.config_id,
                command.source_version,
                persona_id=context.persona_id,
                group_id=context.group_id,
                expected_version=context.expected_version,
                now=now,
            )
            return self._config_data(restored), "control.config_restored"
        if isinstance(command, ReviewEvidence):
            decision = self._required_text(command.decision, "review decision")
            if decision not in {"accept", "reject", "needs_more_evidence"}:
                raise CommandValidationError("unsupported evidence review decision")
            return {
                "entity_ref": self._scoped_ref(
                    db,
                    context,
                    command.entity_ref,
                    _ALL_PROJECTIONS,
                    evidence=True,
                ),
                "decision": decision,
            }, "control.evidence_reviewed"
        if isinstance(command, ReviewShadowDecision):
            if self.shadow_repository is None:
                raise CommandValidationError("shadow review governance is unavailable")
            review = getattr(self.shadow_repository, "_review_on", None)
            if not callable(review):
                raise CommandValidationError("shadow review repository contract is invalid")
            try:
                data = review(
                    db,
                    command.entity_ref,
                    persona_id=context.persona_id,
                    group_id=context.group_id,
                    reviewer_id=context.admin_id,
                    decision=command.decision,
                    categories=tuple(command.categories),
                    correction=command.correction,
                    reviewed_at=now,
                )
            except LookupError as exc:
                raise CommandNotFound("command target is not available") from exc
            except ValueError as exc:
                raise CommandValidationError(str(exc)) from exc
            return data, "control.shadow_decision_reviewed"
        if isinstance(command, ForgetMemory):
            return {
                "entity_ref": self._scoped_ref(
                    db, context, command.entity_ref, ("people",)
                )
            }, "control.memory_forgotten"
        if isinstance(command, CorrectSocialState):
            if not isinstance(command.correction, Mapping) or not command.correction:
                raise CommandValidationError("social correction must not be empty")
            correction = json.loads(self._canonical_json(dict(command.correction)))
            return {
                "entity_ref": self._scoped_ref(
                    db, context, command.entity_ref, ("people",)
                ),
                "correction": correction,
            }, "control.social_state_corrected"
        if isinstance(command, LinkIdentity):
            allowed = tuple(
                self._required_text(value, "allowed data type")
                for value in command.allowed_data_types
            )
            if not allowed:
                raise CommandValidationError(
                    "identity link requires allowed data types"
                )
            return {
                "source_ref": self._scoped_ref(
                    db, context, command.source_ref, ("people",)
                ),
                "target_ref": self._scoped_ref(
                    db, context, command.target_ref, ("people",)
                ),
                "allowed_data_types": list(allowed),
            }, "control.identity_linked"
        if isinstance(command, CancelTask):
            return {
                "entity_ref": self._scoped_ref(
                    db, context, command.entity_ref, ("tasks",)
                )
            }, "control.task_cancel_requested"
        if isinstance(command, ApproveCalibration):
            if self.shadow_repository is not None:
                approve = getattr(
                    self.shadow_repository, "_approve_calibration_on", None
                )
                if not callable(approve):
                    raise CommandValidationError(
                        "shadow calibration repository contract is invalid"
                    )
                try:
                    data = approve(
                        db,
                        command.entity_ref,
                        persona_id=context.persona_id,
                        group_id=context.group_id,
                        config_repository=self.config_repository,
                        now=now,
                    )
                except LookupError as exc:
                    raise CommandNotFound("command target is not available") from exc
                return data, "control.calibration_approved"
            return {
                "entity_ref": self._scoped_ref(
                    db,
                    context,
                    command.entity_ref,
                    ("governance", "evaluation"),
                )
            }, "control.calibration_approved"
        raise CommandValidationError("unsupported control command")

    def _validate_context(
        self, command: ControlCommand, context: CommandContext
    ) -> None:
        if not isinstance(context, CommandContext):
            raise CommandValidationError("command context is required")
        if str(context.admin_id).strip() not in self.admin_ids:
            raise CommandForbidden("administrator authority is required")
        if (
            str(context.persona_id).strip() != self.persona_id
            or context.group_id not in self.group_ids
        ):
            raise CommandNotFound("command scope is not available")
        if not str(context.reason).strip():
            raise CommandValidationError("command reason is required")
        if not isinstance(context.expected_version, int) or context.expected_version < 0:
            raise CommandValidationError(
                "expected_version must be a non-negative integer"
            )
        if isinstance(command, _HIGH_IMPACT) and context.confirmed is not True:
            raise CommandConfirmationRequired(
                "high-impact command requires explicit confirmation"
            )

    def _expected_version_on(
        self,
        db: sqlite3.Connection,
        command: ControlCommand,
        context: CommandContext,
    ) -> int:
        if isinstance(command, _CONFIG_COMMANDS):
            return self.config_repository._published_version_on(
                db,
                persona_id=context.persona_id,
                group_id=context.group_id,
            )
        return self._control_version_on(db, context)

    @staticmethod
    def _control_version_on(
        db: sqlite3.Connection, context: CommandContext
    ) -> int:
        rows = db.execute(
            "SELECT action_json FROM governance_actions "
            "WHERE persona_id=? AND group_id IS ?",
            (context.persona_id, context.group_id),
        ).fetchall()
        return max(
            (
                int(json.loads(str(row[0])).get("control_version", 0))
                for row in rows
            ),
            default=0,
        )

    @staticmethod
    def _command_payload(command: ControlCommand) -> dict[str, object]:
        payload = asdict(command)
        payload.pop("command_id", None)
        return payload

    def _fingerprint(
        self,
        command: ControlCommand,
        context: CommandContext,
        payload: Mapping[str, object],
    ) -> str:
        identity = {
            "type": type(command).__name__,
            "persona_id": context.persona_id,
            "group_id": context.group_id,
            "admin_id": context.admin_id,
            "expected_version": context.expected_version,
            "reason": context.reason.strip(),
            "payload": payload,
        }
        return hashlib.sha256(self._canonical_json(identity).encode()).hexdigest()

    def _resolve_command_id(
        self,
        command: ControlCommand,
        context: CommandContext,
        fingerprint: str,
    ) -> str:
        explicit = str(getattr(command, "command_id", None) or "").strip()
        if explicit:
            return explicit
        return self._opaque_id(
            "command",
            fingerprint,
            str(context.expected_version),
        )

    @staticmethod
    def _config_data(version) -> dict[str, object]:
        return {
            "config_id": version.config_id,
            "version": version.version,
            "status": version.status.value,
        }

    @staticmethod
    def _result_to_dict(result: CommandResult) -> dict[str, object]:
        return {
            "action_id": result.action_id,
            "command_id": result.command_id,
            "version": result.version,
            "data": result.data,
            "event": result.event.to_dict(),
        }

    @staticmethod
    def _result_from_dict(payload: Mapping[str, object]) -> CommandResult:
        return CommandResult(
            action_id=str(payload["action_id"]),
            command_id=str(payload["command_id"]),
            version=int(payload["version"]),
            data=dict(payload["data"]),
            event=SocialEventEnvelope.from_dict(payload["event"]),
        )

    @staticmethod
    def _required_text(value: object, label: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise CommandValidationError(f"{label} must not be empty")
        return normalized

    @classmethod
    def _required_ref(cls, value: object) -> str:
        normalized = cls._required_text(value, "entity reference")
        if ":" not in normalized:
            raise CommandValidationError("entity reference must be opaque and typed")
        return normalized

    @classmethod
    def _scoped_ref(
        cls,
        db: sqlite3.Connection,
        context: CommandContext,
        value: object,
        projection_names: tuple[str, ...],
        *,
        evidence: bool = False,
    ) -> str:
        normalized = cls._required_ref(value)
        placeholders = ",".join("?" for _ in projection_names)
        try:
            rows = db.execute(
                "SELECT entity_ref, evidence_refs_json "
                "FROM control_projection_items "
                f"WHERE projection_name IN ({placeholders}) "
                "AND persona_id=? AND group_id IS ?",
                (*projection_names, context.persona_id, context.group_id),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = ()
        for row in rows:
            if not evidence and str(row["entity_ref"]) == normalized:
                return normalized
            if evidence and normalized in json.loads(str(row["evidence_refs_json"])):
                return normalized
        raise CommandNotFound("command target is not available")

    @staticmethod
    def _opaque_id(prefix: str, *parts: str) -> str:
        digest = hashlib.sha256("\0".join(parts).encode()).hexdigest()[:24]
        return f"{prefix}:{digest}"

    @staticmethod
    def _canonical_json(value: object) -> str:
        try:
            return json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise CommandValidationError(
                "command payload must be JSON serializable"
            ) from exc


__all__ = (
    "ApproveCalibration",
    "CancelTask",
    "CommandConfirmationRequired",
    "CommandContext",
    "CommandError",
    "CommandForbidden",
    "CommandIdentityConflict",
    "CommandNotFound",
    "CommandResult",
    "CommandService",
    "CommandValidationError",
    "CorrectSocialState",
    "CreateConfigDraft",
    "DryRunConfig",
    "ExpectedVersionConflict",
    "ForgetMemory",
    "LinkIdentity",
    "PauseRuntime",
    "PublishConfig",
    "ResetState",
    "RestoreConfig",
    "ReviewEvidence",
    "ReviewShadowDecision",
    "ValidateConfig",
)
