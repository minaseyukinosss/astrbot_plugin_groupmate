from pathlib import Path


PAGE = Path(__file__).parents[2] / "pages" / "settings"


def test_profile_workspace_manages_distilled_member_style_without_editing_it():
    source = (PAGE / "workspaces" / "profiles.js").read_text(encoding="utf-8")
    styles = (PAGE / "styles" / "components.css").read_text(encoding="utf-8")

    assert "说话风格蒸馏" in source
    for label in ("关闭", "积累中", "可用", "分析失败"):
        assert label in source
    for field in (
        "eligible_message_count",
        "active_day_count",
        "scene_types",
        "style_version",
        "stable_traits",
    ):
        assert field in source
    assert 'type: "member_style_distillation_set"' in source
    assert "expected_version: style.setting_version" in source
    assert "member_ref: member.member_ref" in source
    assert "enabled: !style.enabled" in source
    assert "编辑风格" not in source
    assert "profile-style-distillation" in styles
