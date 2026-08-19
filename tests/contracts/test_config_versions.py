from __future__ import annotations

import pytest

from groupmate.social_runtime.control.commands import (
    CommandContext,
    CommandNotFound,
    CommandService,
    CreateConfigDraft,
    DryRunConfig,
    ExpectedVersionConflict,
    PublishConfig,
    RestoreConfig,
    ValidateConfig,
)
from groupmate.social_runtime.control.config_versions import (
    ConfigStatus,
    ConfigVersionRepository,
)
from groupmate.social_runtime.persistence.schema import connect_database


def _context(expected_version: int, *, confirmed: bool = False):
    return CommandContext(
        admin_id="admin:root",
        persona_id="aemeath",
        group_id="group-1",
        expected_version=expected_version,
        reason="publish tested behavior configuration",
        confirmed=confirmed,
    )


def _service(path, repository=None):
    return CommandService(
        path,
        persona_id="aemeath",
        group_ids=("group-1",),
        admin_ids=("admin:root",),
        config_repository=repository,
    )


def _draft_validate_publish(service, version: int, config: dict[str, object]):
    config_id = "behavior"
    service.execute(
        CreateConfigDraft(config_id, config, command_id=f"draft:{version}"),
        _context(version - 1),
    )
    service.execute(
        ValidateConfig(config_id, command_id=f"validate:{version}"),
        _context(version - 1),
    )
    return service.execute(
        PublishConfig(config_id, command_id=f"publish:{version}"),
        _context(version - 1, confirmed=True),
    )


def test_config_lifecycle_is_versioned_and_new_cycles_only_read_new_publish(tmp_path):
    path = tmp_path / "groupmate-social-runtime-v2.db"
    repository = ConfigVersionRepository(path)
    service = _service(path, repository)

    first = _draft_validate_publish(
        service,
        1,
        {"style": {"reply_length": "short"}, "autonomy": {"enabled": False}},
    )
    frozen_cycle = repository.snapshot(persona_id="aemeath", group_id="group-1")
    second = _draft_validate_publish(
        service,
        2,
        {"style": {"reply_length": "medium"}, "autonomy": {"enabled": False}},
    )

    assert first.data["status"] == ConfigStatus.PUBLISHED.value
    assert first.event.payload["status"] == ConfigStatus.PUBLISHED.value
    assert first.event.payload["config_version"] == 1
    assert second.data["version"] == 2
    assert repository.load("behavior", 1).status is ConfigStatus.SUPERSEDED
    assert repository.load("behavior", 2).status is ConfigStatus.PUBLISHED
    assert repository.published_version() == 2
    assert frozen_cycle.version == 1
    assert frozen_cycle.config["style"]["reply_length"] == "short"
    assert repository.snapshot(
        persona_id="aemeath", group_id="group-1"
    ).config["style"]["reply_length"] == "medium"


def test_publish_expected_version_conflict_preserves_current_version(tmp_path):
    path = tmp_path / "groupmate-social-runtime-v2.db"
    repository = ConfigVersionRepository(path)
    service = _service(path, repository)
    for version in range(1, 4):
        _draft_validate_publish(
            service,
            version,
            {"style": {"reply_length": f"length-{version}"}},
        )
    service.execute(
        CreateConfigDraft(
            "behavior",
            {"style": {"reply_length": "next"}},
            command_id="draft:4",
        ),
        _context(3),
    )
    service.execute(
        ValidateConfig("behavior", command_id="validate:4"),
        _context(3),
    )

    with pytest.raises(ExpectedVersionConflict) as conflict:
        service.execute(
            PublishConfig("behavior", command_id="publish:4"),
            _context(2, confirmed=True),
        )

    assert conflict.value.current_version == 3
    assert repository.published_version() == 3
    assert repository.load("behavior", 4).status is ConfigStatus.VALIDATED


def test_publish_failure_keeps_validated_draft_and_previous_publish(tmp_path):
    path = tmp_path / "groupmate-social-runtime-v2.db"
    should_fail = {"value": False}

    def publish_hook(_version):
        if should_fail["value"]:
            raise RuntimeError("injected publish failure")

    repository = ConfigVersionRepository(path, publish_hook=publish_hook)
    service = _service(path, repository)
    _draft_validate_publish(
        service, 1, {"style": {"reply_length": "short"}}
    )
    service.execute(
        CreateConfigDraft(
            "behavior",
            {"style": {"reply_length": "medium"}},
            command_id="draft:2",
        ),
        _context(1),
    )
    service.execute(
        ValidateConfig("behavior", command_id="validate:2"),
        _context(1),
    )
    should_fail["value"] = True

    with pytest.raises(RuntimeError, match="injected publish failure"):
        service.execute(
            PublishConfig("behavior", command_id="publish:2"),
            _context(1, confirmed=True),
        )

    assert repository.published_version() == 1
    assert repository.load("behavior", 1).status is ConfigStatus.PUBLISHED
    assert repository.load("behavior", 2).status is ConfigStatus.VALIDATED


def test_dry_run_returns_literal_semantic_diff_without_changing_config_rows(tmp_path):
    path = tmp_path / "groupmate-social-runtime-v2.db"
    repository = ConfigVersionRepository(path)
    service = _service(path, repository)
    _draft_validate_publish(
        service, 1, {"style": {"reply_length": "short"}}
    )
    service.execute(
        CreateConfigDraft(
            "behavior",
            {"style": {"reply_length": "medium"}},
            command_id="draft:2",
        ),
        _context(1),
    )
    service.execute(
        ValidateConfig("behavior", command_id="validate:2"),
        _context(1),
    )
    with connect_database(path) as db:
        before = tuple(
            tuple(row)
            for row in db.execute(
                "SELECT config_id, version, status, config_json "
                "FROM config_versions ORDER BY version"
            ).fetchall()
        )

    result = service.execute(
        DryRunConfig(
            "behavior",
            historical_events=(
                {"event_id": "fixture:1", "event_type": "platform.message"},
            ),
            worker_outputs=(
                {"worker": "dialogue", "observation": "fixture-output"},
            ),
            command_id="dry-run:2",
        ),
        _context(1),
    )

    assert result.data["changed"] == [
        {
            "after": "medium",
            "before": "short",
            "path": "style.reply_length",
        }
    ]
    assert result.data["historical_event_count"] == 1
    assert result.data["worker_output_count"] == 1
    with connect_database(path) as db:
        after = tuple(
            tuple(row)
            for row in db.execute(
                "SELECT config_id, version, status, config_json "
                "FROM config_versions ORDER BY version"
            ).fetchall()
        )
    assert after == before


def test_restore_republishes_old_content_as_new_immutable_version(tmp_path):
    path = tmp_path / "groupmate-social-runtime-v2.db"
    repository = ConfigVersionRepository(path)
    service = _service(path, repository)
    _draft_validate_publish(
        service, 1, {"style": {"reply_length": "short"}}
    )
    _draft_validate_publish(
        service, 2, {"style": {"reply_length": "long"}}
    )

    restored = service.execute(
        RestoreConfig("behavior", source_version=1, command_id="restore:1"),
        _context(2, confirmed=True),
    )

    assert restored.data["version"] == 3
    assert repository.published_version() == 3
    assert repository.load("behavior", 2).status is ConfigStatus.SUPERSEDED
    assert repository.load("behavior", 3).config == {
        "style": {"reply_length": "short"}
    }


def test_published_versions_are_scope_monotonic_across_distinct_draft_ids(tmp_path):
    path = tmp_path / "groupmate-social-runtime-v2.db"
    repository = ConfigVersionRepository(path)
    service = _service(path, repository)

    for version, config_id in ((1, "draft:one"), (2, "draft:two")):
        service.execute(
            CreateConfigDraft(
                config_id,
                {"style": {"reply_length": f"length-{version}"}},
                command_id=f"create-distinct:{version}",
            ),
            _context(version - 1),
        )
        service.execute(
            ValidateConfig(
                config_id, command_id=f"validate-distinct:{version}"
            ),
            _context(version - 1),
        )
        published = service.execute(
            PublishConfig(
                config_id, command_id=f"publish-distinct:{version}"
            ),
            _context(version - 1, confirmed=True),
        )
        assert published.data["version"] == version

    assert repository.published_version() == 2
    assert repository.load("draft:one", 1).status is ConfigStatus.SUPERSEDED
    assert repository.load("draft:two", 2).status is ConfigStatus.PUBLISHED


def test_cross_group_config_id_is_hidden_as_404(tmp_path):
    path = tmp_path / "groupmate-social-runtime-v2.db"
    repository = ConfigVersionRepository(path)
    service = CommandService(
        path,
        persona_id="aemeath",
        group_ids=("group-1", "group-2"),
        admin_ids=("admin:root",),
        config_repository=repository,
    )
    service.execute(
        CreateConfigDraft(
            "group-one-config",
            {"style": {"reply_length": "short"}},
            command_id="create:group-one",
        ),
        _context(0),
    )

    with pytest.raises(CommandNotFound) as hidden:
        service.execute(
            ValidateConfig("group-one-config", command_id="validate:group-two"),
            CommandContext(
                admin_id="admin:root",
                persona_id="aemeath",
                group_id="group-2",
                expected_version=0,
                reason="attempt cross-group access",
                confirmed=False,
            ),
        )

    assert hidden.value.status_code == 404
