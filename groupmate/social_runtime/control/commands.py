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
from ..persona.profile import GroupmatePersonaProfile, PERSONA_PROFILE_CONFIG_KEY
from ..persistence.schema import connect_database, initialize_database
from ..knowledge.repository import KnowledgeRepository
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
class SetRuntimeMode:
    runtime_mode: str
    readiness_report_hash: str
    old_instance_confirmation_token: str
    command_id: str | None = None


@dataclass(frozen=True)
class AdvanceRollout:
    readiness_report_hash: str
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


@dataclass(frozen=True)
class CorrectProfileFact:
    member_ref: str
    fact_ref: str
    new_summary: str
    command_id: str | None = None


@dataclass(frozen=True)
class InvalidateProfileFact:
    member_ref: str
    fact_ref: str
    command_id: str | None = None


@dataclass(frozen=True)
class MergeProfileIdentity:
    source_member_ref: str
    target_member_ref: str
    stable_target_id: str
    command_id: str | None = None


@dataclass(frozen=True)
class SplitProfileIdentity:
    source_member_ref: str
    new_stable_id: str
    new_display_name: str
    fact_refs: tuple[str, ...]
    command_id: str | None = None


@dataclass(frozen=True)
class SetMemberStyleDistillation:
    member_ref: str
    enabled: bool
    command_id: str | None = None


@dataclass(frozen=True)
class ConfirmKnowledgeConvention:
    convention_id: str
    command_id: str | None = None


@dataclass(frozen=True)
class RejectKnowledgeConvention:
    convention_id: str
    command_id: str | None = None


@dataclass(frozen=True)
class SupersedeKnowledgeAlias:
    alias_id: str
    replacement_entity_id: str
    command_id: str | None = None


@dataclass(frozen=True)
class DisputeKnowledgeClaim:
    claim_id: str
    command_id: str | None = None


@dataclass(frozen=True)
class RetryKnowledgeJob:
    job_id: str
    command_id: str | None = None


@dataclass(frozen=True)
class InvalidateKnowledgeCache:
    entity_id: str
    command_id: str | None = None


@dataclass(frozen=True)
class SetKnowledgeAmbientCanary:
    enabled: bool
    command_id: str | None = None


ControlCommand = (
    PauseRuntime
    | SetRuntimeMode
    | AdvanceRollout
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
    | CorrectProfileFact
    | InvalidateProfileFact
    | MergeProfileIdentity
    | SplitProfileIdentity
    | SetMemberStyleDistillation
    | ConfirmKnowledgeConvention
    | RejectKnowledgeConvention
    | SupersedeKnowledgeAlias
    | DisputeKnowledgeClaim
    | RetryKnowledgeJob
    | InvalidateKnowledgeCache
    | SetKnowledgeAmbientCanary
)


@dataclass(frozen=True)
class CommandResult:
    action_id: str
    command_id: str
    version: int
    data: dict[str, object]
    event: SocialEventEnvelope


_HIGH_IMPACT = (
    SetRuntimeMode,
    AdvanceRollout,
    ResetState,
    PublishConfig,
    RestoreConfig,
    ForgetMemory,
    CorrectSocialState,
    LinkIdentity,
    CancelTask,
    ApproveCalibration,
    ReviewShadowDecision,
    CorrectProfileFact,
    InvalidateProfileFact,
    MergeProfileIdentity,
    SplitProfileIdentity,
)
_CONFIG_COMMANDS = (
    CreateConfigDraft,
    ValidateConfig,
    DryRunConfig,
    PublishConfig,
    RestoreConfig,
)
_PROFILE_COMMANDS = (
    CorrectProfileFact,
    InvalidateProfileFact,
    MergeProfileIdentity,
    SplitProfileIdentity,
)
_MEMBER_STYLE_COMMANDS = (SetMemberStyleDistillation,)
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
        readiness_gate: object | None = None,
        clock: object | None = None,
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
        self.readiness_gate = readiness_gate
        self._clock = time.time if clock is None else clock
        if not callable(self._clock):
            raise ValueError("command clock must be callable")
        if self.config_repository.path != self.path:
            raise ValueError("config repository must share the command database")
        if readiness_gate is not None and Path(getattr(readiness_gate, "path", "")) != self.path:
            raise ValueError("readiness gate must share the command database")

    def execute(
        self, command: ControlCommand, context: CommandContext
    ) -> CommandResult:
        self._validate_context(command, context)
        payload = self._command_payload(command)
        fingerprint = self._fingerprint(command, context, payload)
        command_id = self._resolve_command_id(command, context, fingerprint)
        now = int(self._clock())
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
                data, event_type = self._execute_on(
                    db, command, context, now, command_id
                )
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
                    "runtime_mode",
                    "rollout_phase",
                    "readiness_report_hash",
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
        command_id: str,
    ) -> tuple[dict[str, object], str]:
        if isinstance(command, PauseRuntime):
            return {"paused": bool(command.paused)}, (
                "control.runtime_paused"
                if command.paused
                else "control.runtime_resumed"
            )
        if isinstance(command, SetRuntimeMode):
            if self._required_text(command.runtime_mode, "runtime mode") != "SOCIAL_RUNTIME":
                raise CommandValidationError(
                    "SetRuntimeMode only publishes SOCIAL_RUNTIME ownership"
                )
            if self.readiness_gate is None:
                raise CommandValidationError("production readiness gate is unavailable")
            authorize = getattr(self.readiness_gate, "authorize_social_runtime_on", None)
            if not callable(authorize):
                raise CommandValidationError("production readiness gate contract is invalid")
            try:
                data = authorize(
                    db,
                    persona_id=context.persona_id,
                    group_id=context.group_id,
                    operator_id=context.admin_id,
                    reason=context.reason,
                    expected_version=context.expected_version,
                    report_hash=command.readiness_report_hash,
                    confirmation_token=command.old_instance_confirmation_token,
                    now=now,
                )
            except (LookupError, ValueError) as exc:
                raise CommandValidationError(str(exc)) from exc
            return data, "control.runtime_mode_set"
        if isinstance(command, AdvanceRollout):
            if self.readiness_gate is None:
                raise CommandValidationError("production readiness gate is unavailable")
            advance = getattr(self.readiness_gate, "advance_rollout_on", None)
            if not callable(advance):
                raise CommandValidationError("rollout gate contract is invalid")
            try:
                data = advance(
                    db,
                    persona_id=context.persona_id,
                    group_id=context.group_id,
                    operator_id=context.admin_id,
                    reason=context.reason,
                    expected_version=context.expected_version,
                    report_hash=command.readiness_report_hash,
                    now=now,
                )
            except (LookupError, ValueError) as exc:
                raise CommandValidationError(str(exc)) from exc
            return data, "control.rollout_advanced"
        if isinstance(command, ResetState):
            return {
                "target": self._required_text(command.target, "reset target")
            }, "control.state_reset"
        if isinstance(command, CreateConfigDraft):
            config = command.config
            if str(command.config_id).startswith("persona-profile:"):
                profile = GroupmatePersonaProfile.from_mapping(command.config)
                current = self.config_repository._published_on(
                    db,
                    persona_id=context.persona_id,
                    group_id=context.group_id,
                )
                config = {} if current is None else dict(current.config)
                config[PERSONA_PROFILE_CONFIG_KEY] = profile.to_mapping()
            draft = self.config_repository._create_draft_on(
                db,
                command.config_id,
                config,
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
        if isinstance(command, CorrectProfileFact):
            actor_id = self._profile_subject_on(db, context, command.member_ref)
            row = self._profile_fact_on(db, context, actor_id, command.fact_ref)
            summary = " ".join(str(command.new_summary or "").split())
            if not summary or len(summary) > 160:
                raise CommandValidationError(
                    "profile fact correction must contain 1-160 characters"
                )
            revision = context.expected_version + 1
            new_fact_ref = self._opaque_id(
                "profile-fact", str(command.fact_ref), summary, str(now)
            )
            evidence_ref = self._opaque_id(
                "profile-admin", context.admin_id, new_fact_ref
            )
            db.execute(
                "UPDATE profile_facts SET status='superseded',injectable=0,"
                "valid_until=?,updated_at=? WHERE fact_id=?",
                (now, now, str(command.fact_ref)),
            )
            db.execute(
                "INSERT INTO profile_facts(fact_id,persona_id,group_id,subject_id,"
                "category,summary,source_kind,source_actor_id,source_event_ids_json,"
                "confidence,status,evidence_count,valid_from,valid_until,"
                "supersedes_fact_id,injectable,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    new_fact_ref,
                    context.persona_id,
                    context.group_id,
                    actor_id,
                    str(row["category"]),
                    summary,
                    "admin_correction",
                    context.admin_id,
                    self._canonical_json([evidence_ref]),
                    1.0,
                    "confirmed",
                    1,
                    now,
                    None,
                    str(command.fact_ref),
                    1,
                    now,
                ),
            )
            self._record_profile_audit(
                db,
                context,
                subject_id=actor_id,
                action_type="profile_fact_corrected",
                target_id=str(command.fact_ref),
                details={"new_fact_ref": new_fact_ref},
                now=now,
            )
            self._advance_profile_snapshot(
                db,
                context,
                actor_id,
                revision=revision,
                now=now,
                replace=(str(row["summary"]), summary),
            )
            return {
                "member_ref": str(command.member_ref),
                "fact_ref": new_fact_ref,
                "profile_revision": revision,
                "status": "corrected",
            }, "control.profile_fact_corrected"
        if isinstance(command, InvalidateProfileFact):
            actor_id = self._profile_subject_on(db, context, command.member_ref)
            self._profile_fact_on(db, context, actor_id, command.fact_ref)
            revision = context.expected_version + 1
            db.execute(
                "UPDATE profile_facts SET status='rejected',injectable=0,"
                "valid_until=?,updated_at=? WHERE fact_id=?",
                (now, now, str(command.fact_ref)),
            )
            self._record_profile_audit(
                db,
                context,
                subject_id=actor_id,
                action_type="profile_fact_invalidated",
                target_id=str(command.fact_ref),
                details={"status": "rejected"},
                now=now,
            )
            self._advance_profile_snapshot(
                db, context, actor_id, revision=revision, now=now
            )
            return {
                "member_ref": str(command.member_ref),
                "fact_ref": str(command.fact_ref),
                "profile_revision": revision,
                "status": "invalidated",
            }, "control.profile_fact_invalidated"
        if isinstance(command, MergeProfileIdentity):
            source_id = self._profile_subject_on(
                db, context, command.source_member_ref
            )
            target_id = self._profile_subject_on(
                db, context, command.target_member_ref
            )
            stable_target_id = self._required_text(
                command.stable_target_id, "stable target id"
            )
            if source_id == target_id or stable_target_id != target_id:
                raise CommandValidationError(
                    "identity merge requires the explicit stable target id"
                )
            revision = context.expected_version + 1
            self._merge_profile_identity_on(
                db, context, source_id=source_id, target_id=target_id
            )
            self._record_profile_audit(
                db,
                context,
                subject_id=target_id,
                action_type="profile_identity_merged",
                target_id=str(command.source_member_ref),
                details={"source_member_ref": str(command.source_member_ref)},
                now=now,
            )
            self._advance_profile_snapshot(
                db, context, target_id, revision=revision, now=now
            )
            return {
                "member_ref": str(command.target_member_ref),
                "profile_revision": revision,
                "status": "merged",
            }, "control.profile_identity_merged"
        if isinstance(command, SplitProfileIdentity):
            source_id = self._profile_subject_on(
                db, context, command.source_member_ref
            )
            new_actor_id = self._required_text(command.new_stable_id, "new stable id")
            new_name = " ".join(str(command.new_display_name or "").split())
            fact_refs = tuple(
                dict.fromkeys(
                    str(value or "").strip()
                    for value in command.fact_refs
                    if str(value or "").strip()
                )
            )
            if new_actor_id == source_id or not new_name or len(new_name) > 48 or not fact_refs:
                raise CommandValidationError(
                    "identity split requires a distinct stable id, display name and selected facts"
                )
            if db.execute(
                "SELECT 1 FROM participant_directory WHERE persona_id=? AND group_id=? "
                "AND actor_id=?",
                (context.persona_id, context.group_id, new_actor_id),
            ).fetchone() is not None:
                raise CommandValidationError("new stable id already exists in this group")
            for fact_ref in fact_refs:
                self._profile_fact_on(db, context, source_id, fact_ref)
            digest = hashlib.sha256(
                f"{context.group_id}\0{new_actor_id}".encode("utf-8")
            ).hexdigest()[:20]
            member_ref = f"member:{digest}"
            avatar_ref = f"participant:{digest}"
            db.execute(
                "INSERT INTO participant_directory(avatar_ref,member_ref,persona_id,"
                "group_id,actor_id,display_name,updated_at) VALUES(?,?,?,?,?,?,?)",
                (
                    avatar_ref,
                    member_ref,
                    context.persona_id,
                    context.group_id,
                    new_actor_id,
                    new_name,
                    now,
                ),
            )
            db.execute(
                "INSERT OR IGNORE INTO member_identities(persona_id,platform,actor_id,"
                "display_name,avatar_ref,system_roles_json,first_seen_at,last_seen_at,updated_at) "
                "VALUES(?,?,?,?,?,'[]',?,?,?)",
                (
                    context.persona_id,
                    "qq",
                    new_actor_id,
                    new_name,
                    avatar_ref,
                    now,
                    now,
                    now,
                ),
            )
            placeholders = ",".join("?" for _ in fact_refs)
            db.execute(
                f"UPDATE profile_facts SET subject_id=?,updated_at=? "
                f"WHERE fact_id IN ({placeholders})",
                (new_actor_id, now, *fact_refs),
            )
            self._record_profile_audit(
                db,
                context,
                subject_id=new_actor_id,
                action_type="profile_identity_split",
                target_id=member_ref,
                details={"moved_fact_count": len(fact_refs)},
                now=now,
            )
            self._advance_profile_snapshot(
                db,
                context,
                source_id,
                revision=context.expected_version + 1,
                now=now,
            )
            return {
                "member_ref": member_ref,
                "profile_revision": 0,
                "status": "split",
            }, "control.profile_identity_split"
        if isinstance(command, SetMemberStyleDistillation):
            actor_id = self._profile_subject_on(db, context, command.member_ref)
            row = db.execute(
                "SELECT enabled,enabled_at,version,collection_windows_json "
                "FROM member_style_settings WHERE group_id=? AND member_id=?",
                (context.group_id, actor_id),
            ).fetchone()
            was_enabled = bool(row["enabled"]) if row is not None else False
            windows = (
                list(json.loads(str(row["collection_windows_json"])))
                if row is not None
                else []
            )
            if command.enabled and not was_enabled:
                windows.append([now, None])
            elif not command.enabled and was_enabled and windows:
                windows[-1][1] = now
            version = (int(row["version"]) if row is not None else 0) + 1
            enabled_at = (
                int(row["enabled_at"])
                if row is not None and int(row["enabled_at"]) > 0
                else now if command.enabled else 0
            )
            db.execute(
                "INSERT INTO member_style_settings(group_id,member_id,enabled,"
                "enabled_at,updated_by,updated_at,version,collection_windows_json) "
                "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(group_id,member_id) DO UPDATE SET "
                "enabled=excluded.enabled,enabled_at=excluded.enabled_at,"
                "updated_by=excluded.updated_by,updated_at=excluded.updated_at,"
                "version=excluded.version,collection_windows_json=excluded.collection_windows_json",
                (
                    context.group_id,
                    actor_id,
                    int(bool(command.enabled)),
                    enabled_at,
                    context.admin_id,
                    now,
                    version,
                    self._canonical_json(windows),
                ),
            )
            if not command.enabled:
                db.execute(
                    "UPDATE imitation_sessions SET stopped_at=?,stopped_by=?,"
                    "stop_reason='distillation_disabled' WHERE group_id=? "
                    "AND target_member_id=? AND stopped_at IS NULL AND expires_at>?",
                    (now, context.admin_id, context.group_id, actor_id, now),
                )
            self._record_profile_audit(
                db,
                context,
                subject_id=actor_id,
                action_type=(
                    "member_style_distillation_enabled"
                    if command.enabled
                    else "member_style_distillation_disabled"
                ),
                target_id=str(command.member_ref),
                details={"enabled": bool(command.enabled), "setting_version": version},
                now=now,
            )
            return {
                "member_ref": str(command.member_ref),
                "enabled": bool(command.enabled),
                "setting_version": version,
                "status": "ACCUMULATING" if command.enabled else "DISABLED",
            }, "control.member_style_distillation_set"
        if isinstance(command, ConfirmKnowledgeConvention):
            return self._knowledge_mutation(
                KnowledgeRepository.confirm_convention_on,
                db,
                context,
                command_id,
                now,
                convention_id=command.convention_id,
            ), "control.knowledge.convention_confirmed"
        if isinstance(command, RejectKnowledgeConvention):
            return self._knowledge_mutation(
                KnowledgeRepository.reject_convention_on,
                db,
                context,
                command_id,
                now,
                convention_id=command.convention_id,
            ), "control.knowledge.convention_rejected"
        if isinstance(command, SupersedeKnowledgeAlias):
            return self._knowledge_mutation(
                KnowledgeRepository.supersede_group_alias_on,
                db,
                context,
                command_id,
                now,
                alias_id=command.alias_id,
                replacement_entity_id=command.replacement_entity_id,
            ), "control.knowledge.alias_superseded"
        if isinstance(command, DisputeKnowledgeClaim):
            return self._knowledge_mutation(
                KnowledgeRepository.dispute_claim_on,
                db,
                context,
                command_id,
                now,
                claim_id=command.claim_id,
            ), "control.knowledge.claim_disputed"
        if isinstance(command, RetryKnowledgeJob):
            return self._knowledge_mutation(
                KnowledgeRepository.retry_job_on,
                db,
                context,
                command_id,
                now,
                job_id=command.job_id,
            ), "control.knowledge.job_retry_requested"
        if isinstance(command, InvalidateKnowledgeCache):
            return self._knowledge_mutation(
                KnowledgeRepository.invalidate_cache_on,
                db,
                context,
                command_id,
                now,
                entity_id=command.entity_id,
            ), "control.knowledge.cache_invalidated"
        if isinstance(command, SetKnowledgeAmbientCanary):
            if type(command.enabled) is not bool:
                raise CommandValidationError("knowledge canary state must be boolean")
            return self._knowledge_mutation(
                KnowledgeRepository.append_canary_audit_on,
                db,
                context,
                command_id,
                now,
                enabled=command.enabled,
            ), "control.knowledge.ambient_canary_enabled"
        raise CommandValidationError("unsupported control command")

    @staticmethod
    def _knowledge_mutation(
        operation,
        db: sqlite3.Connection,
        context: CommandContext,
        command_id: str,
        now: int,
        **target: object,
    ) -> dict[str, object]:
        try:
            return operation(
                db,
                group_id=str(context.group_id),
                actor_id=context.admin_id,
                reason=context.reason.strip(),
                command_id=command_id,
                now=now,
                **target,
            )
        except LookupError as exc:
            raise CommandNotFound("knowledge target is not available") from exc
        except ValueError as exc:
            raise CommandValidationError(str(exc)) from exc

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
        if isinstance(command, _PROFILE_COMMANDS):
            member_ref = (
                command.target_member_ref
                if isinstance(command, MergeProfileIdentity)
                else command.source_member_ref
                if isinstance(command, SplitProfileIdentity)
                else command.member_ref
            )
            actor_id = self._profile_subject_on(db, context, member_ref)
            row = db.execute(
                "SELECT source_revision FROM profile_snapshots WHERE persona_id=? "
                "AND group_id=? AND subject_id=?",
                (context.persona_id, context.group_id, actor_id),
            ).fetchone()
            return int(row[0]) if row is not None else 0
        if isinstance(command, _MEMBER_STYLE_COMMANDS):
            actor_id = self._profile_subject_on(db, context, command.member_ref)
            row = db.execute(
                "SELECT version FROM member_style_settings "
                "WHERE group_id=? AND member_id=?",
                (context.group_id, actor_id),
            ).fetchone()
            return int(row[0]) if row is not None else 0
        return self._control_version_on(db, context)

    @classmethod
    def _profile_subject_on(
        cls, db: sqlite3.Connection, context: CommandContext, member_ref: object
    ) -> str:
        normalized = cls._required_text(member_ref, "member reference")
        row = db.execute(
            "SELECT actor_id FROM participant_directory WHERE persona_id=? "
            "AND group_id=? AND member_ref=?",
            (context.persona_id, context.group_id, normalized),
        ).fetchone()
        if row is None:
            raise CommandNotFound("profile member is not available")
        return str(row[0])

    @classmethod
    def _profile_fact_on(
        cls,
        db: sqlite3.Connection,
        context: CommandContext,
        actor_id: str,
        fact_ref: object,
    ):
        normalized = cls._required_text(fact_ref, "profile fact reference")
        row = db.execute(
            "SELECT * FROM profile_facts WHERE fact_id=? AND persona_id=? "
            "AND group_id=? AND subject_id=?",
            (normalized, context.persona_id, context.group_id, actor_id),
        ).fetchone()
        if row is None:
            raise CommandNotFound("profile fact is not available")
        return row

    @classmethod
    def _record_profile_audit(
        cls,
        db: sqlite3.Connection,
        context: CommandContext,
        *,
        subject_id: str,
        action_type: str,
        target_id: str,
        details: Mapping[str, object],
        now: int,
    ) -> None:
        audit_id = cls._opaque_id(
            "profile-audit", context.admin_id, action_type, target_id, str(now)
        )
        db.execute(
            "INSERT INTO profile_audit(audit_id,persona_id,group_id,subject_id,"
            "actor_id,action_type,target_id,audit_json,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (
                audit_id,
                context.persona_id,
                context.group_id,
                subject_id,
                context.admin_id,
                action_type,
                target_id,
                cls._canonical_json(dict(details)),
                now,
            ),
        )

    @classmethod
    def _advance_profile_snapshot(
        cls,
        db: sqlite3.Connection,
        context: CommandContext,
        actor_id: str,
        *,
        revision: int,
        now: int,
        replace: tuple[str, str] | None = None,
    ) -> None:
        row = db.execute(
            "SELECT snapshot_json FROM profile_snapshots WHERE persona_id=? "
            "AND group_id=? AND subject_id=?",
            (context.persona_id, context.group_id, actor_id),
        ).fetchone()
        if row is None:
            return
        payload = dict(json.loads(str(row[0])))
        if replace is not None:
            old, new = replace
            for key in ("individual_fingerprints", "preferences_and_boundaries"):
                payload[key] = [
                    new if value == old else value for value in payload.get(key, [])
                ]
            if payload.get("one_line_portrait") == old:
                payload["one_line_portrait"] = new
        payload["source_revision"] = revision
        payload["generated_at"] = now
        db.execute(
            "UPDATE profile_snapshots SET snapshot_json=?,source_revision=?,"
            "generated_at=? WHERE persona_id=? AND group_id=? AND subject_id=?",
            (
                cls._canonical_json(payload),
                revision,
                now,
                context.persona_id,
                context.group_id,
                actor_id,
            ),
        )

    @classmethod
    def _merge_profile_identity_on(
        cls,
        db: sqlite3.Connection,
        context: CommandContext,
        *,
        source_id: str,
        target_id: str,
    ) -> None:
        db.execute(
            "UPDATE profile_facts SET subject_id=? WHERE persona_id=? AND group_id=? "
            "AND subject_id=?",
            (target_id, context.persona_id, context.group_id, source_id),
        )
        episodes = db.execute(
            "SELECT episode_id,participants_json FROM profile_episodes "
            "WHERE persona_id=? AND group_id=?",
            (context.persona_id, context.group_id),
        ).fetchall()
        for episode in episodes:
            participants = list(
                dict.fromkeys(
                    target_id if value == source_id else value
                    for value in json.loads(str(episode["participants_json"]))
                )
            )
            db.execute(
                "UPDATE profile_episodes SET participants_json=? WHERE episode_id=?",
                (cls._canonical_json(participants), str(episode["episode_id"])),
            )
        db.execute(
            "UPDATE social_edges SET source_member_id=? WHERE persona_id=? "
            "AND group_id=? AND source_member_id=?",
            (target_id, context.persona_id, context.group_id, source_id),
        )
        db.execute(
            "UPDATE social_edges SET target_member_id=? WHERE persona_id=? "
            "AND group_id=? AND target_member_id=?",
            (target_id, context.persona_id, context.group_id, source_id),
        )
        db.execute(
            "DELETE FROM social_edges WHERE persona_id=? AND group_id=? "
            "AND source_member_id=target_member_id",
            (context.persona_id, context.group_id),
        )
        aliases = db.execute(
            "SELECT alias,alias_type,confidence,source_event_id,status,first_seen_at,"
            "last_seen_at FROM member_aliases WHERE persona_id=? AND group_id=? "
            "AND actor_id=?",
            (context.persona_id, context.group_id, source_id),
        ).fetchall()
        for alias in aliases:
            db.execute(
                "INSERT OR IGNORE INTO member_aliases(persona_id,group_id,actor_id,"
                "alias,alias_type,confidence,source_event_id,status,first_seen_at,last_seen_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    context.persona_id,
                    context.group_id,
                    target_id,
                    alias["alias"],
                    alias["alias_type"],
                    alias["confidence"],
                    alias["source_event_id"],
                    alias["status"],
                    alias["first_seen_at"],
                    alias["last_seen_at"],
                ),
            )
        db.execute(
            "DELETE FROM member_aliases WHERE persona_id=? AND group_id=? AND actor_id=?",
            (context.persona_id, context.group_id, source_id),
        )
        db.execute(
            "DELETE FROM profile_snapshots WHERE persona_id=? AND group_id=? AND subject_id=?",
            (context.persona_id, context.group_id, source_id),
        )
        db.execute(
            "DELETE FROM profile_preferences WHERE persona_id=? AND group_id=? AND subject_id=?",
            (context.persona_id, context.group_id, source_id),
        )
        db.execute(
            "DELETE FROM participant_directory WHERE persona_id=? AND group_id=? AND actor_id=?",
            (context.persona_id, context.group_id, source_id),
        )

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
    "ConfirmKnowledgeConvention",
    "CorrectSocialState",
    "CorrectProfileFact",
    "CreateConfigDraft",
    "DryRunConfig",
    "DisputeKnowledgeClaim",
    "ExpectedVersionConflict",
    "ForgetMemory",
    "InvalidateKnowledgeCache",
    "InvalidateProfileFact",
    "LinkIdentity",
    "MergeProfileIdentity",
    "PauseRuntime",
    "PublishConfig",
    "RejectKnowledgeConvention",
    "ResetState",
    "RetryKnowledgeJob",
    "RestoreConfig",
    "ReviewEvidence",
    "ReviewShadowDecision",
    "SplitProfileIdentity",
    "SetMemberStyleDistillation",
    "SetKnowledgeAmbientCanary",
    "SupersedeKnowledgeAlias",
    "ValidateConfig",
)
