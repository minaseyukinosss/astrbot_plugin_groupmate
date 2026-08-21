import re
from pathlib import Path


ROOT = Path(__file__).parents[2]
PAGE = ROOT / "pages" / "settings"


def test_runtime_console_reads_and_surfaces_shadow_projection_details():
    app = (PAGE / "app.js").read_text(encoding="utf-8")
    runtime = (PAGE / "workspaces" / "runtime.js").read_text(encoding="utf-8")
    inspector = (PAGE / "components" / "inspector.js").read_text(encoding="utf-8")

    runtime_projection_block = re.search(
        r'WORKSPACE_PROJECTIONS.*?"/runtime":\s*\[(.*?)\]', app, re.DOTALL
    ).group(1)
    assert '"scenes"' in runtime_projection_block
    assert '"evaluation"' in runtime_projection_block

    for label in ("SHADOW 观察", "事件流", "参与判断", "决策 / 行动", "证据"):
        assert label in runtime + inspector

    for field in ("focus", "attention", "candidate_response", "candidate_actions"):
        assert field in inspector
