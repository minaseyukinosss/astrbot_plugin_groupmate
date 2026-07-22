"""AstrBot @ wake must suppress default agent via call_llm=True."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from groupmate.astrbot_adapter import AstrBotBridge
from groupmate.config import PluginSettings


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
    return AstrBotBridge(
        context=SimpleNamespace(),
        settings=PluginSettings.from_mapping(settings),
        data_dir=tmp_path,
    )


def test_should_take_native_wake_on_at(tmp_path):
    bridge = _bridge(tmp_path)
    event = _FakeEvent(at_bot=True)
    assert bridge.should_take_native_wake(event) is True


def test_should_not_take_when_native_wake_disabled(tmp_path):
    bridge = _bridge(tmp_path, handle_native_wake=False)
    event = _FakeEvent(at_bot=True)
    assert bridge.should_take_native_wake(event) is False


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
    assert bridge.should_take_native_wake(event) is True
    assert bridge.should_defer_native_wake_to_astrbot(event) is True
    # Defer path must leave call_llm False so ProcessStage can run Agent+tools.
    assert event.call_llm is False
    assert event.is_at_or_wake_command and not event.call_llm


def test_casual_at_does_not_defer(tmp_path):
    bridge = _bridge(tmp_path)
    event = _FakeEvent(text="你今天怎样")
    assert bridge.should_take_native_wake(event) is True
    assert bridge.should_defer_native_wake_to_astrbot(event) is False
