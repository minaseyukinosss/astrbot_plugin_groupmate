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


def test_layout_reflows_before_astrbot_iframe_reaches_broken_toolbar_width():
    layout = (PAGE / "styles" / "layout.css").read_text(encoding="utf-8")

    assert "max-width: 80rem" in layout
    assert "grid-template-columns: 4.5rem" in layout
    assert ".nav-label" in layout


def test_runtime_and_activity_have_distinct_product_views():
    runtime = (PAGE / "workspaces" / "runtime.js").read_text(encoding="utf-8")
    activity = (PAGE / "workspaces" / "activity.js").read_text(encoding="utf-8")
    projection = (PAGE / "components" / "projection.js").read_text(encoding="utf-8")

    for label in ("运行概览", "近期活动", "健康状态"):
        assert label in runtime
    for label in ("筛选事件", "事件", "参与者", "结果"):
        assert label in activity + projection
    assert "projectionList(runtime)" not in runtime
    assert "projectionList(activity" not in activity
