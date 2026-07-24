from pathlib import Path


def test_settings_plugin_page_assets_exist():
    root = Path(__file__).resolve().parents[1] / "pages" / "settings"
    assert (root / "index.html").is_file()
    assert (root / "app.js").is_file()
    assert (root / "style.css").is_file()
    html = (root / "index.html").read_text(encoding="utf-8")
    assert "Groupmate" in html
    assert "./app.js" in html
