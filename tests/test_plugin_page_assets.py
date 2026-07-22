from pathlib import Path
import re


PAGE_ROOT = Path(__file__).resolve().parents[1] / "pages" / "control-center"


def test_control_center_page_assets_exist_and_use_relative_scripts():
    index = PAGE_ROOT / "index.html"
    app = PAGE_ROOT / "app.js"
    style = PAGE_ROOT / "style.css"

    assert index.is_file()
    assert app.is_file()
    assert style.is_file()

    index_text = index.read_text(encoding="utf-8")
    app_text = app.read_text(encoding="utf-8")
    assert './style.css' in index_text
    assert './app.js' in index_text
    assert 'window.AstrBotPluginPage' in app_text
    assert '<script src="http' not in index_text
    assert '<script type="module" src="http' not in index_text


def test_control_center_uses_only_plugin_relative_endpoints():
    app_text = (PAGE_ROOT / "app.js").read_text(encoding="utf-8")

    for endpoint in (
        "dashboard/overview",
        "shadow/decisions",
        "runtime/pause",
        "runtime/resume",
        "shadow/export",
    ):
        assert endpoint in app_text
    assert not re.search(r"https?://|/api/v1/", app_text)
