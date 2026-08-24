import re
from pathlib import Path


ROOT = Path(__file__).parents[2]
PAGE = ROOT / "pages" / "settings"


def test_runtime_console_is_message_centric_and_plain_language():
    app = (PAGE / "app.js").read_text(encoding="utf-8")
    runtime = (PAGE / "workspaces" / "runtime.js").read_text(encoding="utf-8")
    inspector = (PAGE / "components" / "inspector.js").read_text(encoding="utf-8")

    runtime_projection_block = re.search(
        r'WORKSPACE_PROJECTIONS.*?"/runtime":\s*\[(.*?)\]', app, re.DOTALL
    ).group(1)
    assert '"traces"' in runtime_projection_block
    assert '"activity"' not in runtime_projection_block
    assert '"evaluation"' not in runtime_projection_block

    for label in ("收到的消息", "处理路径", "Groupmate 的理解", "决定", "最终结果"):
        assert label in runtime + inspector

    for field in ("actor", "message", "route", "understanding", "decision", "delivery", "stages"):
        assert field in inspector

    for internal in ("forced_observe", "AMBIENT", "projection_version"):
        assert internal not in runtime


def test_runtime_console_covers_real_delivery_and_handoff_states():
    runtime = (PAGE / "workspaces" / "runtime.js").read_text(encoding="utf-8")

    for state in ("SENT", "SILENT", "OBSERVED", "HANDED_OFF", "FAILED", "UNKNOWN"):
        assert state in runtime
    for label in ("全部消息", "会回复", "继续观察", "外部能力", "异常"):
        assert label in runtime


def test_runtime_console_supports_immediate_refresh_without_losing_filters():
    app = (PAGE / "app.js").read_text(encoding="utf-8")
    runtime = (PAGE / "workspaces" / "runtime.js").read_text(encoding="utf-8")

    assert "refreshWorkspaceData" in app
    assert "立即刷新" in runtime
    assert "runtimeViewState" in runtime
    assert "renderRuntime(select, command, refreshData)" in runtime


def test_manual_refresh_keeps_existing_data_visible_and_reports_partial_failure():
    app = (PAGE / "app.js").read_text(encoding="utf-8")

    assert 'elements.workspace.setAttribute("aria-busy", "true")' not in app
    assert "刷新完成，但" in app
    assert "failedProjections" in app


def test_manual_refresh_updates_an_open_inspector_with_the_same_timeout():
    app = (PAGE / "app.js").read_text(encoding="utf-8")

    assert "activeInspectorQuery" in app
    assert "refreshOpenInspector" in app
    assert "timeoutMs: 4_000" in app
    assert "activeInspectorQuery !== query" in app


def test_trace_duration_is_explicitly_labeled_in_list_and_detail():
    runtime = (PAGE / "workspaces" / "runtime.js").read_text(encoding="utf-8")
    inspector = (PAGE / "components" / "inspector.js").read_text(encoding="utf-8")

    assert "链路历时" in runtime
    assert '["链路历时", formatTraceDuration' in inspector
    assert '["总耗时"' not in inspector


def test_inspector_leads_with_result_and_moves_diagnostics_to_technical_details():
    inspector = (PAGE / "components" / "inspector.js").read_text(encoding="utf-8")
    presenters = (PAGE / "components" / "presenters.js").read_text(encoding="utf-8")
    default_content, technical_content = inspector.split(
        'element("details", { className: "technical-details" }', 1
    )

    for label in (
        "处理结果",
        "判断原因",
        "关键证据",
        "result-summary",
        "result-evidence",
    ):
        assert label in default_content
    for removed_default in (
        "参与方案",
        "SHADOW 前判断",
        "生成说明",
    ):
        assert removed_default not in default_content
    for technical_label in (
        "处理阶段",
        "认知模块",
        "策略通道",
        "原始诊断码",
    ):
        assert technical_label in technical_content
    assert "cognitionDiagnostics(understanding.diagnostics)" in technical_content
    for diagnostic_label in (
        "认知后端",
        "耗时",
        "Provider 等待",
        "输入大小",
        "本次截止",
    ):
        assert diagnostic_label in inspector + presenters


def test_runtime_list_distinguishes_reply_observe_silence_and_defer_results():
    runtime = (PAGE / "workspaces" / "runtime.js").read_text(encoding="utf-8")
    presenters = (PAGE / "components" / "presenters.js").read_text(encoding="utf-8")

    for helper in (
        "traceResultHeadline",
        "traceResultState",
        "traceResultReason",
    ):
        assert helper in runtime
    assert "正式运行会回复" in runtime + presenters
    for label in ("本轮暂不参与", "本轮不回复", "稍后重新判断", "判断未完成"):
        assert label in runtime + presenters


def test_result_first_styles_preserve_readable_single_column_evidence():
    styles = (PAGE / "styles" / "components.css").read_text(encoding="utf-8")

    for selector in (
        ".result-summary",
        ".result-summary-heading",
        ".result-state",
        ".result-reason",
        ".result-evidence",
    ):
        assert selector in styles
    assert ".result-evidence" in styles and "minmax(0, 1fr)" in styles


def test_runtime_console_surfaces_strategy_and_cognition_failures():
    runtime = (PAGE / "workspaces" / "runtime.js").read_text(encoding="utf-8")
    inspector = (PAGE / "components" / "inspector.js").read_text(encoding="utf-8")
    presenters = (PAGE / "components" / "presenters.js").read_text(encoding="utf-8")

    for field in (
        "participation_lane",
        "would_reply",
        "candidate_count",
        "candidate_source",
        "participation_diagnostics",
    ):
        assert field in runtime + inspector + presenters

    for label in ("会回复", "继续观察", "认知异常"):
        assert label in runtime
    assert "traceHasCognitionFailure" in runtime


def test_preview_fixture_exercises_strategy_and_timeout_states():
    fixture = (PAGE.parents[1] / "tests" / "page" / "fixtures" / "fake_bridge.js")
    source = fixture.read_text(encoding="utf-8")

    for value in (
        "DIRECT_FAST",
        "CONTINUATION",
        "AMBIENT",
        "candidate_count",
        "candidate_source",
        "participation_diagnostics",
        "direct_deepseek",
        "deepseek-v4-flash",
        "direct_timeout",
        "judgement:",
        'status: "accepted"',
        'status: "unavailable"',
        "关键证据来自当前公开群聊消息",
    ):
        assert value in source
