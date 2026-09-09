from groupmate.social_runtime.stickers.request import parse_sticker_ask


def test_asks_one_sticker():
    ask = parse_sticker_ask("来张表情", addressed=True)
    assert ask is not None
    assert ask.pack is False
    assert ask.specified is False


def test_asks_named_attitude():
    ask = parse_sticker_ask("发个无奈的表情", addressed=True)
    assert ask is not None
    assert ask.specified is True
    assert "无奈" in ask.query
    assert ask.pack is False


def test_asks_a_pack():
    ask = parse_sticker_ask("来一堆表情", addressed=True)
    assert ask is not None
    assert ask.pack is True
    assert ask.specified is False


def test_refuses_sticker_send():
    assert parse_sticker_ask("别发表情", addressed=True) is None
    assert parse_sticker_ask("不要给我发表情包", addressed=True) is None


def test_ignores_bq_command():
    assert parse_sticker_ask("bq 开心", addressed=True) is None
    assert parse_sticker_ask("BQ表情", addressed=True) is None


def test_requires_addressing_the_bot():
    assert parse_sticker_ask("来张表情", addressed=False) is None


def test_wake_word_is_not_a_kind():
    ask = parse_sticker_ask("小爱来个表情包", addressed=True)
    assert ask is not None
    assert ask.specified is False
    assert "小爱" not in ask.query


def test_kind_comes_from_the_request_slot():
    ask = parse_sticker_ask("小爱来个无奈的表情包", addressed=True)
    assert ask is not None
    assert ask.specified is True
    assert ask.query == "无奈"
    assert "小爱" not in ask.query


def test_pack_ask_after_alias():
    ask = parse_sticker_ask("小爱多来点表情包", addressed=True)
    assert ask is not None
    assert ask.pack is True
    assert ask.specified is False


def test_natural_phrasing_still_asks():
    samples = (
        "来个表情包呗",
        "能不能给我来张表情",
        "帮我发个表情包",
        "表情包来一张",
        "有没有表情包",
        "有表情包吗",
        "来个可爱的",
        "发个无奈的呗",
        "小爱 来点表情嘛",
        "求你整点表情包",
    )
    for text in samples:
        assert parse_sticker_ask(text, addressed=True) is not None, text


def test_bare_kind_without_sticker_word():
    ask = parse_sticker_ask("来个可爱的", addressed=True)
    assert ask is not None
    assert ask.specified is True
    assert "可爱" in ask.query


def test_casual_mention_is_not_an_ask():
    assert parse_sticker_ask("这个表情好搞笑", addressed=True) is None
    assert parse_sticker_ask("我刚发了个表情", addressed=True) is None
