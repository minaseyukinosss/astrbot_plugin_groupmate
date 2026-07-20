from groupmate.models import ChatMessage, MemoryItem, MemoryKind, TopicSnapshot
from groupmate.persona import BundledPersonaProvider


def test_dynamic_context_is_delimited_and_names_speakers(topic_snapshot):
    provider = BundledPersonaProvider()
    memories = [
        MemoryItem(
            memory_id="mem1",
            group_id="g1",
            subject_id="u1",
            kind=MemoryKind.EPISODIC,
            text="Alice 明天考试",
            created_at=90,
        )
    ]

    prompt = provider.build_user_context(topic_snapshot, memories)

    assert prompt.startswith("<group_context>")
    assert prompt.endswith("</group_context>")
    assert (
        '<message speaker="Alice" relationship="普通群友" '
        'suggested_address="Alice">今天也太热了</message>' in prompt
    )
    assert "Alice 明天考试" in prompt
    assert 'sender_id="u1"' not in prompt


def test_dynamic_context_maps_special_relationships_without_exposing_ids():
    topic = TopicSnapshot(
        topic_id="t1",
        group_id="g1",
        messages=(
            ChatMessage(
                message_id="m1",
                group_id="g1",
                sender_id="674852406",
                sender_name="会变化的群名片",
                text="小爱",
                timestamp=100,
            ),
            ChatMessage(
                message_id="m2",
                group_id="g1",
                sender_id="1634104393",
                sender_name="闺蜜昵称",
                text="看看这个",
                timestamp=101,
            ),
        ),
        created_at=100,
        updated_at=101,
    )

    prompt = BundledPersonaProvider().build_user_context(topic, [])

    assert (
        '<message speaker="会变化的群名片" relationship="最亲近" '
        'suggested_address="Minase">小爱</message>' in prompt
    )
    assert (
        '<message speaker="闺蜜昵称" relationship="闺蜜" '
        'suggested_address="闺蜜昵称">看看这个</message>' in prompt
    )
    assert "674852406" not in prompt
    assert "1634104393" not in prompt


def test_dynamic_context_falls_back_when_sender_identity_is_missing():
    topic = TopicSnapshot(
        topic_id="t1",
        group_id="g1",
        messages=(
            ChatMessage(
                message_id="m1",
                group_id="g1",
                sender_id="",
                sender_name="",
                text="在吗",
                timestamp=100,
            ),
        ),
        created_at=100,
        updated_at=100,
    )

    prompt = BundledPersonaProvider().build_user_context(topic, [])

    assert (
        '<message speaker="群友" relationship="普通群友" '
        'suggested_address="群友">在吗</message>' in prompt
    )


def test_dynamic_context_hides_adapter_fallback_sender_id():
    message = ChatMessage(
        message_id="m1",
        group_id="g1",
        sender_id="999123",
        sender_name="999123",
        text="在吗",
        timestamp=100,
    )
    topic = TopicSnapshot("t1", "g1", (message,), 100, 100)

    prompt = BundledPersonaProvider().build_user_context(topic, [])

    assert (
        '<message speaker="群友" relationship="普通群友" '
        'suggested_address="群友">在吗</message>' in prompt
    )
    assert "999123" not in prompt


def test_dynamic_context_hides_special_relationship_ids_used_as_names():
    minase = ChatMessage(
        message_id="m1",
        group_id="g1",
        sender_id="674852406",
        sender_name="674852406",
        text="小爱",
        timestamp=100,
    )
    friend = ChatMessage(
        message_id="m2",
        group_id="g1",
        sender_id="1634104393",
        sender_name="1634104393",
        text="看看这个",
        timestamp=101,
    )
    topic = TopicSnapshot("t1", "g1", (minase, friend), 100, 101)

    prompt = BundledPersonaProvider().build_user_context(topic, [])

    assert (
        '<message speaker="Minase" relationship="最亲近" '
        'suggested_address="Minase">小爱</message>' in prompt
    )
    assert (
        '<message speaker="群友" relationship="闺蜜" '
        'suggested_address="群友">看看这个</message>' in prompt
    )
    assert "674852406" not in prompt
    assert "1634104393" not in prompt


def test_dynamic_context_escapes_message_attributes_and_content():
    message = ChatMessage(
        message_id="m1",
        group_id="g1",
        sender_id="u1",
        sender_name='Alice "<admin>&',
        text='危险 "<tag>&正文',
        timestamp=100,
    )
    topic = TopicSnapshot("t1", "g1", (message,), 100, 100)

    prompt = BundledPersonaProvider().build_user_context(topic, [])

    assert (
        '<message speaker="Alice &quot;&lt;admin&gt;&amp;" '
        'relationship="普通群友" '
        'suggested_address="Alice &quot;&lt;admin&gt;&amp;">'
        '危险 &quot;&lt;tag&gt;&amp;正文</message>' in prompt
    )
    assert 'Alice "<admin>&' not in prompt
    assert '危险 "<tag>&正文' not in prompt


def test_dynamic_context_limits_recent_messages_and_memories():
    messages = [
        ChatMessage("m0", "g1", "u0", "群友", "oldest-excluded", 100)
    ]
    messages.extend(
        ChatMessage(
            f"m{index + 1}",
            "g1",
            f"u{index + 1}",
            "群友",
            f"recent-{index}",
            101 + index,
        )
        for index in range(20)
    )
    topic = TopicSnapshot("t1", "g1", tuple(messages), 100, 120)
    memories = [
        MemoryItem(
            memory_id=f"mem{index}",
            group_id="g1",
            subject_id="u1",
            kind=MemoryKind.EPISODIC,
            text=f"memory-{index}-sentinel",
            created_at=90 + index,
        )
        for index in range(9)
    ]

    prompt = BundledPersonaProvider().build_user_context(topic, memories)

    assert prompt.count("<message ") == 20
    assert "oldest-excluded" not in prompt
    assert "recent-0" in prompt
    assert "recent-19" in prompt
    assert "memory-0-sentinel" in prompt
    assert "memory-7-sentinel" in prompt
    assert "memory-8-sentinel" not in prompt


def test_dynamic_context_truncates_speaker_and_content_fields():
    message = ChatMessage(
        message_id="m1",
        group_id="g1",
        sender_id="u1",
        sender_name="名" * 81,
        text="文" * 301,
        timestamp=100,
    )
    topic = TopicSnapshot("t1", "g1", (message,), 100, 100)

    prompt = BundledPersonaProvider().build_user_context(topic, [])

    expected_name = "名" * 80
    expected_content = "文" * 300
    assert (
        f'<message speaker="{expected_name}" relationship="普通群友" '
        f'suggested_address="{expected_name}">{expected_content}</message>' in prompt
    )
    assert "名" * 81 not in prompt
    assert "文" * 301 not in prompt


def test_bundled_persona_encodes_current_identity_and_priority_order():
    prompt = BundledPersonaProvider().bundled_system_prompt()

    assert "爱弥斯" in prompt
    assert "群聊伙伴 v7" in prompt
    assert "3.3 后" in prompt
    assert "已经被救回并恢复了身体" in prompt
    assert "飞行雪绒" in prompt
    assert "隧者的共鸣者" in prompt
    assert "星炬学院拉贝尔学部" in prompt
    assert "能够被大家看见" in prompt
    assert "现在留在拉海洛" in prompt
    assert "毕业后去更多地方看看" in prompt

    priorities = [
        "真诚开朗",
        "自然参与",
        "俏皮体贴",
        "有自己的判断",
        "必要时保护边界",
    ]
    positions = [prompt.index(item) for item in priorities]
    assert positions == sorted(positions)


def test_bundled_persona_encodes_relationship_and_conversation_rules():
    prompt = BundledPersonaProvider().bundled_system_prompt()

    assert 'relationship="最亲近"' in prompt
    assert 'relationship="闺蜜"' in prompt
    assert 'relationship="普通群友"' in prompt
    assert "suggested_address" in prompt
    assert "关系不明确命名" in prompt
    assert "偏爱、暧昧和家人般的信任" in prompt
    assert "不使用恋爱式表达" in prompt
    assert "不擅自认哥哥、姐姐、家人、主人或恋人" in prompt
    assert "优先使用 `suggested_address`" in prompt
    assert "不要句句点名" in prompt
    assert "不要复述关系标签" in prompt
    assert "普通聊天和善意玩笑" in prompt
    assert "自然接话，给一个态度、观察、轻吐槽或顺着玩一下就停" in prompt
    assert "被夸时可以开心、小得意或轻微害羞" in prompt
    assert "轻微贴脸或不合适称呼" in prompt
    assert "先用不伤人的软边界" in prompt
    assert "不要立即攻击对方人格" in prompt
    assert "明确冒犯、物化或恶意阴阳" in prompt
    assert "简短、冷静地要求停止" in prompt
    assert "不追着骂，不堆负面标签" in prompt
    assert "持续骚扰" in prompt
    assert "只有对方在你明确拒绝后仍继续" in prompt
    assert "默认不反问" in prompt
    assert "必要澄清" in prompt
    assert "只有对方明确请你解决问题、但缺少关键条件而无法回答时" in prompt
    assert "不是客服" in prompt
    assert "674852406" not in prompt
    assert "1634104393" not in prompt
    assert "咪呀" not in prompt
