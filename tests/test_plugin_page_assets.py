from pathlib import Path


def test_settings_plugin_page_assets_exist():
    root = Path(__file__).resolve().parents[1] / "pages" / "settings"
    assert (root / "index.html").is_file()
    assert (root / "app.js").is_file()
    assert (root / "style.css").is_file()
    html = (root / "index.html").read_text(encoding="utf-8")
    script = (root / "app.js").read_text(encoding="utf-8")
    combined = html + script
    assert "爱弥斯" in html
    assert "群聊伙伴治理台" in html
    assert "决策" in html
    assert 'data-module="overview"' in html
    assert 'data-module="attention"' in html
    assert 'data-module="relationships"' in html
    assert 'data-module="members"' in html
    assert 'data-module="memory"' in html
    assert 'data-module="capabilities"' in html
    assert 'data-module="self"' in html
    assert 'data-module="decision"' in html
    assert 'data-module="quality"' in html
    assert 'data-module="governance"' in html
    assert "sidebar" in html
    assert "workspace" in html
    assert "apiGet(\"decisions\"" in script or "apiGet('decisions'" in script
    assert "apiGet(\"cognition\"" in script or "apiGet('cognition'" in script
    assert "relationships/correct" in script
    assert 'apiPost("members/correct"' in script
    assert 'apiPost("members/link"' in script
    assert "relationships/evidence/${encodeURIComponent" in script
    assert "memories/${encodeURIComponent" in script
    assert "governance/${encodeURIComponent" in script
    assert "修正原因" in html
    assert "证据时间线" in html
    assert "昵称轨迹" in html
    assert "身份关联" in html
    assert "相处中的事情" in html
    assert "continuity/${encodeURIComponent" in script
    assert "continuity_status_corrected" in script
    assert "最近跟进" in html
    assert "member-followups" in html
    assert "continuity/followups/${encodeURIComponent" in script
    assert "continuity_followup_rejected" in script
    assert 'CONTINUITY: "后续"' in script
    assert 'item.status !== "deleted"' in script
    assert "我答应过的事" in html
    assert "self-commitment-list" in html
    assert "commitments/${encodeURIComponent" in script
    assert "self_commitment_status_corrected" in script
    assert "self-commitment-scheduler" in html
    assert "data-run-self-commitment" in script
    assert "commitments/${encodeURIComponent(commitmentId)}/run" in script
    assert "renderSelf();" in script
    assert "否定原因" in html
    assert "复核这条关系证据" in html
    assert "wrong_person" in html
    assert "relationship_learning" in script
    assert "quality-learning-groups" in html
    assert "回滚原因" in html
    assert "describeValue" in script
    assert "host_interaction" in script
    assert "宿主互动" in script
    assert "playful_reply" in script
    assert "玩笑回应" in script
    assert "context-list" in html
    assert "当时上下文" in html
    assert "准备回应" in html
    assert "关系对象" in html
    assert "记忆对象" in html
    assert "renderTargetSummary" in script
    assert "renderContext" in script
    assert "applyFilters" in script
    assert "fillGroupFilter" in script
    assert "预留" not in html
    assert "后续再补" not in html
    assert "不承认" not in combined
    assert "我是 AI" not in combined
    assert "整份人格覆盖" not in combined
    assert "persona_id" not in combined
    assert "persona_prompt" not in combined
    assert "max_reply_chars" not in combined
    assert "group_brief" not in combined
    assert "./app.js" in html
