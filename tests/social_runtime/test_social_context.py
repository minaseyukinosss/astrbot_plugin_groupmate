import asyncio
from types import MappingProxyType

from groupmate.social_runtime.contracts import RuntimeMode, SocialEventEnvelope
from groupmate.social_runtime.chorus import ChorusEvidence
from groupmate.social_runtime.manager import SocialRuntimeManager
from groupmate.social_runtime.profile.contracts import MemberAlias
from groupmate.social_runtime.social_context import SceneContextBuilder
from groupmate.social_runtime.knowledge.contracts import TopicUnderstandingFrame


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
    assert {"m2", "m4"} <= {item.event_id for item in context.events}
    assert [item.occurred_at for item in context.events] == sorted(item.occurred_at for item in context.events)
    facts = {item["event_id"]: item for item in context.to_model_facts()["events"]}
    assert facts["m2"]["text"] == "可以放三条 Few-Shot"


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


def test_scene_context_keeps_frozen_dialogue_without_second_packing():
    events = tuple(_event(str(i), "字" * 120, occurred_at=100 + i,
                          actor_id="bot" if i == 0 else "u1",
                          origin_kind="BOT_TEXT" if i == 0 else "USER_TEXT")
                   for i in range(16))
    context = SceneContextBuilder().build(
        source_event=_event("15", "原始未裁剪消息", occurred_at=115),
        context_events=events, focus_event_ids=("15",), target_id="u1",
        topic_id=None, persona_actor_id="bot", persona_aliases=(),
        member_refs={}, profile=None, relationship_memories=(), frozen_dialogue=True,
    )
    assert [item.event_id for item in context.events] == [item.event_id for item in events]
    assert context.events[0].origin_kind == "BOT_TEXT"
    assert context.current_text == events[-1].payload["text"]
    assert all(item.text == "字" * 120 for item in context.events)


def test_scene_context_reuses_the_frozen_topic_understanding_frame():
    topic = TopicUnderstandingFrame.create(
        frame_id="knowledge-frame:1",
        game_ids=("game:genshin-impact",),
        resolved_entities=(),
        resolved_terms=(),
        discourse_referents=(),
        version_reference=None,
        conversation_intent_hint="stable_game_chat",
        ambiguity_codes=(),
        confidence=0.9,
        supporting_knowledge_ids=("game-semantic:genshin-impact",),
    )
    context = SceneContextBuilder(max_chars=800).build(
        source_event=_event("m1", "原神保底"),
        context_events=(),
        focus_event_ids=("m1",),
        target_id="u1",
        topic_id="m1",
        persona_actor_id="aemeath",
        persona_aliases=("爱弥斯",),
        member_refs={},
        profile=None,
        relationship_memories=(),
        topic_understanding=topic,
    )

    facts = context.to_model_facts()["facts"]["topic_understanding"]
    assert facts["frame_id"] == "knowledge-frame:1"
    assert facts == topic.to_prompt_facts()


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


def test_scene_context_attaches_chorus_without_mutating_original():
    context = SceneContextBuilder(max_chars=600).build(
        source_event=_event("m2", "小林今天请客", actor_id="u2", occurred_at=120),
        context_events=(_event("m1", "小林今天请客", actor_id="u1", occurred_at=110),),
        focus_event_ids=("m1", "m2"),
        target_id="u2",
        topic_id="m1",
        persona_actor_id="aemeath",
        persona_aliases=("爱弥斯",),
        member_refs={"u9": ("小林",)},
        profile=None,
        relationship_memories=(),
    )
    evidence = ChorusEvidence(
        chain_id="chorus:abc",
        payload="小林今天请客",
        normalized_key="小林今天请客",
        event_ids=("m1", "m2"),
        participant_ids=("u1", "u2"),
        already_joined=False,
    )

    attached = context.with_chorus(evidence)

    assert context.chorus is None
    assert attached.chorus == evidence
    assert attached.events == context.events
