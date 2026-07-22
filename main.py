"""AstrBot entry point for the Groupmate plugin."""

from __future__ import annotations

import json
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

from .groupmate.astrbot_adapter import AstrBotBridge
from .groupmate.config import PluginSettings


class GroupmatePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.context = context
        self.config = PluginSettings.from_mapping(config)
        data_dir = Path.cwd() / "data" / "plugin_data" / "astrbot_plugin_groupmate"
        self.bridge = AstrBotBridge(context, self.config, data_dir)
        logger.info("Groupmate initialized")

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def observe_group_message(self, event: AstrMessageEvent):
        """旁路观察 QQ 群消息，不抢占已有指令。"""
        if self.bridge.should_take_native_wake(event):
            event.call_llm = False
        await self.bridge.handle_event(event)

    @filter.on_llm_request()
    async def enrich_native_request(self, event: AstrMessageEvent, req):
        """为 AstrBot 原生唤醒请求补充有限群聊上下文。"""
        if event.is_private_chat():
            return
        await self.bridge.enrich_request(event, req)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("groupmate_status")
    async def groupmate_status(self, event: AstrMessageEvent):
        """查看 Groupmate 运行状态。"""
        yield event.plain_result(
            json.dumps(self.bridge.status(), ensure_ascii=False, sort_keys=True)
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("groupmate_pause")
    async def groupmate_pause(self, event: AstrMessageEvent):
        """暂停群聊观察和自主回复。"""
        self.bridge.paused = True
        yield event.plain_result("群聊伙伴先安静一下。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("groupmate_resume")
    async def groupmate_resume(self, event: AstrMessageEvent):
        """恢复群聊观察和自主回复。"""
        self.bridge.paused = False
        yield event.plain_result("好，继续看群里。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("groupmate_reset")
    async def groupmate_reset(self, event: AstrMessageEvent):
        """清理当前群的短期上下文。"""
        group_id = str(event.get_group_id())
        actor = await self.bridge.runtime.actor_for(group_id)
        actor.window = actor.window.__class__(
            group_id, max_messages=self.bridge._policy_for(group_id).history_limit
        )
        yield event.plain_result("这群刚才的上下文清掉了。")

    async def terminate(self):
        await self.bridge.close()
