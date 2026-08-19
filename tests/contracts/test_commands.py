from __future__ import annotations

import pytest

from groupmate.social_runtime.control.commands import (
    ApproveCalibration,
    CancelTask,
    CommandConfirmationRequired,
    CommandContext,
    CommandForbidden,
    CommandIdentityConflict,
    CommandNotFound,
    CommandService,
    CommandValidationError,
    CorrectSocialState,
    ExpectedVersionConflict,
    ForgetMemory,
    LinkIdentity,
    PauseRuntime,
    ResetState,
    ReviewEvidence,
)
from groupmate.social_runtime.control.projections import ProjectionConsumer
from groupmate.social_runtime.persistence.schema import connect_database


def _service(path):
    return CommandService(
        path,
        persona_id="aemeath",
        group_ids=("group-1",),
        admin_ids=("admin:root",),
    )


def _context(**overrides):
    values = {
        "admin_id": "admin:root",
        "persona_id": "aemeath",
        "group_id": "group-1",
        "expected_version": 0,
        "reason": "operator requested change",
        "confirmed": True,
    }
    values.update(overrides)
    return CommandContext(**values)


def _action_count(path) -> int:
    with connect_database(path) as db:
        return int(db.execute("SELECT COUNT(*) FROM governance_actions").fetchone()[0])


def _seed_ref(
    path,
    entity_ref: str,
    *,
    projection_name: str,
    group_id: str = "group-1",
    evidence_ref: bool = False,
) -> None:
    ProjectionConsumer(path, projection_name)
    with connect_database(path) as db:
        db.execute(
            "INSERT INTO control_projection_items("
            "projection_name, entity_key, entity_ref, persona_id, group_id, kind, "
            "projection_version, summary_json, evidence_refs_json, as_of"
            ") VALUES(?, ?, ?, 'aemeath', ?, 'test.fixture', 1, '{}', ?, 1)",
            (
                projection_name,
                f"key:{projection_name}:{entity_ref}",
                (
                    f"entity:{projection_name}:{entity_ref}"
                    if evidence_ref
                    else entity_ref
                ),
                group_id,
                f'["{entity_ref}"]' if evidence_ref else "[]",
            ),
        )


def _seed_command_refs(path, command) -> None:
    if isinstance(command, ForgetMemory | CorrectSocialState):
        _seed_ref(path, command.entity_ref, projection_name="people")
    elif isinstance(command, LinkIdentity):
        _seed_ref(path, command.source_ref, projection_name="people")
        _seed_ref(path, command.target_ref, projection_name="people")
    elif isinstance(command, CancelTask):
        _seed_ref(path, command.entity_ref, projection_name="tasks")
    elif isinstance(command, ApproveCalibration):
        _seed_ref(path, command.entity_ref, projection_name="governance")


def test_command_authority_scope_reason_and_version_fail_before_mutation(tmp_path):
    path = tmp_path / "groupmate-social-runtime-v2.db"
    service = _service(path)
    command = PauseRuntime(paused=True, command_id="cmd:pause")

    with pytest.raises(CommandForbidden) as forbidden:
        service.execute(command, _context(admin_id="member:1"))
    assert forbidden.value.status_code == 403

    with pytest.raises(CommandNotFound) as hidden_scope:
        service.execute(command, _context(group_id="group-other"))
    assert hidden_scope.value.status_code == 404

    with pytest.raises(CommandValidationError) as missing_reason:
        service.execute(command, _context(reason="  "))
    assert missing_reason.value.status_code == 400

    with pytest.raises(ExpectedVersionConflict) as stale:
        service.execute(command, _context(expected_version=1))
    assert stale.value.status_code == 409
    assert stale.value.current_version == 0
    assert _action_count(path) == 0


def test_duplicate_command_id_is_idempotent_but_cannot_change_identity(tmp_path):
    path = tmp_path / "groupmate-social-runtime-v2.db"
    service = _service(path)
    context = _context()

    first = service.execute(
        PauseRuntime(paused=True, command_id="cmd:same"), context
    )
    replay = service.execute(
        PauseRuntime(paused=True, command_id="cmd:same"), context
    )

    assert replay == first
    assert first.event.event_type == "control.runtime_paused"
    assert first.event.payload["paused"] is True
    assert first.version == 1
    assert _action_count(path) == 1

    with pytest.raises(CommandIdentityConflict) as collision:
        service.execute(
            PauseRuntime(paused=False, command_id="cmd:same"),
            _context(expected_version=1),
        )
    assert collision.value.status_code == 409

    with pytest.raises(CommandIdentityConflict):
        service.execute(
            PauseRuntime(paused=True, command_id="cmd:same"),
            _context(expected_version=1, reason="different audit reason"),
        )
    assert _action_count(path) == 1


@pytest.mark.parametrize(
    ("command", "event_type"),
    (
        (ResetState("scene", command_id="cmd:reset"), "control.state_reset"),
        (
            ForgetMemory("memory:opaque", command_id="cmd:forget"),
            "control.memory_forgotten",
        ),
        (
            CorrectSocialState(
                "relationship:opaque",
                {"warmth": -2},
                command_id="cmd:correct",
            ),
            "control.social_state_corrected",
        ),
        (
            LinkIdentity(
                "person:one",
                "person:two",
                ("public_facts",),
                command_id="cmd:link",
            ),
            "control.identity_linked",
        ),
        (
            CancelTask("task:opaque", command_id="cmd:cancel"),
            "control.task_cancel_requested",
        ),
        (
            ApproveCalibration(
                "calibration:opaque", command_id="cmd:calibration"
            ),
            "control.calibration_approved",
        ),
    ),
)
def test_high_impact_commands_require_confirmation_and_emit_scoped_events(
    tmp_path, command, event_type
):
    path = tmp_path / command.command_id.replace(":", "-") / "runtime.db"
    service = _service(path)
    _seed_command_refs(path, command)

    with pytest.raises(CommandConfirmationRequired) as unconfirmed:
        service.execute(command, _context(confirmed=False))
    assert unconfirmed.value.status_code == 400

    result = service.execute(command, _context())

    assert result.event.event_type == event_type
    assert result.event.persona_id == "aemeath"
    assert result.event.group_id == "group-1"
    assert result.event.actor_id == "admin:root"
    assert result.event.payload["reason"] == "operator requested change"


def test_review_command_is_audited_without_direct_domain_table_mutation(tmp_path):
    path = tmp_path / "groupmate-social-runtime-v2.db"
    service = _service(path)
    _seed_ref(
        path,
        "evidence:opaque",
        projection_name="people",
        evidence_ref=True,
    )

    result = service.execute(
        ReviewEvidence(
            "evidence:opaque",
            decision="accept",
            command_id="cmd:review",
        ),
        _context(),
    )

    assert result.event.event_type == "control.evidence_reviewed"
    with connect_database(path) as db:
        assert db.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0
        assert (
            db.execute("SELECT COUNT(*) FROM relationship_projection").fetchone()[0]
            == 0
        )
        assert db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
        action = db.execute(
            "SELECT actor_id, action_type, reason FROM governance_actions"
        ).fetchone()
    assert tuple(action) == (
        "admin:root",
        "evidence_reviewed",
        "operator requested change",
    )


def test_cross_group_entity_reference_returns_404_without_audit(tmp_path):
    path = tmp_path / "groupmate-social-runtime-v2.db"
    service = CommandService(
        path,
        persona_id="aemeath",
        group_ids=("group-1", "group-2"),
        admin_ids=("admin:root",),
    )
    _seed_ref(
        path,
        "memory:group-one",
        projection_name="people",
        group_id="group-1",
    )

    with pytest.raises(CommandNotFound) as hidden:
        service.execute(
            ForgetMemory("memory:group-one", command_id="cmd:cross-group"),
            _context(group_id="group-2"),
        )

    assert hidden.value.status_code == 404
    assert _action_count(path) == 0
