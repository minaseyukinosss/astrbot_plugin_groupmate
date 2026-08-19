from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).parents[2]
PAGE = ROOT / "pages" / "settings"


class _Parser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = []

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


def test_landmarks_live_regions_and_keyboard_targets_are_explicit():
    parser = _Parser()
    parser.feed((PAGE / "index.html").read_text(encoding="utf-8"))

    assert any(tag == "nav" and attrs.get("aria-label") for tag, attrs in parser.elements)
    assert any(tag == "main" and attrs.get("tabindex") == "-1" for tag, attrs in parser.elements)
    assert any(attrs.get("aria-live") == "polite" for _, attrs in parser.elements)
    assert any(attrs.get("role") == "alert" for _, attrs in parser.elements)
    assert all(
        attrs.get("aria-label") or attrs.get("id")
        for tag, attrs in parser.elements
        if tag == "button"
    )


def test_theme_zoom_narrow_iframe_and_reduced_motion_contracts_are_present():
    css = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (PAGE / "styles").glob("*.css")
    )
    html = (PAGE / "index.html").read_text(encoding="utf-8")

    assert 'name="viewport"' in html
    assert 'name="color-scheme"' in html
    assert '[data-theme="dark"]' in css
    assert "prefers-color-scheme: dark" in css
    assert "prefers-reduced-motion: reduce" in css
    assert "max-width: 44rem" in css
    assert "overflow-x: auto" in css
    assert "font-size: 0.9375rem" in css
    assert "outline: 0" in css and "--focus-ring" in css


def test_command_dialog_keeps_focusable_labels_and_server_side_authority_copy():
    source = (PAGE / "components" / "command-dialog.js").read_text(encoding="utf-8")

    assert 'element("label"' in source
    assert 'attrs: { for:' in source
    assert "reason.focus()" in source
    assert "confirmation.focus()" in source
    assert "服务端会再次验证管理员、作用域、版本、原因与确认" in source
    assert 'attrs: { role: "alert" }' in source
