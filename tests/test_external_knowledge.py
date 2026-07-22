from groupmate.external_knowledge import needs_external_knowledge


def test_user_isa_example_needs_external():
    assert needs_external_knowledge("抖音isa怎么了。怎么那么多人骂她") is True


def test_explicit_search_needs_external():
    assert needs_external_knowledge("帮我查一下明天天气") is True
    assert needs_external_knowledge("搜索一下鸣潮最新活动") is True


def test_url_needs_external():
    assert needs_external_knowledge("看看这个 https://example.com/news") is True


def test_casual_chat_does_not_need_external():
    assert needs_external_knowledge("你今天怎样") is False
    assert needs_external_knowledge("给我买个风暴号吧") is False
    assert needs_external_knowledge("好无聊啊") is False


def test_empty_is_false():
    assert needs_external_knowledge("") is False
    assert needs_external_knowledge("   ") is False
