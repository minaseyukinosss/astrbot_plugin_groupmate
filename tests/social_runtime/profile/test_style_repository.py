from __future__ import annotations

from groupmate.social_runtime.profile.speech_style import MemberSpeechStyle
from groupmate.social_runtime.profile.style_repository import MemberStyleRepository


def _style(*, group_id: str = "group-1", member_id: str = "member-1", version: int = 1):
    return MemberSpeechStyle(
        group_id=group_id,
        member_id=member_id,
        version=version,
        status="READY",
        opening_patterns=("先直接表态",),
        progression_patterns=("短句后补充理由",),
        closing_patterns=("自然收口",),
        length_rhythm="以一到两句为主",
        directness="直接但不生硬",
        disagreement_style="先反问再指出问题",
        play_style="从现场用词形成轻微调侃",
        care_style="给出具体动作",
        addressing_style="只在需要明确对象时称呼",
        particles_punctuation="少量语气词，不连续感叹",
        stable_traits=("结论在前",),
        occasional_traits=("偶尔省略主语",),
        evidence_event_ids=("event-1", "event-2", "event-3"),
        eligible_message_count=40,
        active_day_count=5,
        scene_types=("answer", "banter", "care"),
        generated_at=100,
    )


def test_setting_defaults_off_and_enable_timestamp_is_preserved(tmp_path):
    repository = MemberStyleRepository(tmp_path / "groupmate-social-runtime-v2.db")

    assert repository.setting("group-1", "member-1").enabled is False
    enabled = repository.set_enabled(
        "group-1", "member-1", enabled=True, updated_by="admin", now=100
    )
    refreshed = repository.set_enabled(
        "group-1", "member-1", enabled=True, updated_by="admin", now=120
    )

    assert enabled.enabled_at == refreshed.enabled_at == 100
    assert refreshed.version == 2


def test_disabled_member_style_is_retained_but_not_selectable(tmp_path):
    repository = MemberStyleRepository(tmp_path / "groupmate-social-runtime-v2.db")
    repository.set_enabled(
        "group-1", "member-1", enabled=True, updated_by="admin", now=100
    )
    repository.publish(_style())
    assert repository.latest_ready("group-1", "member-1") == _style()

    repository.set_enabled(
        "group-1", "member-1", enabled=False, updated_by="admin", now=200
    )

    assert repository.style("group-1", "member-1", 1) == _style()
    assert repository.latest_ready("group-1", "member-1") is None


def test_starting_session_replaces_current_group_only(tmp_path):
    repository = MemberStyleRepository(tmp_path / "groupmate-social-runtime-v2.db")
    for member_id in ("member-1", "member-2"):
        repository.set_enabled(
            "group-1", member_id, enabled=True, updated_by="admin", now=100
        )
        repository.publish(_style(member_id=member_id))

    first = repository.start_session(
        group_id="group-1",
        target_member_id="member-1",
        target_display_name="A",
        style_version=1,
        started_by="admin",
        started_at=110,
        expires_at=200,
    )
    second = repository.start_session(
        group_id="group-1",
        target_member_id="member-2",
        target_display_name="B",
        style_version=1,
        started_by="admin",
        started_at=120,
        expires_at=220,
    )

    assert first.session_id != second.session_id
    assert repository.active_session("group-1", now=150) == second
    assert repository.active_session("group-2", now=150) is None


def test_active_session_expires_on_read(tmp_path):
    repository = MemberStyleRepository(tmp_path / "groupmate-social-runtime-v2.db")
    repository.set_enabled(
        "group-1", "member-1", enabled=True, updated_by="admin", now=100
    )
    repository.publish(_style())
    repository.start_session(
        group_id="group-1",
        target_member_id="member-1",
        target_display_name="A",
        style_version=1,
        started_by="admin",
        started_at=100,
        expires_at=200,
    )

    assert repository.active_session("group-1", now=199) is not None
    assert repository.active_session("group-1", now=200) is None


def test_only_target_or_admin_can_stop_a_session(tmp_path):
    repository = MemberStyleRepository(tmp_path / "groupmate-social-runtime-v2.db")
    repository.set_enabled(
        "group-1", "member-1", enabled=True, updated_by="admin", now=100
    )
    repository.publish(_style())
    repository.start_session(
        group_id="group-1", target_member_id="member-1", target_display_name="A",
        style_version=1, started_by="admin", started_at=100, expires_at=200,
    )

    assert repository.stop_session(
        "group-1", stopped_by="member-2", now=120, requester_is_admin=False
    ) is None
    stopped = repository.stop_session(
        "group-1", stopped_by="member-1", now=121, requester_is_admin=False
    )

    assert stopped is not None
    assert stopped.stop_reason == "target_opt_out"
    assert repository.active_session("group-1", now=122) is None
