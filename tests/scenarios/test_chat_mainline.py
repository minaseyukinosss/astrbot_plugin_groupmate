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


def _event(message_id, text, *, mention_bot=False):
    message = []
    if mention_bot:
        message.append({"type": "at", "data": {"qq": "bot-1"}})
    message.append({"type": "text", "data": {"text": text}})

    class MessageObject:
        self_id = "bot-1"
        raw_message = {
            "message_id": message_id,
            "group_id": "885617919",
            "user_id": "u1",
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


def test_live_chat_replies_once_while_external_command_stays_out(tmp_path):
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
        await bridge.handle_event(_event("external", "bq 开心"))
        calls_after_external = len(context.model_calls)
        await bridge.handle_event(
            _event("m1", "这个报错怎么看", mention_bot=True)
        )
        parts = bridge.manager.outbox.receipted_parts()
        state = await bridge.manager.group_snapshot("885617919")
        event_ids = bridge.manager.event_store.event_ids()
        reply_error = bridge.reply_error
        await bridge.close()
        return context, calls_after_external, parts, state, event_ids, reply_error

    context, calls_after_external, parts, state, event_ids, reply_error = asyncio.run(
        scenario()
    )

    assert calls_after_external == 0
    assert len(context.client.calls) == 1
    assert len(parts) == 1 and parts[0].status is OutboxStatus.SENT
    assert any(value.startswith("delivery-feedback:") for value in event_ids)
    assert state.recent_presence.last_bot_event_at == 100
    assert reply_error is None
