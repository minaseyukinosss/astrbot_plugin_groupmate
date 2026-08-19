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
    for label in ("当前状态", "实时活动", "任务义务", "运行健康", "暂停", "恢复"):
        assert label in source
    assert 'type: "pause"' in source
    assert "controlVersion" in source
    assert "degraded_reasons" in source
    assert "fallback_poll_seconds" in source
    assert "setTimeout" not in source
    assert "处理中" not in source


def test_persona_workspace_covers_versioned_behavior_and_draft_flow():
    source = _source(WORKSPACES / "persona.js")

    for label in (
        "Constitution",
        "状态与模式",
        "注意力",
        "自主性",
        "Governor",
        "风格",
        "媒体",
        "工具",
        "草稿",
        "语义差异",
        "Dry-run",
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
    assert 'name: "config_id"' in source
    assert 'name: "config"' in source
    assert 'format: "json"' in source
    assert "publishedConfigVersion" in source
    assert '{ name: "config_id", label: "已发布配置 ID", defaultValue: "" }' in source


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
    for label in (
        "因果时间线",
        "结构化 Observation",
        "候选意图",
        "Governor",
        "ActionPlan",
        "Task",
        "Delivery Part",
        "故障",
    ):
        assert label in source
    assert 'type: "cancel"' in source
    assert "controlVersion" in source
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

    dialog = _source(COMPONENTS / "command-dialog.js")
    assert "expected_version" in dialog
    assert "reason" in dialog
    assert "confirmed" in dialog
    assert "requiresConfirmation" in dialog
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
    assert "entity_ref" in inspector
    assert ".textContent" in dom
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "eval(", "new Function"):
        assert sink not in all_page_js


def test_workspace_styles_remain_text_first_and_responsive():
    styles = "\n".join(
        _source(path) for path in (PAGE / "styles").glob("*.css")
    )

    for selector in (
        ".workspace-section",
        ".projection-list",
        ".timeline",
        ".command-dialog",
        ".empty-state",
    ):
        assert selector in styles
    assert "@media (max-width:" in styles
    assert "grid-template-columns: repeat(4" not in styles
    assert "backdrop-filter" not in styles
