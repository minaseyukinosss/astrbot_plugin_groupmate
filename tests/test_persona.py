from groupmate.models import MemoryItem, MemoryKind
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
    assert "Alice: 今天也太热了" in prompt
    assert "Alice 明天考试" in prompt


def test_bundled_persona_contains_non_customer_service_rules():
    prompt = BundledPersonaProvider().bundled_system_prompt()

    assert "爱弥斯" in prompt
    assert "不是客服" in prompt
    assert "默认不反问收尾" in prompt

