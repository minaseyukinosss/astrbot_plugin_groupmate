from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).parents[2]
PAGE = ROOT / "pages" / "settings"
WORKSPACES = PAGE / "workspaces"
COMPONENTS = PAGE / "components"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_five_workspace_modules_are_wired_to_the_shell():
    app = _source(PAGE / "app.js")
    expected = {
        "runtime": "renderRuntime",
        "persona": "renderPersona",
        "people": "renderPeople",
        "activity": "renderActivity",
        "governance": "renderGovernance",
    }

    for name, exported in expected.items():
        source = _source(WORKSPACES / f"{name}.js")
        assert re.search(rf"export function {exported}\(select, command\)", source)
        assert f'./workspaces/{name}.js' in app
        assert exported in app

    assert "WORKSPACE_RENDERERS" in app
    assert "renderWorkspace(activeRoute" in app


def test_runtime_workspace_uses_real_runtime_task_activity_and_health_projections():
    source = _source(WORKSPACES / "runtime.js")

    for projection in ("runtime", "activity", "tasks", "health"):
        assert f'select("{projection}")' in source
    for label in ("运行概览", "近期活动", "任务义务", "健康状态", "暂停", "恢复"):
        assert label in source
    assert 'type: "pause"' in source
    assert "controlVersion" in source
    assert "degraded_reasons" in source
    assert "fallback_poll_seconds" in source
    assert "setTimeout" not in source
    assert "处理中" not in source


def test_persona_workspace_covers_versioned_behavior_and_draft_flow():
    source = _source(WORKSPACES / "persona.js")
    dialog = _source(COMPONENTS / "command-dialog.js")

    for label in (
        "身份",
        "在场状态",
        "参与方式",
        "表达",
        "社交印象",
        "媒体",
        "工具",
        "角色名称",
        "角色定位",
        "参与主动性",
        "回复长度",
        "保存草稿",
        "预览效果",
        "发布到群",
    ):
        assert label in source
    for command_type in (
        "config_draft",
        "config_validate",
        "config_dry_run",
        "config_publish",
        "config_restore",
    ):
        assert f'"{command_type}"' in source
    assert "collectProfile" in source
    assert "payloadFactory" in source
    assert "payloadFactory" in dialog
    assert 'format: "json"' not in source
    assert "publishedConfigVersion" in source
    assert "persona-profile:" in source


def test_people_workspace_exposes_social_evidence_without_sensitive_payloads():
    source = _source(WORKSPACES / "people.js")

    for projection in ("people", "culture", "governance"):
        assert f'select("{projection}")' in source
    for label in (
        "身份",
        "关系维度",
        "印象",
        "经历",
        "事实",
        "未完事项",
        "承诺",
        "群文化",
        "治理历史",
    ):
        assert label in source
    for command_type in ("review", "forget", "correct", "link"):
        assert f'type: "{command_type}"' in source
    for field in ("source_ref", "target_ref", "allowed_data_types", "correction"):
        assert f'name: "{field}"' in source
    for forbidden in ("chain_of_thought", "raw_payload", "prompt", "secret"):
        assert forbidden not in source.casefold()
    assert "controlVersion" in source


def test_activity_workspace_is_a_causal_projection_timeline_not_chat_text_parsing():
    source = _source(WORKSPACES / "activity.js")

    for projection in ("activity", "scenes", "tasks"):
        assert f'select("{projection}")' in source
    for label in ("事件流", "筛选事件", "群聊活动", "决策判断", "任务交付"):
        assert label in source
    assert "message_text" not in source


def test_governance_workspace_has_audited_high_impact_actions():
    source = _source(WORKSPACES / "governance.js")

    for projection in ("governance", "evaluation"):
        assert f'select("{projection}")' in source
    for label in (
        "待复核",
        "纠正",
        "遗忘",
        "身份关联",
        "配置版本",
        "校准",
        "导出",
        "保留策略",
        "目标效果评估",
    ):
        assert label in source
    assert 'type: "approve_calibration"' in source
    assert 'type: "reset"' in source
    assert "controlVersion" in source
    for label in (
        "历史摘要",
        "唯一 Focus",
        "Attention",
        "对象",
        "候选响应与动作",
        "Governor outcome",
        "结构化理由",
        "有效期",
        "合理",
        "不合理",
        "证据不足",
    ):
        assert label in source
    assert 'type: "shadow_review"' in source
    assert "suggested_categories" in source
    assert "raw_ids" not in source
    assert "message_text" not in source

    dialog = _source(COMPONENTS / "command-dialog.js")
    assert "expected_version" in dialog
    assert "reason" in dialog
    assert "confirmed" in dialog
    assert "requiresConfirmation" in dialog
    assert '"shadow_review"' in dialog
    assert "reason.value.trim()" in dialog
    assert "options.fields" in dialog
    assert "JSON.parse" in dialog
    assert '.split(",")' in dialog


def test_inspector_is_allowlisted_and_renders_only_text_nodes():
    inspector = _source(COMPONENTS / "inspector.js")
    dom = _source(COMPONENTS / "dom.js")
    all_page_js = "\n".join(_source(path) for path in PAGE.rglob("*.js"))

    for label in (
        "证据",
        "结构化 Observation",
        "候选意图",
        "效用贡献",
        "硬约束",
        "Plan",
        "版本",
        "结果",
    ):
        assert label in inspector
    assert "INSPECTOR_FIELDS" in inspector
    assert "Entity ref:" not in inspector
    assert ".textContent" in dom
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "eval(", "new Function"):
        assert sink not in all_page_js


def test_workspace_styles_remain_text_first_and_responsive():
    styles = "\n".join(
        _source(path) for path in (PAGE / "styles").glob("*.css")
    )

    for selector in (
        ".workspace-section",
        ".event-feed",
        ".activity-table",
        ".command-dialog",
        ".empty-state",
    ):
        assert selector in styles
    assert "@media (max-width:" in styles
    assert ".summary-grid" in styles
    assert "grid-template-columns: repeat(2" in styles
    assert "backdrop-filter" not in styles
