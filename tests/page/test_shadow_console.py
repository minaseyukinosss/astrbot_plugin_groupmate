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


def test_inspector_explains_shadow_pre_gate_result_and_cognition_diagnostics():
    inspector = (PAGE / "components" / "inspector.js").read_text(encoding="utf-8")
    presenters = (PAGE / "components" / "presenters.js").read_text(encoding="utf-8")

    for label in (
        "策略通道",
        "正式运行",
        "参与方案",
        "SHADOW 前判断",
        "候选回复",
        "认知模块",
        "耗时",
        "Provider 等待",
        "输入大小",
        "本次截止",
    ):
        assert label in inspector + presenters


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
        "worker_timeout",
    ):
        assert value in source
