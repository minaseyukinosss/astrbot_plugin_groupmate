from __future__ import annotations

import asyncio

from groupmate.adapters.astrbot_bridge import AstrBotSocialRuntimeBridge
from groupmate.settings import SOCIAL_RUNTIME_DATABASE_NAME, SocialRuntimeSettings
from groupmate.social_runtime.profile.speech_style import MemberSpeechStyle
from groupmate.social_runtime.profile.style_repository import MemberStyleRepository
from tests.scenarios.test_chat_mainline import (
    _Context as _ChatContext,
    _MatrixCognition,
)


class _Context:
    def __init__(self):
        self.model_calls = []

    async def llm_generate(self, **kwargs):
        self.model_calls.append(kwargs)
        raise AssertionError("state transition must not enter a model")


class _Response:
    def __init__(self, text):
        self.completion_text = text


class _ConfirmationContext(_Context):
    async def llm_generate(self, **kwargs):
        self.model_calls.append(kwargs)
        assert "模仿确认" in kwargs["system_prompt"]
        return _Response(
            "行，我学阿甲说话到1970年1月2日20:00。"
            "只是说话方式变了，我还是爱弥斯。"
        )


class _FailingConfirmationContext(_Context):
    async def llm_generate(self, **kwargs):
        self.model_calls.append(kwargs)
        raise RuntimeError("provider down")


class _ImitatingChatContext(_ChatContext):
    async def llm_generate(self, **kwargs):
        if "模仿确认" in kwargs["system_prompt"]:
            self.model_calls.append(kwargs)
            return _Response(
                "行，我学阿甲说话到1970年1月2日20:00。"
                "只是说话方式变了，我还是爱弥斯。"
            )
        return await super().llm_generate(**kwargs)


class _Cognition:
    model = "test"

    async def close(self):
        return None


def _event(message_id, text, *, actor="admin", target="u1", group="g1"):
    class MessageObject:
        self_id = "bot"
        raw_message = {
            "message_id": message_id,
            "group_id": group,
            "user_id": actor,
            "time": 100,
            "message": [
                {"type": "at", "data": {"qq": "bot"}},
                {"type": "at", "data": {"qq": target}},
                {"type": "text", "data": {"text": text}},
            ],
        }

    class Event:
        message_obj = MessageObject()
        message_str = text
        unified_msg_origin = f"aiocqhttp:GroupMessage:{group}"

        @staticmethod
        def get_platform_id():
            return "onebot-main"

    return Event()


def _ready_style(repository, *, group="g1", member="u1"):
    repository.set_enabled(
        group, member, enabled=True, updated_by="admin", now=10
    )
    repository.publish(
        MemberSpeechStyle(
            group_id=group,
            member_id=member,
            version=1,
            status="READY",
            opening_patterns=("先表态",),
            progression_patterns=("再补理由",),
            closing_patterns=("自然收口",),
            length_rhythm="短句",
            directness="直接",
            disagreement_style="指出问题",
            play_style="现场调侃",
            care_style="给具体动作",
            addressing_style="需要时称呼",
            particles_punctuation="少量语气词",
            stable_traits=("结论在前",),
            occasional_traits=("偶尔省略主语",),
            evidence_event_ids=("e1", "e2"),
            eligible_message_count=40,
            active_day_count=5,
            scene_types=("answer", "banter", "care"),
            generated_at=20,
        )
    )


def _bridge(tmp_path, *, context=None):
    settings = SocialRuntimeSettings.from_mapping(
        {
            "enabled_groups": ["g1", "g2"],
            "runtime_mode": "SOCIAL_RUNTIME",
            "generation_provider": "provider:text",
            "profile_enabled": False,
            "control_admin_ids": ["admin"],
            "persona_name": "爱弥斯",
        }
    )
    context = context or _Context()
    bridge = AstrBotSocialRuntimeBridge(
        context,
        settings,
        tmp_path,
        clock=lambda: 100,
        cognition_client_factory=lambda _: _Cognition(),
    )
    bridge.trace_repository.participants.remember_actor(
        persona_id=settings.persona_id,
        group_id="g1",
        actor_id="u1",
        display_name="阿甲",
        updated_at=90,
    )
    repository = MemberStyleRepository(tmp_path / SOCIAL_RUNTIME_DATABASE_NAME)
    _ready_style(repository)
    return bridge, context, repository


def test_bridge_commits_imitation_transition_without_entering_models(tmp_path):
    async def scenario():
        bridge, context, repository = _bridge(tmp_path)
        await bridge.start()
        try:
            result = await bridge.prepare_imitation_transition(
                _event("m1", "开始模仿到明晚八点")
            )
            return result, context.model_calls, repository
        finally:
            await bridge.close()

    result, model_calls, repository = asyncio.run(scenario())

    assert result.handled is True
    assert result.transition.operation == "STARTED"
    assert result.transition.persona_name == "爱弥斯"
    assert result.transition.session.target_display_name == "阿甲"
    assert repository.active_session("g1", now=101) is not None
    assert repository.active_session("g2", now=101) is None
    assert model_calls == []


def test_bridge_returns_current_persona_error_for_unready_target(tmp_path):
    async def scenario():
        bridge, context, _ = _bridge(tmp_path)
        await bridge.start()
        try:
            # 关闭后风格立即不可选，但请求仍由确定性通道接管。
            bridge.member_style_repository.set_enabled(
                "g1", "u1", enabled=False, updated_by="admin", now=99
            )
            result = await bridge.prepare_imitation_transition(
                _event("m2", "开始模仿到明晚八点")
            )
            return result, context.model_calls
        finally:
            await bridge.close()

    result, model_calls = asyncio.run(scenario())

    assert result.handled is True
    assert result.transition is None
    assert "我现在还学不像阿甲" in result.error_text
    assert model_calls == []


def test_bridge_ignores_ordinary_mentioned_smalltalk(tmp_path):
    async def scenario():
        bridge, _, _ = _bridge(tmp_path)
        await bridge.start()
        try:
            return await bridge.prepare_imitation_transition(
                _event("m3", "今天过得怎么样")
            )
        finally:
            await bridge.close()

    assert asyncio.run(scenario()) is None


def test_activation_confirmation_is_first_style_audition(tmp_path):
    async def scenario():
        bridge, context, _ = _bridge(
            tmp_path, context=_ConfirmationContext()
        )
        await bridge.start()
        try:
            result = await bridge.prepare_imitation_command(
                _event("m4", "开始模仿到明晚八点")
            )
            return result, context.model_calls
        finally:
            await bridge.close()

    result, model_calls = asyncio.run(scenario())

    assert result.handled is True
    assert "我还是爱弥斯" in result.response_text
    assert len(model_calls) == 1
    assert "结论在前" in model_calls[0]["system_prompt"]
    assert "正在连接" not in result.response_text
    assert "上线" not in result.response_text


def test_activation_model_failure_uses_plain_aemeath_confirmation(tmp_path):
    async def scenario():
        bridge, _, repository = _bridge(
            tmp_path, context=_FailingConfirmationContext()
        )
        await bridge.start()
        try:
            result = await bridge.prepare_imitation_command(
                _event("m5", "开始模仿到明晚八点")
            )
            return result, repository.active_session("g1", now=101)
        finally:
            await bridge.close()

    result, active = asyncio.run(scenario())

    assert active is not None
    assert "阿甲" in result.response_text
    assert "1970年1月2日20:00" in result.response_text
    assert "我还是爱弥斯" in result.response_text
    assert "连接" not in result.response_text


def test_active_overlay_is_resolved_from_fixed_version_in_current_group(tmp_path):
    async def scenario():
        bridge, _, _ = _bridge(tmp_path, context=_ConfirmationContext())
        await bridge.start()
        try:
            await bridge.prepare_imitation_command(
                _event("m6", "开始模仿到明晚八点")
            )
            return (
                bridge._member_style_overlay("g1", now=101),
                bridge._member_style_overlay("g2", now=101),
            )
        finally:
            await bridge.close()

    current, other = asyncio.run(scenario())

    assert current.target_display_name == "阿甲"
    assert current.style_version == 1
    assert other is None


def test_active_style_reaches_generated_smalltalk_prompt(tmp_path):
    async def scenario():
        context = _ImitatingChatContext()
        settings = SocialRuntimeSettings.from_mapping(
            {
                "enabled_groups": ["g1"],
                "runtime_mode": "SOCIAL_RUNTIME",
                "generation_provider": "provider:text",
                "profile_enabled": False,
                "control_admin_ids": ["admin"],
                "persona_name": "爱弥斯",
            }
        )
        bridge = AstrBotSocialRuntimeBridge(
            context,
            settings,
            tmp_path,
            clock=lambda: 100,
            cognition_client_factory=lambda _: _MatrixCognition(),
        )
        bridge.trace_repository.participants.remember_actor(
            persona_id=settings.persona_id,
            group_id="g1",
            actor_id="u1",
            display_name="阿甲",
            updated_at=90,
        )
        repository = MemberStyleRepository(
            tmp_path / SOCIAL_RUNTIME_DATABASE_NAME
        )
        _ready_style(repository)
        await bridge.start()
        try:
            await bridge.prepare_imitation_command(
                _event("m7", "开始模仿到明晚八点")
            )
            await bridge.handle_event(
                _event("m8", "报错怎么看", actor="u2", target="u1")
            )
            return context.model_calls
        finally:
            await bridge.close()

    calls = asyncio.run(scenario())
    reply_prompts = [
        call["system_prompt"]
        for call in calls
        if "根据已批准的社交动作生成回复" in call["system_prompt"]
    ]

    assert len(reply_prompts) == 1
    assert "本轮临时参考 阿甲 的说话方式" in reply_prompts[0]
    assert "结论在前" in reply_prompts[0]
