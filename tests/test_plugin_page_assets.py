from pathlib import Path


def test_settings_plugin_page_assets_exist():
    root = Path(__file__).resolve().parents[1] / "pages" / "settings"
    assert (root / "index.html").is_file()
    assert (root / "app.js").is_file()
    assert (root / "style.css").is_file()
    html = (root / "index.html").read_text(encoding="utf-8")
    script = (root / "app.js").read_text(encoding="utf-8")
    assert "Groupmate" in html
    assert "爱弥斯行为与关系配置" in html
    assert "整份人格覆盖" not in html
    assert "persona_id" not in html + script
    assert "persona_prompt" not in html + script
    assert "./app.js" in html
