from __future__ import annotations

import asyncio
import json

from groupmate.adapters.astrbot_bridge import AstrBotSocialRuntimeBridge
from groupmate.settings import SocialRuntimeSettings
from groupmate.social_runtime.actions.contracts import OutboxStatus


class _Response:
    def __init__(self, text):
        self.completion_text = text


class _OneBotClient:
    def __init__(self):
        self.calls = []

    async def send_group_msg(self, **kwargs):
        self.calls.append(kwargs)
        return {"message_id": f"sent-{len(self.calls)}"}


class _Platform:
    def __init__(self, client):
        self.client = client

    def get_client(self):
        return self.client


class _Context:
    def __init__(self):
        self.client = _OneBotClient()
        self.model_calls = []

    async def llm_generate(self, **kwargs):
        self.model_calls.append(kwargs)
        if "结构化群聊观察器" not in kwargs["system_prompt"]:
            return _Response("可以把完整报错和相关代码贴一下，我帮你一起看。")
        request = json.loads(kwargs["prompt"])["input"]
        frame = request["frame"]
        worker = request["worker"]
        observations = []
        if worker == "direct_interaction":
            observations.append(
                {
                    "kind": "help_request",
                    "proposition": {
                        "subject_id": frame["candidate_audiences"][0],
                        "topic_id": frame["focus_topic_ids"][0],
                    },
                    "confidence": 0.95,
                    "evidence_event_ids": [frame["focus_event_ids"][0]],
                    "scene_version": frame["scene_version"],
                    "expires_at": 130,
                }
            )
        return _Response(json.dumps({"observations": observations}))

    def get_platform_inst(self, platform_id):
        assert platform_id == "onebot-main"
        return _Platform(self.client)


class _FailingReplyContext(_Context):
    async def llm_generate(self, **kwargs):
        self.model_calls.append(kwargs)
        raise RuntimeError("reply provider unavailable")


def _event(message_id, text, *, mention_bot=False, actor_id="u1"):
    message = []
    if mention_bot:
        message.append({"type": "at", "data": {"qq": "bot-1"}})
    message.append({"type": "text", "data": {"text": text}})

    class MessageObject:
        self_id = "bot-1"
        raw_message = {
            "message_id": message_id,
            "group_id": "885617919",
            "user_id": actor_id,
            "time": 100,
            "message": message,
        }

    class Event:
        message_obj = MessageObject()
        message_str = text
        unified_msg_origin = "aiocqhttp:GroupMessage:885617919"

        @staticmethod
        def get_platform_id():
            return "onebot-main"

    return Event()


async def _run_alias_case(tmp_path, text):
    context = _Context()
    settings = SocialRuntimeSettings.from_mapping(
        {
            "enabled_groups": ["885617919"],
            "runtime_mode": "SOCIAL_RUNTIME",
            "generation_provider": "provider:text",
            "persona_name": "爱弥斯",
            "persona_aliases": ["小爱"],
            "external_command_prefixes": ["bq=astrbot.meme"],
        }
    )
    bridge = AstrBotSocialRuntimeBridge(
        context, settings, tmp_path, clock=lambda: 100
    )
    await bridge.start()
    await bridge.handle_event(_event("alias-case", text))
    trace = bridge.trace_repository.query(
        persona_id=settings.persona_id,
        group_id="885617919",
    )["items"][0]["summary"]
    await bridge.close()
    return context, trace


def test_alias_prefixed_external_command_stays_owned_by_astrbot(tmp_path):
    context, trace = asyncio.run(_run_alias_case(tmp_path, "小爱 bq 开心"))

    assert context.model_calls == []
    assert trace["route"]["owner"] == "EXTERNAL_PLUGIN"
    assert trace["route"]["reason"] == "匹配已配置的外部触发规则"


def test_alias_prefixed_social_call_enters_direct_lane(tmp_path):
    context, trace = asyncio.run(_run_alias_case(tmp_path, "小爱说话"))

    assert trace["decision"]["participation_lane"] == "DIRECT_FAST"
    cognition_calls = [
        call
        for call in context.model_calls
        if "结构化群聊观察器" in call["system_prompt"]
    ]
    assert cognition_calls == []
    assert len(context.client.calls) == 1


def test_live_chat_replies_and_continues_without_structured_cognition(tmp_path):
    async def scenario():
        context = _Context()
        settings = SocialRuntimeSettings.from_mapping(
            {
                "enabled_groups": ["885617919"],
                "runtime_mode": "SOCIAL_RUNTIME",
                "generation_provider": "provider:text",
                "external_command_prefixes": ["bq=astrbot.meme"],
            }
        )
        bridge = AstrBotSocialRuntimeBridge(
            context, settings, tmp_path, clock=lambda: 100
        )
        await bridge.start()
        await bridge.handle_event(_event("external", "bq 开心", actor_id="u2"))
        calls_after_external = len(context.model_calls)
        await bridge.handle_event(
            _event("m1", "这个报错怎么看", mention_bot=True)
        )
        await bridge.handle_event(_event("m2", "然后呢"))
        parts = bridge.manager.outbox.receipted_parts()
        state = await bridge.manager.group_snapshot("885617919")
        event_ids = bridge.manager.event_store.event_ids()
        traces = bridge.trace_repository.query(
            persona_id=settings.persona_id,
            group_id="885617919",
        )["items"]
        reply_error = bridge.reply_error
        await bridge.close()
        return (
            context,
            calls_after_external,
            parts,
            state,
            event_ids,
            traces,
            reply_error,
        )

    (
        context,
        calls_after_external,
        parts,
        state,
        event_ids,
        traces,
        reply_error,
    ) = asyncio.run(scenario())

    assert calls_after_external == 0
    assert len(context.client.calls) == 2
    assert len(parts) == 2
    assert all(part.status is OutboxStatus.SENT for part in parts)
    assert state.conversation_lease is not None
    assert state.conversation_lease.target_id == "u1"
    assert state.conversation_lease.topic_id == "m1"
    assert state.conversation_lease.remaining_turns == 4
    assert all(
        "结构化群聊观察器" not in call["system_prompt"]
        for call in context.model_calls
    )
    direct_decision = next(
        item["summary"]["decision"]
        for item in traces
        if "这个报错怎么看" in item["summary"]["message"]["summary"]
    )
    continuation_decision = next(
        item["summary"]["decision"]
        for item in traces
        if "然后呢" in item["summary"]["message"]["summary"]
    )
    assert direct_decision["participation_lane"] == "DIRECT_FAST"
    assert continuation_decision["participation_lane"] == "CONTINUATION"
    assert continuation_decision["would_reply"] is True
    assert any(value.startswith("delivery-feedback:") for value in event_ids)
    assert state.recent_presence.last_bot_event_at == 100
    assert reply_error is None


def test_shadow_preview_never_opens_a_dialogue_lease(tmp_path):
    async def run(context, directory):
        settings = SocialRuntimeSettings.from_mapping(
            {
                "enabled_groups": ["885617919"],
                "runtime_mode": "SHADOW",
                "generation_provider": "provider:text",
            }
        )
        bridge = AstrBotSocialRuntimeBridge(
            context, settings, directory, clock=lambda: 100
        )
        await bridge.start()
        await bridge.handle_event(_event("m1", "在吗", mention_bot=True))
        state = await bridge.manager.group_snapshot("885617919")
        await bridge.close()
        return state

    ready_context = _Context()
    failed_context = _FailingReplyContext()
    ready_state = asyncio.run(run(ready_context, tmp_path / "ready"))
    failed_state = asyncio.run(run(failed_context, tmp_path / "failed"))

    assert ready_state.conversation_lease is None
    assert failed_state.conversation_lease is None
    assert ready_context.client.calls == failed_context.client.calls == []
