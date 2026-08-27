from __future__ import annotations

import json

import pytest

from groupmate.adapters.participants import ParticipantDirectory
from groupmate.adapters.web_api import ControlPlaneWebAPI
from groupmate.social_runtime.control.commands import (
    CommandContext,
    CommandForbidden,
    CommandService,
    SetMemberStyleDistillation,
)
from groupmate.social_runtime.control.queries import ProjectionQueries
from groupmate.social_runtime.profile.speech_style import MemberSpeechStyle
from groupmate.social_runtime.profile.style_repository import MemberStyleRepository


def _context(**overrides):
    values = {
        "admin_id": "admin:root",
        "persona_id": "aemeath",
        "group_id": "group-1",
        "expected_version": 0,
        "reason": "启用群友风格观察",
        "confirmed": True,
    }
    values.update(overrides)
    return CommandContext(**values)


def _seed(path):
    directory = ParticipantDirectory(path, path.parent / "avatars")
    return directory.remember_actor(
        persona_id="aemeath",
        group_id="group-1",
        actor_id="10001",
        display_name="群友甲",
        updated_at=100,
    )


def test_only_control_admin_can_enable_member_distillation(tmp_path):
    path = tmp_path / "groupmate-social-runtime-v2.db"
    member = _seed(path)
    service = CommandService(
        path,
        persona_id="aemeath",
        group_ids=("group-1",),
        admin_ids=("admin:root",),
        clock=lambda: 200,
    )

    with pytest.raises(CommandForbidden):
        service.execute(
            SetMemberStyleDistillation(member["member_ref"], True),
            _context(admin_id="10001"),
        )

    result = service.execute(
        SetMemberStyleDistillation(member["member_ref"], True), _context()
    )

    assert result.data == {
        "member_ref": member["member_ref"],
        "enabled": True,
        "setting_version": 1,
        "status": "ACCUMULATING",
    }
    assert "10001" not in json.dumps(result.data)
    assert MemberStyleRepository(path).setting("group-1", "10001").enabled is True


def test_profile_query_exposes_safe_style_summary_without_raw_evidence(tmp_path):
    path = tmp_path / "groupmate-social-runtime-v2.db"
    member = _seed(path)
    styles = MemberStyleRepository(path)
    styles.set_enabled("group-1", "10001", enabled=True, updated_by="admin", now=100)
    styles.publish(
        MemberSpeechStyle(
            group_id="group-1", member_id="10001", version=1, status="READY",
            opening_patterns=("先直接表态",), progression_patterns=("随后补理由",),
            closing_patterns=("自然收口",), length_rhythm="一到两句为主",
            directness="直接", disagreement_style="指出问题再解释",
            play_style="从现场形成调侃", care_style="给具体动作",
            addressing_style="需要时称呼", particles_punctuation="少量语气词",
            stable_traits=("结论在前",), occasional_traits=("偶尔省略主语",),
            evidence_event_ids=("secret-event-1", "secret-event-2"),
            eligible_message_count=40, active_day_count=5,
            scene_types=("answer", "banter", "care"), generated_at=200,
        )
    )

    result = ProjectionQueries(path).profile(
        persona_id="aemeath", group_id="group-1", member_ref=member["member_ref"]
    )
    summary = result["items"][0]["summary"]["speech_style"]
    encoded = json.dumps(summary, ensure_ascii=False)

    assert summary["status"] == "READY"
    assert summary["stable_traits"] == ["结论在前"]
    assert summary["eligible_message_count"] == 40
    assert "secret-event" not in encoded
    assert "10001" not in encoded


def test_web_api_parses_governed_member_style_toggle():
    command = ControlPlaneWebAPI._parse_command(
        {
            "type": "member_style_distillation_set",
            "payload": {"member_ref": "member:abc", "enabled": True},
        }
    )

    assert command == SetMemberStyleDistillation("member:abc", True)
