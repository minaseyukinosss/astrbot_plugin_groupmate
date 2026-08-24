from __future__ import annotations

import json
import base64
import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[2]
PAGE = ROOT / "pages" / "settings"


def _run_presenter(body: str) -> object:
    source = (PAGE / "components" / "presenters.js").read_bytes()
    presenter = f"data:text/javascript;base64,{base64.b64encode(source).decode('ascii')}"
    script = f"import * as presenter from {json.dumps(presenter)};\n{body}"
    result = subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_projection_presenter_translates_runtime_facts_for_people():
    result = _run_presenter(
        "console.log(JSON.stringify({"
        "label: presenter.kindLabel('group_world.projected'),"
        "mode: presenter.valueLabel('runtime_mode', 'SHADOW'),"
        "facts: presenter.visibleFacts({kind:'group_world.projected',scene_version:4,"
        "runtime_mode:'SHADOW',paused:false,disposition:'SILENCE'})"
        "}));"
    )

    assert result == {
        "label": "群聊现场已更新",
        "mode": "观察模式（不发送）",
        "facts": [
            {"key": "runtime_mode", "label": "运行模式", "value": "观察模式（不发送）"},
            {"key": "paused", "label": "运行状态", "value": "运行中"},
            {"key": "disposition", "label": "处理结果", "value": "保持沉默"},
        ],
    }


def test_shell_uses_approved_product_hierarchy_instead_of_projection_console():
    html = (PAGE / "index.html").read_text(encoding="utf-8")

    assert "群聊伙伴" in html
    assert "此刻" in html
    assert 'class="topbar-context"' in html
    assert 'id="pause-runtime"' not in html
    assert "所有状态来自版本化 Projection" not in html


def test_layout_preserves_product_navigation_at_normal_iframe_width():
    layout = (PAGE / "styles" / "layout.css").read_text(encoding="utf-8")

    assert "max-width: 60rem" in layout
    assert ".nav-label" in layout


def test_product_palette_is_neutral_with_green_reserved_for_status():
    tokens = (PAGE / "styles" / "tokens.css").read_text(encoding="utf-8")
    components = (PAGE / "styles" / "components.css").read_text(encoding="utf-8")

    assert "--color-canvas: oklch(0.965 0.002 260)" in tokens
    assert "--color-surface: oklch(0.965 0.002 260)" in tokens
    assert ".sidebar nav a[aria-current]" in components
    assert "background: var(--color-surface-active)" in components


def test_runtime_is_the_single_message_trace_product_view():
    runtime = (PAGE / "workspaces" / "runtime.js").read_text(encoding="utf-8")

    for label in ("运行概览", "消息链路", "最终结果"):
        assert label in runtime
    assert not (PAGE / "workspaces" / "activity.js").exists()
    assert "projectionList(runtime)" not in runtime


def test_message_presenter_uses_non_text_parts_instead_of_generic_placeholder():
    result = _run_presenter(
        "console.log(JSON.stringify({"
        "onlyMedia: presenter.messageSummary({summary:'',parts:["
        "{kind:'image',label:'图片'},{kind:'record',label:'语音'}]}),"
        "mixed: presenter.messageSummary({summary:'看看 · 图片',parts:["
        "{kind:'text',text:'看看'},{kind:'image',label:'图片'}]})"
        "}));"
    )

    assert result == {
        "onlyMedia": "图片 · 语音",
        "mixed": "看看 · 图片",
    }


def test_full_message_renderer_keeps_segment_order():
    source = (PAGE / "components" / "message.js").read_text(encoding="utf-8")

    assert "for (const part of parts)" in source
    assert 'parts.filter((part) => part?.kind !== "text")' not in source


def test_message_presenter_formats_media_sizes_for_people():
    result = _run_presenter(
        "console.log(JSON.stringify(["
        "presenter.formatBytes(800),"
        "presenter.formatBytes(2048),"
        "presenter.formatBytes(1572864),"
        "presenter.formatBytes(null)"
        "]));"
    )

    assert result == ["800 B", "2 KB", "1.5 MB", ""]
