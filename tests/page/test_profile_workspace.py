from pathlib import Path
import re


ROOT = Path(__file__).parents[2]
PAGE = ROOT / "pages" / "settings"


def test_profile_workspace_leads_with_member_identity_not_model_diagnostics():
    source = (PAGE / "workspaces" / "profiles.js").read_text(encoding="utf-8")

    for label in ("一句话画像", "个体特征", "偏好与边界", "代表经历", "群友关系", "证据与审计"):
        assert label in source
    assert source.index("一句话画像") < source.index("代表经历")
    assert source.index("代表经历") < source.index("证据与审计")
    assert "模型诊断" not in source


def test_profile_workspace_has_search_member_list_detail_and_teaching_empty_state():
    source = (PAGE / "workspaces" / "profiles.js").read_text(encoding="utf-8")

    assert "搜索成员或画像关键词" in source
    assert "选择左侧成员" in source
    assert "画像会在群聊中逐步形成" in source
    assert "profile-member-list" in source
    assert "profile-detail" in source


def test_profile_workspace_scrolls_directory_and_detail_independently():
    source = (PAGE / "workspaces" / "profiles.js").read_text(encoding="utf-8")
    styles = (PAGE / "styles" / "components.css").read_text(encoding="utf-8")

    assert "height: max(35rem, calc(100dvh - 15.875rem));" in styles
    assert ".profile-directory,\n.profile-detail-host { min-height: 0;" in styles
    detail_rules = re.findall(r"\.profile-detail-host\s*\{([^}]*)\}", styles)
    assert any("overflow-y: auto;" in rule for rule in detail_rules)
    assert "overscroll-behavior: contain;" in styles
    assert "detailHost.scrollTop = 0" in source

    mobile = styles.split("@media (max-width: 44rem)", 1)[1]
    assert ".profile-browser { display: block; height: auto;" in mobile
    mobile_detail_rules = re.findall(
        r"\.profile-detail-host\s*\{([^}]*)\}", mobile
    )
    assert any("overflow: visible;" in rule for rule in mobile_detail_rules)
