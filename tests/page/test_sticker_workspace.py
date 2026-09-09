from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[2]
PAGE = ROOT / "pages" / "settings"


def _run_script(body: str):
    result = subprocess.run(
        ["node", "--input-type=module", "--eval", body],
        check=True,
        capture_output=True,
        text=True,
        cwd=PAGE,
    )
    return json.loads(result.stdout)


def _run_module(path: Path, body: str):
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    script = f"const module=await import('data:text/javascript;base64,{encoded}');\n{body}"
    return _run_script(script)


def test_sticker_workspace_is_reachable():
    router = _run_module(
        PAGE / "router.js",
        "console.log(JSON.stringify({routes:module.ROUTES.map(x=>x.path),"
        "known:module.normalizeHash('#/stickers?status=candidate')}));",
    )
    store = (PAGE / "store.js").read_text(encoding="utf-8")
    app = (PAGE / "app.js").read_text(encoding="utf-8")
    html = (PAGE / "index.html").read_text(encoding="utf-8")
    source = (PAGE / "workspaces" / "stickers.js").read_text(encoding="utf-8")

    assert router == {
        "routes": ["/runtime", "/profiles", "/knowledge", "/stickers"],
        "known": "/stickers",
    }
    assert '"/stickers": ["stickers"]' in store
    assert 'import { renderStickers } from "./workspaces/stickers.js"' in app
    assert 'data-route="/stickers"' in html
    for label in (
        "待认知",
        "图鉴",
        "含义",
        "确认为可用",
        "GIF 和静图都须点",
        "上传表情",
        "认知尚未形成",
        "从群友消息里收表情",
        "重新认知",
        "口气",
        "好感门槛",
        "至少认识",
        "仅默契",
        "委屈",
        "附和",
    ):
        assert label in source
    for action in (
        "sticker_upload",
        "sticker_caption",
        "sticker_confirm",
        "sticker_disable",
        "sticker_reject",
        "sticker_delete",
        "sticker_recaption",
    ):
        assert action in source
    assert "admin_import: \"管理员导入\"" in source
    assert "group_captured: \"群友消息\"" in source


def test_sticker_workspace_renders_captured_cards():
    stickers = (PAGE / "workspaces" / "stickers.js").resolve().as_uri()
    result = _run_script(
        """
function makeNode(tag) {
  return {
    tagName: String(tag).toUpperCase(),
    className: "",
    hidden: false,
    textContent: "",
    dataset: {},
    children: [],
    attributes: {},
    setAttribute(name, value) { this.attributes[name] = String(value); },
    append(...nodes) {
      for (const child of nodes) {
        if (child != null) this.children.push(child);
      }
    },
    addEventListener() {},
    replaceChildren(...nodes) { this.children = nodes.filter((child) => child != null); },
  };
}
function texts(node, bag = []) {
  if (node?.textContent) bag.push(String(node.textContent));
  for (const child of node?.children || []) texts(child, bag);
  return bag;
}
globalThis.document = { createElement: makeNode };
const { renderStickers } = await import(""" + json.dumps(stickers) + """);
const root = renderStickers(() => ({
  inbox: [{
    asset_id: "sticker:abc",
    origin_kind: "group_captured",
    status: "candidate",
    meaning: "摊手无奈",
  }],
  library: [{
    asset_id: "sticker:def",
    origin_kind: "admin_import",
    status: "ready",
    meaning: "得意一笑",
  }],
  capacity: { candidate_count: 1, library_count: 1 },
}), async () => {}, async () => {});
const labels = texts(root);
console.log(JSON.stringify({
  captured: labels.some((text) => text.includes("群友消息")),
  imported: labels.some((text) => text.includes("管理员导入")),
  candidate: labels.some((text) => text.includes("摊手无奈")),
  ready: labels.some((text) => text.includes("得意一笑")),
}));
"""
    )
    assert result == {
        "captured": True,
        "imported": True,
        "candidate": True,
        "ready": True,
    }
