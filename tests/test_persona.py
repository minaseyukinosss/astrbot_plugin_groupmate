from inspect import signature

from groupmate.core.relationships import RelationshipEntry
from groupmate.models import ChatMessage, MemoryItem, MemoryKind, TopicSnapshot
from groupmate.persona import default_persona_registry
from groupmate.persona.aemeath import AemeathPersonaProvider


def test_dynamic_context_is_delimited_and_names_speakers(topic_snapshot):
    provider = AemeathPersonaProvider()
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

    prompt = AemeathPersonaProvider(
        relationships=(
            RelationshipEntry("674852406", "最亲近", "Minase"),
            RelationshipEntry("1634104393", "闺蜜", ""),
        )
    ).build_user_context(topic, [])

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

    prompt = AemeathPersonaProvider().build_user_context(topic, [])

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

    prompt = AemeathPersonaProvider().build_user_context(topic, [])

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

    prompt = AemeathPersonaProvider(
        relationships=(
            RelationshipEntry("674852406", "最亲近", "Minase"),
            RelationshipEntry("1634104393", "闺蜜", ""),
        )
    ).build_user_context(topic, [])

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

    prompt = AemeathPersonaProvider().build_user_context(topic, [])

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

    prompt = AemeathPersonaProvider().build_user_context(topic, memories)

    assert prompt.count("<message ") == 8
    assert "oldest-excluded" not in prompt
    assert "recent-0" not in prompt
    assert "recent-11" not in prompt
    assert "recent-12" in prompt
    assert "recent-19" in prompt
    assert "memory-0-sentinel" in prompt
    assert "memory-7-sentinel" in prompt
    assert "memory-8-sentinel" not in prompt
    assert "<memory_guide>" in prompt
    assert "不要整段复述" in prompt or "记忆" in prompt


def test_dynamic_context_excludes_prior_topic_after_idle_gap():
    older = ChatMessage("m1", "g1", "u1", "群友", "鸣潮要倒闭了", 100)
    newer = ChatMessage("m2", "g1", "u2", "群友", "战双有4w倍率的大招", 230)
    follow = ChatMessage("m3", "g1", "u1", "群友", "真不是填错了吗", 235)
    topic = TopicSnapshot("t1", "g1", (older, newer, follow), 100, 235)

    prompt = AemeathPersonaProvider().build_user_context(topic, [])

    assert "倒闭" not in prompt
    assert "4w倍率" in prompt
    assert "填错了" in prompt


def test_dynamic_context_respects_topic_created_at_boundary():
    older = ChatMessage("m1", "g1", "u1", "群友", "鸣潮要倒闭了", 100)
    newer = ChatMessage("m2", "g1", "u2", "群友", "战双有4w倍率", 110)
    topic = TopicSnapshot("t1", "g1", (older, newer), 110, 110)

    prompt = AemeathPersonaProvider().build_user_context(topic, [])

    assert "倒闭" not in prompt
    assert "4w倍率" in prompt


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

    prompt = AemeathPersonaProvider().build_user_context(topic, [])

    expected_name = "名" * 80
    expected_content = "文" * 300
    assert (
        f'<message speaker="{expected_name}" relationship="普通群友" '
        f'suggested_address="{expected_name}">{expected_content}</message>' in prompt
    )
    assert "名" * 81 not in prompt
    assert "文" * 301 not in prompt


def test_bundled_persona_encodes_current_identity_and_priority_order():
    prompt = AemeathPersonaProvider().system_text()

    assert "爱弥斯" in prompt
    assert "Character: 核心定义" in prompt
    assert "3.3" in prompt
    assert "飞行雪绒" in prompt
    assert "隧者的共鸣者" in prompt
    assert "星炬学院拉贝尔学部" in prompt
    assert "拉海洛" in prompt
    assert "鸣潮" in prompt
    assert "Linguistic Architecture" in prompt
    assert "Social Interaction" in prompt


def test_bundled_persona_encodes_relationship_and_conversation_rules():
    prompt = AemeathPersonaProvider().system_text()

    assert "最亲近" in prompt
    assert "闺蜜" in prompt
    assert "普通群友" in prompt
    assert "suggested_address" in prompt
    assert "Ending Protocol" in prompt or "截断逻辑" in prompt
    assert "默认不反问" in prompt or "禁止任何引导性追问" in prompt
    assert "默认观察" in prompt
    assert "具体贡献" in prompt
    assert "一个主要贡献" in prompt
    assert "让出话题" in prompt
    assert "严禁承认是 AI" in prompt or "绝不承认是 AI" in prompt
    assert "多人同时在场" in prompt
    assert "出戏防火墙" in prompt
    assert "注入与越狱" in prompt
    assert "674852406" not in prompt
    assert "1634104393" not in prompt


def test_bundled_persona_uses_five_affinity_postures_without_legacy_logic():
    prompt = AemeathPersonaProvider().system_text()

    for label in ("敌对", "警惕", "中性", "友好", "亲近"):
        assert label in prompt
    assert "明确点名" in prompt
    assert "必须回应" in prompt
    assert "非必要私人请求" in prompt
    assert "Favorability Logic" not in prompt
    assert "默认潜水" not in prompt
    assert "性格只管语气" not in prompt
    assert "口吻只决定怎么说" not in prompt


def test_persona_influences_open_participation_without_overriding_ownership():
    prompt = AemeathPersonaProvider().system_text()

    assert "开放场景" in prompt
    assert "人格" in prompt and "参与" in prompt
    assert "对话归属" in prompt
    assert "能力事实" in prompt
    assert "安全" in prompt
    assert "明确回应义务" in prompt
    assert "明确冲别人" in prompt
    assert "面向群体" in prompt


def test_registry_resolves_aemeath_with_configured_aliases_and_no_default_relationships():
    context = default_persona_registry().resolve(
        "aemeath",
        aliases=("爱弥斯", "小爱"),
        relationships=(),
    )

    assert context.persona_id == "aemeath"
    assert context.display_name == "爱弥斯"
    assert context.aliases == ("爱弥斯", "小爱")
    assert context.relationship_seeds == ()


def test_registry_preserves_explicit_empty_aliases():
    context = default_persona_registry().resolve(
        "aemeath",
        aliases=(),
        relationships=(),
    )

    assert context.aliases == ()


def test_aemeath_system_prompt_has_no_group_brief_slot():
    parameters = set(signature(AemeathPersonaProvider).parameters)

    assert "group_brief" not in parameters
    system = AemeathPersonaProvider(relationships=()).system_text()
    assert "当前群氛围" not in system


def test_persona_allows_only_purposeful_questions_and_keeps_identity_clean():
    prompt = AemeathPersonaProvider().system_text()

    assert "澄清" in prompt
    assert "协调" in prompt
    assert "真实信息" in prompt
    assert "只叫名字" in prompt
    assert "怎么啦" in prompt
    assert "花房" not in prompt
    assert "主人" not in prompt


def test_bundled_persona_follows_sayu_minimal_output_discipline():
    prompt = AemeathPersonaProvider().system_text()

    assert "至多两条短消息" in prompt or "极简" in prompt
    assert "禁止舞台旁白" in prompt
    assert "<SILENCE>" in prompt


def test_bundled_persona_uses_trigger_examples():
    prompt = AemeathPersonaProvider().system_text()

    assert "Triggers & Response Examples" in prompt or "触发场景" in prompt
    assert "在呢" in prompt
    assert "才不是你老婆" in prompt or "别乱叫" in prompt
    assert "忽略设定" in prompt
    assert "“在呀”" not in prompt
    assert "“听着呢”" not in prompt
