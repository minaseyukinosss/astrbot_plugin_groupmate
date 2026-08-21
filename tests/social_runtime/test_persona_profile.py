from __future__ import annotations

import asyncio

import pytest

from groupmate.social_runtime.contracts import RuntimeMode, SocialEventEnvelope
from groupmate.social_runtime.control.commands import (
    CommandContext,
    CommandService,
    CreateConfigDraft,
)
from groupmate.social_runtime.control.config_versions import (
    ConfigSnapshot,
    ConfigVersionRepository,
)
from groupmate.social_runtime.manager import SocialRuntimeManager
from groupmate.social_runtime.persona.profile import GroupmatePersonaProfile
from tests.factories import social_event_values


def test_default_plugin_persona_is_complete_and_group_chat_oriented():
    profile = GroupmatePersonaProfile.default()
    payload = profile.to_mapping()

    assert payload["identity"]["name"] == "Groupmate"
    assert payload["identity"]["role"]
    assert payload["participation"]["initiative"] == "balanced"
    assert payload["expression"]["reply_length"] == "short"
    assert payload["tools"]["autonomy"] == "read_only"


def test_plugin_persona_rejects_unknown_sections_and_invalid_choices():
    payload = GroupmatePersonaProfile.default().to_mapping()
    payload["unknown"] = {}
    with pytest.raises(ValueError, match="unknown persona sections"):
        GroupmatePersonaProfile.from_mapping(payload)

    payload = GroupmatePersonaProfile.default().to_mapping()
    payload["participation"]["initiative"] = "always"
    with pytest.raises(ValueError, match="initiative"):
        GroupmatePersonaProfile.from_mapping(payload)


def test_persona_draft_preserves_existing_behavior_calibration(tmp_path):
    path = tmp_path / "groupmate-social-runtime-v2.db"
    repository = ConfigVersionRepository(path)
    draft = repository.create_draft(
        "calibration:group-1",
        {"attention_threshold": 0.62},
        persona_id="groupmate:default",
        group_id="group-1",
        now=1,
    )
    repository.validate(
        draft.config_id,
        persona_id="groupmate:default",
        group_id="group-1",
    )
    repository.publish(
        draft.config_id,
        persona_id="groupmate:default",
        group_id="group-1",
        expected_version=0,
    )

    profile = GroupmatePersonaProfile.default().to_mapping()
    CommandService(
        path,
        persona_id="groupmate:default",
        group_ids=("group-1",),
        admin_ids=("admin:root",),
    ).execute(
        CreateConfigDraft("persona-profile:group-1", profile),
        CommandContext(
            admin_id="admin:root",
            persona_id="groupmate:default",
            group_id="group-1",
            expected_version=1,
            reason="更新插件内置人格",
            confirmed=False,
        ),
    )

    saved = repository.load("persona-profile:group-1")
    assert saved.config["attention_threshold"] == 0.62
    assert saved.config["persona_profile"] == profile

def test_published_plugin_persona_is_frozen_into_cognition_context(tmp_path):
    profile = GroupmatePersonaProfile.default().to_mapping()
    profile["identity"]["name"] = "小群友"

    class InspectingWorker:
        name = "direct_interaction"

        def __init__(self):
            self.context = None

        async def observe(self, frame, context):
            self.context = context
            return ()

    async def scenario():
        worker = InspectingWorker()
        manager = SocialRuntimeManager(
            database_path=tmp_path / "groupmate-social-runtime-v2.db",
            persona_id="groupmate:default",
            mode=RuntimeMode.SHADOW,
            enabled_groups=("group-1",),
            cognition_workers={worker.name: worker},
            persona_profile_loader=lambda group_id: ConfigSnapshot(
                7, {"persona_profile": profile, "attention_threshold": 0.5}
            ),
        )
        await manager.start()
        await manager.ingest(
            SocialEventEnvelope.create(
                **social_event_values(
                    event_id="qq:profile",
                    source_message_id="profile",
                    persona_id="groupmate:default",
                    group_id="group-1",
                    actor_id="member-1",
                    occurred_at=100,
                    received_at=100,
                    correlation_id="corr:profile",
                    payload={"text": "在吗", "direct_address": True},
                )
            )
        )
        await manager.drain(now=100)
        await manager.close()
        return worker.context

    context = asyncio.run(scenario())
    assert context.config_version == 7
    assert context.world_summary["persona_profile"]["identity"]["name"] == "小群友"
