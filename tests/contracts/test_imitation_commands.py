from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from groupmate.adapters.imitation_commands import (
    ImitationSessionController,
    ImitationCommandError,
    ImitationCommandInterpreter,
)
from groupmate.adapters.participants import ParticipantDirectory
from groupmate.social_runtime.contracts import SocialEventEnvelope
from groupmate.social_runtime.profile.speech_style import MemberSpeechStyle
from groupmate.social_runtime.profile.style_repository import MemberStyleRepository


TZ = ZoneInfo("Asia/Shanghai")


def _timestamp(value: str) -> int:
    return int(datetime.strptime(value, "%Y-%m-%d %H:%M").replace(tzinfo=TZ).timestamp())


def _event(text, *, actor="admin", mentions=(), mentions_bot=True, bot_id="bot"):
    return SocialEventEnvelope.create(
        event_id=f"event:{actor}:{text}", event_type="platform.message",
        occurred_at=_timestamp("2026-08-27 14:00"), received_at=_timestamp("2026-08-27 14:00"),
        persona_id="persona", group_id="group-1", actor_id=actor,
        source_message_id="message-1", correlation_id="message-1", causation_id=None,
        payload={"text": text, "mentions": list(mentions), "mentions_bot": mentions_bot,
                 "bot_id": bot_id, "sender": {"id": actor, "name": actor}},
    )


def _interpreter(tmp_path):
    directory = ParticipantDirectory(
        tmp_path / "groupmate-social-runtime-v2.db", tmp_path / "avatars"
    )
    for actor_id, name in (("u1", "阿甲"), ("u2", "阿乙")):
        directory.remember_actor(
            persona_id="persona", group_id="group-1", actor_id=actor_id,
            display_name=name, updated_at=100,
        )
    return ImitationCommandInterpreter(
        admin_ids=("admin",), participants=directory, timezone=TZ
    )


def test_textual_bot_name_cannot_change_imitation_state(tmp_path):
    interpreter = _interpreter(tmp_path)
    event = _event(
        "爱弥斯开始模仿阿甲到明晚八点", mentions=(), mentions_bot=False
    )

    assert interpreter.interpret(event, now=_timestamp("2026-08-27 14:00")) is None


def test_real_target_mention_is_preferred_and_tomorrow_time_is_absolute(tmp_path):
    request = _interpreter(tmp_path).interpret(
        _event("开始模仿到明晚八点", mentions=("bot", "u1")),
        now=_timestamp("2026-08-27 14:00"),
    )

    assert request.kind == "START"
    assert request.target_member_id == "u1"
    assert request.target_display_name == "阿甲"
    assert request.expires_at == _timestamp("2026-08-28 20:00")


def test_unique_exact_name_fallback_and_duration(tmp_path):
    request = _interpreter(tmp_path).interpret(
        _event("开始模仿阿乙，持续两小时", mentions=("bot",)),
        now=_timestamp("2026-08-27 14:00"),
    )

    assert request.target_member_id == "u2"
    assert request.expires_at == _timestamp("2026-08-27 16:00")


def test_non_admin_cannot_start_but_target_can_request_self_stop(tmp_path):
    interpreter = _interpreter(tmp_path)
    with pytest.raises(ImitationCommandError) as denied:
        interpreter.interpret(
            _event("开始模仿阿乙，持续两小时", actor="u1", mentions=("bot",)),
            now=_timestamp("2026-08-27 14:00"),
        )
    stop = interpreter.interpret(
        _event("别学我了", actor="u1", mentions=("bot",)),
        now=_timestamp("2026-08-27 14:00"),
    )

    assert denied.value.code == "imitation_admin_required"
    assert stop.kind == "STOP_SELF"
    assert stop.target_member_id == "u1"


@pytest.mark.parametrize(
    "text,code",
    (
        ("开始模仿阿甲", "imitation_expiry_required"),
        ("开始模仿阿甲，持续四天", "imitation_duration_too_long"),
        ("开始模仿到明晚八点", "imitation_target_required"),
    ),
)
def test_invalid_start_request_does_not_guess(tmp_path, text, code):
    with pytest.raises(ImitationCommandError) as captured:
        _interpreter(tmp_path).interpret(
            _event(text, mentions=("bot",)), now=_timestamp("2026-08-27 14:00")
        )

    assert captured.value.code == code


def _ready_style(repository):
    repository.set_enabled(
        "group-1", "u1", enabled=True, updated_by="admin", now=100
    )
    repository.publish(MemberSpeechStyle(
        group_id="group-1", member_id="u1", version=1, status="READY",
        opening_patterns=("先表态",), progression_patterns=("再补理由",),
        closing_patterns=("自然收口",), length_rhythm="短句", directness="直接",
        disagreement_style="指出问题", play_style="现场调侃", care_style="给具体动作",
        addressing_style="需要时称呼", particles_punctuation="少量语气词",
        stable_traits=("结论在前",), occasional_traits=("偶尔省略主语",),
        evidence_event_ids=("e1", "e2"), eligible_message_count=40,
        active_day_count=5, scene_types=("answer", "banter", "care"), generated_at=100,
    ))


def test_controller_commits_ready_session_and_self_stop(tmp_path):
    interpreter = _interpreter(tmp_path)
    repository = MemberStyleRepository(tmp_path / "groupmate-social-runtime-v2.db")
    _ready_style(repository)
    controller = ImitationSessionController(interpreter, repository)
    now = _timestamp("2026-08-27 14:00")

    started = controller.handle(
        _event("开始模仿到明晚八点", mentions=("bot", "u1")), now=now
    )
    stopped = controller.handle(
        _event("别学我了", actor="u1", mentions=("bot",)), now=now + 1
    )

    assert started.transition.operation == "STARTED"
    assert started.transition.session.target_member_id == "u1"
    assert stopped.transition.operation == "STOPPED_BY_TARGET"
    assert repository.active_session("group-1", now=now + 2) is None


def test_controller_returns_aemeath_error_when_style_is_not_ready(tmp_path):
    controller = ImitationSessionController(
        _interpreter(tmp_path),
        MemberStyleRepository(tmp_path / "groupmate-social-runtime-v2.db"),
    )

    result = controller.handle(
        _event("开始模仿到明晚八点", mentions=("bot", "u1")),
        now=_timestamp("2026-08-27 14:00"),
    )

    assert result.handled is True
    assert result.transition is None
    assert result.diagnostic_code == "imitation_style_not_ready"
    assert "还学不像阿甲" in result.error_text
