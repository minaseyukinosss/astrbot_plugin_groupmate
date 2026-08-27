from __future__ import annotations

from groupmate.adapters.profile_query import parse_profile_command


def test_profile_query_accepts_only_the_exact_view_command():
    command = parse_profile_command("  查看我的画像\n")

    assert command is not None
    assert command.kind == "show_self"


def test_all_other_former_member_profile_commands_are_unclaimed():
    for text in (
        "我的画像",
        "我觉得我的画像应该更具体",
        "帮我查看我的画像",
        "查看他的画像",
        "我的画像呀",
        "纠正画像",
        "纠正画像 2 现在不喝冷饮",
        "删除画像 3",
        "删除画像 abc",
        "停止画像个性化",
        "恢复画像个性化",
    ):
        assert parse_profile_command(text) is None
