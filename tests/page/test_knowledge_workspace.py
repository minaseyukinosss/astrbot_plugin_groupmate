from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[2]
PAGE = ROOT / "pages" / "settings"


def _run_module(path: Path, body: str):
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    script = f"const module=await import('data:text/javascript;base64,{encoded}');\n{body}"
    result = subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_knowledge_workspace_is_reachable_and_loads_scoped_views():
    router = _run_module(
        PAGE / "router.js",
        "console.log(JSON.stringify({routes:module.ROUTES.map(x=>x.path),"
        "known:module.normalizeHash('#/knowledge?status=retry')}));",
    )
    store = (PAGE / "store.js").read_text(encoding="utf-8")
    app = (PAGE / "app.js").read_text(encoding="utf-8")
    html = (PAGE / "index.html").read_text(encoding="utf-8")

    assert router == {
        "routes": ["/runtime", "/profiles", "/knowledge"],
        "known": "/knowledge",
    }
    assert '"/knowledge": [' in store
    for endpoint in (
        "knowledge/library/overview",
        "knowledge/library/entities",
        "knowledge/library/claims",
        "knowledge/library/jobs",
        "knowledge/group/overview",
        "knowledge/group/aliases",
        "knowledge/group/conventions",
        "knowledge/group/jobs",
    ):
        assert endpoint in store
    assert 'import { renderKnowledge } from "./workspaces/knowledge.js"' in app
    assert 'data-route="/knowledge"' in html


def test_workspace_exposes_review_states_actions_and_mobile_safe_layout():
    source = (PAGE / "workspaces" / "knowledge.js").read_text(encoding="utf-8")
    styles = (PAGE / "styles" / "components.css").read_text(encoding="utf-8")
    layout = (PAGE / "styles" / "layout.css").read_text(encoding="utf-8")

    for label in (
        "本群认知",
        "共享知识库",
        "所有群共享",
        "仅影响本群",
        "群内热门游戏",
        "版本核验",
        "群内约定",
        "冲突与过期",
        "群触发任务",
        "共享刷新任务",
        "近期使用",
        "加载中",
        "暂时无法读取",
        "暂无",
    ):
        assert label in source
    for command in (
        "knowledge_convention_confirm",
        "knowledge_convention_reject",
        "knowledge_alias_supersede",
        "knowledge_claim_dispute",
        "knowledge_job_retry",
        "knowledge_cache_invalidate",
        "knowledge_ambient_canary_set",
    ):
        assert f'type: "{command}"' in source
    assert "next_cursor" in source
    assert "statusFilter" in source
    assert "textContent" not in source
    assert "innerHTML" not in source
    assert ".knowledge-workspace" in styles
    mobile = styles.split("@media (max-width: 44rem)")[-1]
    assert ".knowledge-table" in mobile
    assert "min-width: 0" in mobile
    shell_mobile = layout.split("@media (max-width: 44rem)")[-1]
    assert "flex: 0 1 auto;" in shell_mobile
    assert ".sidebar-quick { display: none;" in shell_mobile
    assert "grid-template-columns: repeat(3, minmax(0, 1fr));" in shell_mobile


def test_workspace_exposes_accessible_entity_detail_with_safe_public_sources():
    """Catches removal of the detail affordance or unsafe source rendering."""
    source = (PAGE / "workspaces" / "knowledge.js").read_text(encoding="utf-8")
    styles = (PAGE / "styles" / "components.css").read_text(encoding="utf-8")

    assert "knowledge/library/entity-detail" in source
    assert "knowledge/group/entity-context" in source
    assert "查看详情" in source
    assert 'element("dialog"' in source
    assert 'aria-labelledby' in source
    assert 'rel: "noreferrer noopener"' in source
    assert 'target: "_blank"' in source
    assert "证据链" in source
    assert "支持这条事实" in source
    assert "反驳这条事实" in source
    assert "群内别名" in source
    assert "所属游戏" in source
    assert "同游戏相关实体" in source
    assert "原始群聊不会在这里展示" in source
    assert ".knowledge-detail" in styles
    assert "innerHTML" not in source
