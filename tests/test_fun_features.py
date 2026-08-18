import pytest

from groupmate.fun import FunRuntime
from groupmate.fun.features import DynamicCardFeature
from groupmate.host.config import AstrBotConfigParser
from groupmate.memory.store import SQLiteMemoryStore
from groupmate.models import ChatMessage


class FakeActions:
    def __init__(self):
        self.cards = []

    async def set_own_group_card(self, group_id: str, card: str) -> str:
        self.cards.append((str(group_id), str(card)))
        return ""


def _msg(index, sender_id, text):
    return ChatMessage(
        message_id="m{}".format(index),
        group_id="100",
        sender_id=str(sender_id),
        sender_name="成员{}".format(sender_id),
        text=text,
        timestamp=1000 + index,
    )


@pytest.mark.asyncio
async def test_dynamic_card_records_status_card_and_question_context(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "groupmate.db")
    settings = AstrBotConfigParser().parse(
        {
            "fun_group": {
                "enabled": True,
                "dynamic_card": {
                    "enabled": True,
                    "min_interval_minutes": 15,
                },
            }
        }
    ).fun
    messages = (
        _msg(1, "1", "不是吧？"),
        _msg(2, "2", "怎么又这样！"),
        _msg(3, "3", "？"),
        _msg(4, "1", "你再说一遍？"),
        _msg(5, "2", "我就说"),
        _msg(6, "3", "来了来了"),
        _msg(7, "4", "这么热闹？"),
        _msg(8, "2", "？？"),
    )
    actions = FakeActions()
    runtime = FunRuntime(
        persona_id="aemeath",
        memory=store,
        actions=actions,
        settings=settings,
        group_ids_getter=lambda: ("100",),
        recent_messages_getter=lambda _group_id: messages,
        paused_getter=lambda: False,
        features=(DynamicCardFeature(settings.dynamic_card),),
        clock=lambda: 2000,
    )
    try:
        event = await runtime.refresh_dynamic_card("100")

        assert event is not None
        assert event.status == "active"
        assert actions.cards == [("100", event.public_value)]
        assert event.feature_id == "dynamic_card"
        assert "星炬学院生活状态牌" in event.private_context["social_intent"]
        assert "不指向任何群友" in event.private_context["social_intent"]
        assert event.private_context["target_policy"] == "none"
        assert event.private_context["visible_cause"]
        assert event.private_context["answer_angle"]
        assert event.private_context["reply_cues"]
        assert event.private_context["next_refresh_at"] > 2000
        assert event.participants == ()
        assert "谁急" not in event.public_value
        assert "谁是" not in event.public_value

        followup = ChatMessage(
            message_id="q1",
            group_id="100",
            sender_id="9",
            sender_name="追问者",
            text="你这名片什么意思？",
            timestamp=2001,
        )
        context = runtime.active_context_for_message(followup)

        assert "<fun_feature_context>" in context
        assert event.public_value in context
        assert "星炬学院生活状态牌" in context
        assert "不指向任何群友" in context
        assert "不猜人" in context
        assert "当事人参考" not in context
        assert "追问者" not in context
        assert "根据" not in context

        involved_followup = ChatMessage(
            message_id="q2",
            group_id="100",
            sender_id="2",
            sender_name="成员2",
            text="你这名片说谁急了",
            timestamp=2002,
        )
        involved_context = runtime.active_context_for_message(involved_followup)

        assert "不要追溯谁触发" in involved_context
        assert "追问者" not in involved_context
    finally:
        store.close()


@pytest.mark.asyncio
async def test_dynamic_card_manual_refresh_can_use_day_rhythm_without_recent_messages(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "groupmate.db")
    settings = AstrBotConfigParser().parse(
        {
            "fun_group": {
                "enabled": True,
                "dynamic_card": {
                    "enabled": True,
                    "min_interval_minutes": 30,
                },
            }
        }
    ).fun
    actions = FakeActions()
    runtime = FunRuntime(
        persona_id="aemeath",
        memory=store,
        actions=actions,
        settings=settings,
        group_ids_getter=lambda: ("100",),
        recent_messages_getter=lambda _group_id: (),
        paused_getter=lambda: False,
        features=(DynamicCardFeature(settings.dynamic_card),),
        clock=lambda: 2000,
    )
    try:
        event = await runtime.refresh_dynamic_card("100")

        assert event is not None
        assert event.status == "active"
        assert event.private_context["scene"].startswith("academy_")
        assert event.private_context["target_policy"] == "none"
        assert actions.cards == [("100", event.public_value)]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_dynamic_card_is_opt_in(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "groupmate.db")
    settings = AstrBotConfigParser().parse({}).fun
    actions = FakeActions()
    runtime = FunRuntime(
        persona_id="aemeath",
        memory=store,
        actions=actions,
        settings=settings,
        group_ids_getter=lambda: ("100",),
        recent_messages_getter=lambda _group_id: (_msg(1, "1", "谁急了？"),),
        paused_getter=lambda: False,
        features=(DynamicCardFeature(settings.dynamic_card),),
        clock=lambda: 2000,
    )
    try:
        assert await runtime.refresh_dynamic_card("100") is None
        assert actions.cards == []
    finally:
        store.close()
