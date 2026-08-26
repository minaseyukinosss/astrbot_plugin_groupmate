from __future__ import annotations

import pytest

from groupmate.adapters.profile_query import parse_profile_command


@pytest.mark.parametrize("text", ("查看我的画像", "我的画像", "  我的画像\n"))
def test_profile_query_is_exact_and_local(text):
    command = parse_profile_command(text)
    assert command is not None
    assert command.kind == "show_self"


@pytest.mark.parametrize(
    "text",
    (
        "我觉得我的画像应该更具体",
        "帮我查看我的画像",
        "查看他的画像",
        "我的画像呀",
        "纠正画像",
        "删除画像 abc",
    ),
)
def test_natural_chat_does_not_claim_profile_command(text):
    assert parse_profile_command(text) is None


def test_profile_correction_and_personalization_commands_are_structured():
    correction = parse_profile_command("纠正画像 2 现在不喝冷饮")
    deletion = parse_profile_command("删除画像 3")

    assert correction is not None
    assert (correction.kind, correction.fact_number, correction.content) == (
        "correct_fact",
        2,
        "现在不喝冷饮",
    )
    assert deletion is not None
    assert (deletion.kind, deletion.fact_number) == ("delete_fact", 3)
    assert parse_profile_command("停止画像个性化").kind == "disable_personalization"
    assert parse_profile_command("恢复画像个性化").kind == "enable_personalization"
