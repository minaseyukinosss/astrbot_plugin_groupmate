"""Core 单元测试：装配分层与 Session。"""

from groupmate.core.context_assembly import DYNAMIC_BLOCK_ORDER, ContextAssembly
from groupmate.core.session import GroupSession
from groupmate.persona.aemeath import (
    DEFAULT_RELATIONSHIPS,
    PACK_DIR,
    AemeathPersonaProvider,
)


def _assembly(**kwargs) -> ContextAssembly:
    return ContextAssembly(
        pack_dir=PACK_DIR,
        relationships=DEFAULT_RELATIONSHIPS,
        character_name="爱弥斯",
        **kwargs,
    )


def test_assembly_system_separates_identity_and_constraints():
    system = _assembly().build_system()

    assert "角色扮演" in system
    assert "爱弥斯" in system
    assert "优先级声明" in system or "出戏防火墙" in system or "多人同时在场" in system
    assert "最后提醒" in system
    assert "<SILENCE>" in system
    assert "<mood>" not in system


def test_assembly_system_includes_group_brief():
    system = _assembly(group_brief="这个群爱聊游戏和抽卡。").build_system()
    assert "当前群氛围" in system
    assert "抽卡" in system


def test_assembly_user_includes_speak_note_and_voice_anchor(topic_snapshot):
    session = GroupSession("g1", character_name="爱弥斯")
    session.append_assistant("刚才那句", 99)
    user = _assembly().build_user(
        topic_snapshot,
        [],
        contribution="接一下",
        soft_trigger=True,
        session=session,
    )

    assert "<voice_anchor>" in user
    assert "<speak_note>" in user
    assert "<SILENCE>" in user
    assert "<session_turns>" in user
    assert "刚才那句" in user
    assert "<mood>" in user
    assert "<relationship_line>" in user
    assert "soft" in user.lower() or "路过" in user or "开口纪律" in user


def test_dynamic_block_order_is_locked(topic_snapshot):
    session = GroupSession("g1", character_name="爱弥斯")
    session.append_assistant("我说过会陪你", 90)
    user = _assembly().build_user(
        topic_snapshot,
        [],
        contribution="接一下",
        soft_trigger=False,
        session=session,
        mood_key="bright",
    )
    positions = []
    for name in DYNAMIC_BLOCK_ORDER:
        tag = "<{}>".format(name)
        # voice_anchor / speak_note / reply_task use tags; mood/relationship_line too
        idx = user.find(tag)
        if idx < 0 and name == "self_episodes":
            continue
        if idx < 0 and name in ("relevant_memories", "memory_guide"):
            continue
        assert idx >= 0, name
        positions.append(idx)
    assert positions == sorted(positions)
    assert DYNAMIC_BLOCK_ORDER[0] == "recent_messages"
    assert DYNAMIC_BLOCK_ORDER[-1] == "reply_task"


def test_self_episodes_on_recall(topic_snapshot, message_factory):
    session = GroupSession("g1", character_name="爱弥斯")
    session.append_assistant("那局我帮你盯着", 80)
    messages = list(topic_snapshot.messages) + [
        message_factory(message_id="r1", text="你之前不是说会帮我吗", timestamp=120)
    ]
    from groupmate.models import TopicSnapshot

    topic = TopicSnapshot(
        topic_snapshot.topic_id,
        topic_snapshot.group_id,
        tuple(messages),
        topic_snapshot.created_at,
        120,
    )
    user = _assembly().build_user(topic, [], session=session)
    assert "<self_episodes>" in user
    assert "那局我帮你盯着" in user


def test_bundled_provider_loads_product_pack():
    prompt = AemeathPersonaProvider().system_text()
    assert "Character: 核心定义" in prompt
    assert "鸣潮" in prompt
    assert "禁词表" in prompt or "Negative Vocabulary" in prompt
    assert "多人同时在场" in prompt


def test_session_turn_limit():
    session = GroupSession("g1", max_turns=4, character_name="角色")
    for index in range(6):
        session.append_user("A", "u" + str(index), index)
        session.append_assistant("a" + str(index), index)
    turns = session.recent_turns(10)
    assert len(turns) == 4
    assert turns[-1].speaker == "角色"


def test_core_sources_have_no_product_hardcode():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "groupmate" / "core"
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "爱弥斯" not in text, path.name
        assert "aemeath" not in text.lower(), path.name
