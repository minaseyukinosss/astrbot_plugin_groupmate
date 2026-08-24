from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).parents[2]
PAGE = ROOT / "pages" / "settings"
WORKSPACES = PAGE / "workspaces"
COMPONENTS = PAGE / "components"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_runtime_is_the_only_product_workspace():
    app = _source(PAGE / "app.js")
    router = _source(PAGE / "router.js")
    source = _source(WORKSPACES / "runtime.js")

    assert re.search(r"export function renderRuntime\(select, command, refreshData\)", source)
    assert './workspaces/runtime.js' in app
    assert 'path: "/runtime"' in router
    for name in ("persona", "people", "activity", "governance"):
        assert not (WORKSPACES / f"{name}.js").exists()
        assert f'path: "/{name}"' not in router

    assert "WORKSPACE_RENDERERS" in app
    assert "renderWorkspace(activeRoute" in app


def test_runtime_workspace_uses_message_trace_and_readiness_projections():
    source = _source(WORKSPACES / "runtime.js")

    for projection in ("bootstrap", "runtime", "traces", "health", "governance"):
        assert f'select("{projection}")' in source
    for label in ("运行概览", "消息链路", "收到的消息", "暂停", "恢复"):
        assert label in source
    assert 'type: "pause"' in source
    assert "controlVersion" in source
    assert "runtime_blockers" in source
    assert "setTimeout" not in source


def test_inspector_is_allowlisted_and_renders_only_text_nodes():
    inspector = _source(COMPONENTS / "inspector.js")
    dom = _source(COMPONENTS / "dom.js")
    all_page_js = "\n".join(_source(path) for path in PAGE.rglob("*.js"))

    for label in (
        "参与者",
        "收到的消息",
        "处理路径",
        "Groupmate 的理解",
        "决定",
        "最终结果",
        "处理阶段",
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
        ".command-dialog",
        ".empty-state",
    ):
        assert selector in styles
    assert "@media (max-width:" in styles
    assert ".summary-grid" in styles
    assert "grid-template-columns: repeat(2" in styles
    assert "backdrop-filter" not in styles
