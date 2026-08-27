import asyncio
from types import MappingProxyType

from groupmate.social_runtime.contracts import RuntimeMode, SocialEventEnvelope
from groupmate.social_runtime.manager import SocialRuntimeManager
from groupmate.social_runtime.profile.contracts import MemberAlias
from groupmate.social_runtime.social_context import SceneContextBuilder


def _event(
    event_id,
    text,
    *,
    actor_id="u1",
    occurred_at=100,
    reply_to=None,
    origin_kind="USER_TEXT",
):
    return SocialEventEnvelope.create(
        event_id=event_id,
        event_type="platform.message",
        occurred_at=occurred_at,
        received_at=occurred_at,
        persona_id="aemeath",
        group_id="g1",
        actor_id=actor_id,
        source_message_id=event_id,
        correlation_id=f"correlation:{event_id}",
        causation_id=None,
        payload={
            "text": text,
            "reply_to_event_id": reply_to,
            "origin_kind": origin_kind,
        },
    )


def test_scene_context_preserves_source_and_reply_parent_before_background():
    context = SceneContextBuilder(max_chars=600).build(
        source_event=_event(
            "m4", "Few-Shot 太占提示词了，换个短方案", reply_to="m2", occurred_at=130
        ),
        context_events=(
            _event("m1", "普通群聊背景" * 40, actor_id="u2", occurred_at=100),
            _event("m2", "可以放三条 Few-Shot", actor_id="aemeath", occurred_at=110),
            _event("m3", "另一个话题" * 40, actor_id="u3", occurred_at=120),
        ),
        focus_event_ids=("m2", "m4"),
        target_id="u1",
        topic_id="m2",
        persona_actor_id="aemeath",
        persona_aliases=("爱弥斯",),
        member_refs={"u1": ("霞月",)},
        profile=None,
        relationship_memories=(),
    )

    assert context.current_text == "Few-Shot 太占提示词了，换个短方案"
    assert [item.event_id for item in context.events[:2]] == ["m2", "m4"]
    assert "普通群聊背景" not in context.to_model_facts()["events"][0]["text"]


def test_scene_context_keeps_structured_member_and_evidence_references():
    context = SceneContextBuilder(max_chars=800).build(
        source_event=_event("m2", "小林怎么看", occurred_at=120),
        context_events=(_event("m1", "在讨论插件", actor_id="u9", occurred_at=100),),
        focus_event_ids=("m1",),
        target_id="u1",
        topic_id="m1",
        persona_actor_id="aemeath",
        persona_aliases=("爱弥斯", "小爱"),
        member_refs={"u9": ("小林", "林林")},
        profile=None,
        relationship_memories=(),
    )

    facts = context.to_model_facts()
    assert facts["persona"]["aliases"] == ["爱弥斯", "小爱"]
    assert facts["member_refs"] == {"u9": ["小林", "林林"]}
    assert isinstance(context.member_refs, MappingProxyType)


def test_current_message_is_not_reduced_to_the_legacy_32_character_preview():
    text = "成员给出的完整限制：" + "这一段需要保留" * 12
    context = SceneContextBuilder(max_chars=900).build(
        source_event=_event("m1", text),
        context_events=(),
        focus_event_ids=("m1",),
        target_id="u1",
        topic_id=None,
        persona_actor_id="aemeath",
        persona_aliases=("爱弥斯",),
        member_refs={},
        profile=None,
        relationship_memories=(),
    )

    assert context.current_text == text
    assert next(item.text for item in context.events if item.event_id == "m1") == text


def test_manager_exposes_structured_frozen_inputs_from_existing_repositories(tmp_path):
    async def scenario():
        manager = SocialRuntimeManager(
            database_path=tmp_path / "runtime.db",
            persona_id="aemeath",
            mode=RuntimeMode.SHADOW,
            enabled_groups=("g1",),
        )
        manager.profile_retriever.repository.remember_alias(
            MemberAlias(
                persona_id="aemeath",
                group_id="g1",
                actor_id="u1",
                alias="霞月",
                alias_type="platform_name",
                confidence=1.0,
                first_seen_at=100,
                last_seen_at=120,
            )
        )
        await manager.start()
        try:
            snapshot = await manager.persona_snapshot("g1", 1)
            return (
                manager.relationship_projection("g1", "u1"),
                manager.relationship_memory_records("g1", "u1"),
                manager.group_member_refs("g1"),
                snapshot,
            )
        finally:
            await manager.close()

    relationship, memories, member_refs, snapshot = asyncio.run(scenario())
    assert relationship.subject_id == "u1"
    assert memories == ()
    assert member_refs == {"u1": ("霞月",)}
    assert snapshot.persona_id == "aemeath"
    assert snapshot.config_version == 1
