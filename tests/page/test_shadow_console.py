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
    for label in ("全部消息", "已回复", "未参与", "外部能力", "异常"):
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


def test_inspector_explains_shadow_pre_gate_result_and_cognition_diagnostics():
    inspector = (PAGE / "components" / "inspector.js").read_text(encoding="utf-8")

    for label in ("SHADOW 前判断", "候选回复", "认知模块", "诊断码", "耗时"):
        assert label in inspector
