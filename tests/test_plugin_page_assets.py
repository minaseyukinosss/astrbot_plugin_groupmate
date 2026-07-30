from pathlib import Path


def test_settings_plugin_page_assets_exist():
    root = Path(__file__).resolve().parents[1] / "pages" / "settings"
    assert (root / "index.html").is_file()
    assert (root / "app.js").is_file()
    assert (root / "style.css").is_file()
    html = (root / "index.html").read_text(encoding="utf-8")
    script = (root / "app.js").read_text(encoding="utf-8")
    assert "Groupmate" in html
    assert "部署摘要" in html
    assert "整份人格覆盖" not in html
    assert "persona_id" not in html + script
    assert "persona_prompt" not in html + script
    assert "max_reply_chars" not in html + script
    assert "group_brief" not in html + script
    assert "./app.js" in html
