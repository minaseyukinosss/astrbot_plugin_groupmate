"""AstrBot @ wake must suppress default agent via call_llm=True."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from groupmate.host import AstrBotBridge
from groupmate.host.bridge import TurnOwner
from groupmate.host.config import AstrBotConfigParser
from groupmate.core.response_act import TaskResolutionStatus
from groupmate.models import (
    ChatMessage,
    InteractionScene,
    MessageOrigin,
)


class _FakeEvent:
    def __init__(
        self,
        *,
        at_bot: bool = True,
        group_id: str = "912113397",
        text: str = "给我买个风暴号吧",
    ) -> None:
        self.call_llm = False
        self.is_at_or_wake_command = at_bot
        self.unified_msg_origin = "aiocqhttp:GroupMessage:{}".format(group_id)
        self.message_str = text
        text_segment = " {}".format(text) if at_bot else text
        self.message_obj = SimpleNamespace(
            message_id="42",
            timestamp=1_700_000_000,
            raw_message={
                "message_id": "42",
                "group_id": group_id,
                "user_id": "10001",
                "time": 1_700_000_000,
                "sender": {"nickname": "恺撒"},
                "message": (
                    [
                        {"type": "at", "data": {"qq": "20002", "name": "爱弥斯"}},
                        {"type": "text", "data": {"text": text_segment}},
                    ]
                    if at_bot
                    else [{"type": "text", "data": {"text": text}}]
                ),
            },
        )
        self._group_id = group_id

    def get_group_id(self) -> str:
        return self._group_id

    def get_self_id(self) -> str:
        return "20002"

    def get_sender_id(self) -> str:
        return "10001"

    def get_sender_name(self) -> str:
        return "恺撒"

    def get_extra(self, key=None, default=None):
        del key
        return default

    def should_call_llm(self, call_llm: bool) -> None:
        self.call_llm = call_llm


def _bridge(tmp_path: Path, **settings) -> AstrBotBridge:
    raw = {
        "scope_group": {
            "enabled_groups": settings.pop("enabled_groups", []),
        },
        "persona_group": {
            "persona_aliases": {
                "aemeath": settings.pop(
                    "aliases",
                    ["爱弥斯", "小爱", "飞行雪绒"],
                )
            },
            "relationships": {"aemeath": []},
        },
        "provider_group": {
            "generation_provider": settings.pop("generation_provider", ""),
            "vision_enabled": settings.pop("vision_enabled", True),
            "vision_provider": settings.pop("vision_provider", ""),
        },
        "interaction_group": {
            "poke_enabled": settings.pop("poke_enabled", False),
            "poke_back_enabled": settings.pop("poke_back_enabled", False),
            "poke_exclusive": settings.pop("poke_exclusive", False),
            "poke_face_enabled": settings.pop("poke_face_enabled", False),
        },
    }
    assert not settings
    return AstrBotBridge(
        context=SimpleNamespace(),
        settings=AstrBotConfigParser().parse(raw),
        data_dir=tmp_path,
    )


def _synthetic_poke():
    return ChatMessage(
        message_id="poke-1",
        group_id="912113397",
        sender_id="10001",
        sender_name="恺撒",
        text="",
        timestamp=1_700_000_000,
        segment_types=("poke",),
        origin=MessageOrigin.SYSTEM_SYNTHETIC,
        metadata={
            "interaction_kind": "poke",
            "target_id": "20002",
            "source_adapter": "aiocqhttp_poke",
        },
    )


def test_adapted_event_marks_owner_and_submits_exact_message():
    async def scenario():
        bridge = object.__new__(AstrBotBridge)
        bridge._paused = False
        actor = SimpleNamespace(calls=[])

        async def submit(message, schedule):
            actor.calls.append((message, schedule))

        async def prepare(event):
            del event
            actor.submit = submit
            return actor

        bridge._prepare_actor = prepare
        event = _FakeEvent(at_bot=False)
        message = _synthetic_poke()
        accepted = await bridge.handle_adapted_event(event, message)
        return event, message, actor.calls, accepted

    event, message, calls, accepted = asyncio.run(scenario())

    assert accepted is True
    assert event.call_llm is True
    assert calls == [(message, True)]
    assert calls[0][0] is message


def test_adapted_event_paused_submits_without_schedule():
    async def scenario():
        bridge = object.__new__(AstrBotBridge)
        bridge._paused = True
        actor = SimpleNamespace(calls=[])

        async def submit(message, schedule):
            actor.calls.append((message, schedule))

        async def prepare(event):
            del event
            actor.submit = submit
            return actor

        bridge._prepare_actor = prepare
        accepted = await bridge.handle_adapted_event(
            _FakeEvent(at_bot=False),
            _synthetic_poke(),
        )
        return actor.calls, accepted

    calls, accepted = asyncio.run(scenario())

    assert accepted is True
    assert calls == [(_synthetic_poke(), False)]


def test_status_reports_poke_adapter_state(tmp_path):
    bridge = _bridge(tmp_path, poke_enabled=True)

    assert bridge.status()["poke_adapter"] == "enabled"
    assert bridge.status()["poke_back"] == "disabled"


def test_status_reports_poke_back_enabled(tmp_path):
    bridge = _bridge(tmp_path, poke_enabled=True, poke_back_enabled=True)

    assert bridge.status()["poke_back"] == "enabled"


def test_should_take_native_wake_on_at(tmp_path):
    bridge = _bridge(tmp_path)
    event = _FakeEvent(at_bot=True)
    assert bridge.should_take_native_wake(event) is True


def test_suppress_polarity_matches_astrbot_process_stage(tmp_path):
    """AstrBot ProcessStage runs default agent when ``not event.call_llm``.

    Setting False is a no-op (already default) and causes double reply with @.
    """
    bridge = _bridge(tmp_path)
    event = _FakeEvent(at_bot=True)
    assert bridge.should_take_native_wake(event) is True
    assert bridge.should_defer_native_wake_to_astrbot(event) is False

    # Correct suppress: True means "prohibit default LLM".
    event.should_call_llm(True)
    assert event.call_llm is True
    # ProcessStage gate: is_at_or_wake and not call_llm → must be False now.
    assert not (event.is_at_or_wake_command and not event.call_llm)


def test_defer_search_question_to_astrbot(tmp_path):
    bridge = _bridge(tmp_path)
    event = _FakeEvent(text="抖音isa怎么了。怎么那么多人骂她")
    assert bridge.owner_for_event(event) is TurnOwner.ASTRBOT_AGENT
    assert bridge.should_take_native_wake(event) is True
    assert bridge.should_defer_native_wake_to_astrbot(event) is True
    # Defer path must leave call_llm False so ProcessStage can run Agent+tools.
    assert event.call_llm is False
    assert event.is_at_or_wake_command and not event.call_llm


def test_casual_at_does_not_defer(tmp_path):
    bridge = _bridge(tmp_path)
    event = _FakeEvent(text="你今天怎样")
    assert bridge.owner_for_event(event) is TurnOwner.GROUPMATE
    assert bridge.should_take_native_wake(event) is True
    assert bridge.should_defer_native_wake_to_astrbot(event) is False


def test_non_native_message_is_observe_only_owner(tmp_path):
    bridge = _bridge(tmp_path)
    event = _FakeEvent(at_bot=False, text="今天好热")

    assert bridge.owner_for_event(event) is TurnOwner.OBSERVE_ONLY


def test_native_wake_has_exactly_one_owner(tmp_path):
    bridge = _bridge(tmp_path)

    assert bridge.owner_for_event(_FakeEvent(text="你今天怎样")) is TurnOwner.GROUPMATE
    assert (
        bridge.owner_for_event(_FakeEvent(text="查一下今天发布的公告"))
        is TurnOwner.ASTRBOT_AGENT
    )


def test_owner_application_suppresses_only_groupmate_owned_wake(tmp_path):
    bridge = _bridge(tmp_path)
    groupmate_event = _FakeEvent(text="你今天怎样")
    agent_event = _FakeEvent(text="搜索今天的新闻")
    observe_event = _FakeEvent(at_bot=False, text="路过一下")

    assert bridge.apply_owner_to_event(groupmate_event) is TurnOwner.GROUPMATE
    assert groupmate_event.call_llm is True

    assert bridge.apply_owner_to_event(agent_event) is TurnOwner.ASTRBOT_AGENT
    assert agent_event.call_llm is False

    assert bridge.apply_owner_to_event(observe_event) is TurnOwner.OBSERVE_ONLY
    assert observe_event.call_llm is False


def test_pause_still_observes_without_dispatch(tmp_path):
    async def scenario():
        bridge = _bridge(tmp_path)
        bridge.paused = True
        event = _FakeEvent(at_bot=False, text="暂停期间也要记住")
        await bridge.handle_event(event)
        await bridge.runtime.drain()
        messages = bridge.memory.recent_messages(
            bridge.persona_context.persona_id,
            event.get_group_id(),
            10,
        )
        snapshot = bridge.runtime.snapshots(
            bridge.persona_context.persona_id
        )[event.get_group_id()]
        await bridge.close()
        return messages, snapshot

    messages, snapshot = asyncio.run(scenario())
    assert [message.text for message in messages] == ["暂停期间也要记住"]
    assert snapshot["dispatch_enabled"] is False
    assert "last_outcome" not in snapshot


def test_bridge_registers_policy_scoped_vision_capability(tmp_path):
    bridge = _bridge(tmp_path, vision_enabled=True)
    workflow = bridge._workflow_for("g1", bridge.persona_context)
    message = ChatMessage(
        message_id="m1",
        group_id="g1",
        sender_id="u1",
        sender_name="群友甲",
        text="帮我看看这张图",
        timestamp=100,
        image_urls=("https://example.test/image.png",),
    )

    resolution = workflow.task_response_resolver(
        InteractionScene.TASK_REQUEST,
        message,
    )

    assert workflow.capabilities is not None
    assert resolution.status is TaskResolutionStatus.SUPPORTED
    assert resolution.capability_name == "vision"


def test_bridge_injects_governed_capabilities(tmp_path):
    bridge = _bridge(tmp_path, vision_enabled=True)
    workflow = bridge._workflow_for("g1", bridge.persona_context)

    assert workflow.capabilities is not None
    assert workflow.capability_governor is not None
    manifest_names = tuple(item.name for item in workflow.capabilities.manifests())
    assert manifest_names == ("vision",)


def test_bridge_stores_provider_runtime_per_group(tmp_path):
    bridge = _bridge(tmp_path, vision_enabled=True)
    workflow = bridge._workflow_for("g1", bridge.persona_context)
    runtime = bridge._capability_runtimes["g1"]

    assert workflow.capabilities is runtime.registry
    assert workflow.capability_governor.registry is runtime.registry
    assert runtime.closed is False


def test_bridge_close_releases_provider_runtimes(tmp_path):
    async def scenario():
        bridge = _bridge(tmp_path, vision_enabled=True)
        bridge._workflow_for("g1", bridge.persona_context)
        runtime = bridge._capability_runtimes["g1"]

        await bridge.close()
        return runtime, bridge._capability_runtimes

    runtime, runtimes = asyncio.run(scenario())

    assert runtime.closed is True
    assert runtimes == {}


def test_bridge_does_not_route_text_only_tasks_to_arbitrary_capabilities(tmp_path):
    bridge = _bridge(tmp_path, vision_enabled=True)
    workflow = bridge._workflow_for("g1", bridge.persona_context)
    message = ChatMessage(
        message_id="m1",
        group_id="g1",
        sender_id="u1",
        sender_name="群友甲",
        text="帮我执行这个任务",
        timestamp=100,
    )

    resolution = workflow.task_response_resolver(
        InteractionScene.TASK_REQUEST,
        message,
    )

    assert resolution.status is TaskResolutionStatus.UNSUPPORTED
