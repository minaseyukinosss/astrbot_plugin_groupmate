from pathlib import Path


def test_settings_plugin_page_assets_exist():
    root = Path(__file__).resolve().parents[1] / "pages" / "settings"
    assert (root / "index.html").is_file()
    assert (root / "app.js").is_file()
    assert (root / "style.css").is_file()
    html = (root / "index.html").read_text(encoding="utf-8")
    script = (root / "app.js").read_text(encoding="utf-8")
    combined = html + script
    assert "Groupmate" in html
    assert "决策" in html
    assert 'data-module="decision"' in html
    assert 'data-module="runtime"' in html
    assert 'data-module="config"' in html
    assert 'data-module="future"' in html
    assert "apiGet(\"decisions\"" in script or "apiGet('decisions'" in script
    assert "部署摘要" not in html
    assert "整份人格覆盖" not in combined
    assert "persona_id" not in combined
    assert "persona_prompt" not in combined
    assert "max_reply_chars" not in combined
    assert "group_brief" not in combined
    assert "./app.js" in html
