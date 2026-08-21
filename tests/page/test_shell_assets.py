from __future__ import annotations

import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).parents[2]
PAGE = ROOT / "pages" / "settings"


class _ShellParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []
        self.inline_script_text = []
        self.links = []
        self.elements = []
        self._inside_script = False

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        self.elements.append((tag, values))
        if tag == "script":
            self.scripts.append(values)
            self._inside_script = True
        if tag == "link":
            self.links.append(values)

    def handle_endtag(self, tag):
        if tag == "script":
            self._inside_script = False

    def handle_data(self, data):
        if self._inside_script and data.strip():
            self.inline_script_text.append(data.strip())


def _parse_shell():
    parser = _ShellParser()
    parser.feed((PAGE / "index.html").read_text(encoding="utf-8"))
    return parser


def test_shell_uses_relative_external_assets_and_no_inline_execution():
    required = {
        "index.html",
        "app.js",
        "bridge.js",
        "router.js",
        "store.js",
        "i18n.js",
        "styles/tokens.css",
        "styles/layout.css",
        "styles/components.css",
    }
    assert required <= {
        str(path.relative_to(PAGE)) for path in PAGE.rglob("*") if path.is_file()
    }
    parser = _parse_shell()

    assert parser.inline_script_text == []
    assert all(script.get("src", "").startswith("./") for script in parser.scripts)
    assert all(link.get("href", "").startswith("./") for link in parser.links)
    assert not any("style" in attrs or "onclick" in attrs for _, attrs in parser.elements)

    for path in PAGE.rglob("*.js"):
        source = path.read_text(encoding="utf-8")
        imports = re.findall(r"from\s+[\"']([^\"']+)[\"']", source)
        assert all(value.startswith("./") or value.startswith("../") for value in imports)
        assert "localStorage" not in source
        assert "document.cookie" not in source
        assert re.search(r"\bfetch\s*\(", source) is None


def test_shell_has_accessible_navigation_status_and_control_landmarks():
    parser = _parse_shell()
    elements = parser.elements

    assert any(
        tag == "a" and attrs.get("href") == "#main-content" and "skip-link" in attrs.get("class", "")
        for tag, attrs in elements
    )
    assert any(tag == "nav" and attrs.get("aria-label") for tag, attrs in elements)
    assert any(
        attrs.get("id") == "connection-state" and attrs.get("aria-live") == "polite"
        for _, attrs in elements
    )
    assert any(tag == "main" and attrs.get("id") == "main-content" for tag, attrs in elements)
    assert any(attrs.get("id") == "group-select" for _, attrs in elements)
    assert any(attrs.get("id") == "persona-name" for _, attrs in elements)
    assert any(attrs.get("id") == "config-version" for _, attrs in elements)
    assert not any(attrs.get("id") == "pause-runtime" for _, attrs in elements)
    assert any(attrs.get("id") == "system-status" for _, attrs in elements)
    assert any(attrs.get("id") == "inspector" for _, attrs in elements)


def test_theme_tokens_are_restrained_accessible_and_motion_safe():
    tokens = (PAGE / "styles" / "tokens.css").read_text(encoding="utf-8")
    layout = (PAGE / "styles" / "layout.css").read_text(encoding="utf-8")
    components = (PAGE / "styles" / "components.css").read_text(encoding="utf-8")
    combined = "\n".join((tokens, layout, components))

    for token in (
        "--color-bg",
        "--color-surface",
        "--color-ink",
        "--color-muted",
        "--color-primary",
        "--color-danger",
        "--focus-ring",
        "--z-dropdown",
        "--z-sticky",
        "--z-modal",
    ):
        assert token in tokens
    assert "oklch(" in tokens
    assert "[data-theme=\"dark\"]" in tokens
    assert "prefers-color-scheme: dark" in tokens
    assert "prefers-reduced-motion: reduce" in combined
    assert "@media (max-width:" in layout
    assert "background-clip: text" not in combined
    assert "backdrop-filter" not in combined
    assert "repeating-linear-gradient" not in combined
    assert not re.search(r"border-radius:\s*(?:3[2-9]|[4-9]\d)px", combined)


def test_bridge_uses_only_astrbot_page_bridge_and_exposes_real_states():
    source = (PAGE / "bridge.js").read_text(encoding="utf-8")

    assert "window.AstrBotPluginPage" in source
    assert "apiGet" in source
    assert "apiPost" in source
    assert "subscribeSSE" in source
    assert "polling" in source
    assert "disconnected" in source
    assert "conflict" in source
    assert "15_000" in source


def test_all_es_modules_parse_and_resolve_only_existing_relative_modules():
    for path in PAGE.rglob("*.js"):
        source = path.read_text(encoding="utf-8")
        subprocess.run(
            ["node", "--input-type=module", "--check"],
            input=source,
            text=True,
            capture_output=True,
            check=True,
        )
        for imported in re.findall(r"from\s+[\"']([^\"']+)[\"']", source):
            if imported.startswith("."):
                assert (path.parent / imported).resolve().is_file()
