from pathlib import Path


ROOT = Path(__file__).parents[2]
PAGE = ROOT / "pages" / "settings"


def test_runtime_does_not_construct_plugin_asset_urls_in_javascript():
    javascript = "\n".join(
        path.read_text(encoding="utf-8") for path in PAGE.rglob("*.js")
    )

    assert "./assets/icons/" not in javascript
    assert "document.createElement(\"img\")" in javascript
    assert 'source.startsWith("data:image/")' in javascript


def test_sidebar_does_not_collapse_at_normal_desktop_width():
    layout = (PAGE / "styles" / "layout.css").read_text(encoding="utf-8")
    components = (PAGE / "styles" / "components.css").read_text(encoding="utf-8")

    assert "@media (max-width: 80rem)" not in layout + components
    assert "@media (max-width: 60rem)" in layout
    assert "grid-template-columns: 4.5rem" not in layout


def test_trace_table_becomes_readable_cards_on_narrow_screens():
    css = (PAGE / "styles" / "components.css").read_text(encoding="utf-8")
    runtime = (PAGE / "workspaces" / "runtime.js").read_text(encoding="utf-8")

    assert ".trace-table td::before" in css
    assert "content: attr(data-label)" in css
    assert 'attrs: { "data-label": "收到的消息" }' in runtime
    assert ".two-line" in css
    assert "-webkit-line-clamp: 2" in css


def test_trace_message_flex_layout_does_not_replace_the_table_cell_layout():
    runtime = (PAGE / "workspaces" / "runtime.js").read_text(encoding="utf-8")

    assert 'element("td", { className: "trace-message"' not in runtime
    assert 'element("div", { className: "trace-message"' in runtime


def test_light_and_dark_tokens_keep_status_surfaces_theme_safe():
    tokens = (PAGE / "styles" / "tokens.css").read_text(encoding="utf-8")

    assert '[data-theme="dark"]' in tokens
    for token in (
        "--color-primary-soft",
        "--color-info-soft",
        "--color-warning-soft",
        "--color-danger-soft",
    ):
        assert tokens.count(token) >= 2
