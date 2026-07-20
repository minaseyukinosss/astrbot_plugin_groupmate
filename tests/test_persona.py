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


def test_bundled_persona_contains_non_customer_service_rules():
    prompt = BundledPersonaProvider().bundled_system_prompt()

    assert "爱弥斯" in prompt
    assert "不是客服" in prompt
    assert "默认不反问收尾" in prompt
