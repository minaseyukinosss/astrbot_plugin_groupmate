"""AstrBot entry point for the Groupmate plugin."""

from __future__ import annotations

import json
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

from .groupmate.host import AstrBotBridge
from .groupmate.config import PluginSettings
from .groupmate.host.web_api import GroupmateWebAPI


class GroupmatePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.context = context
        self.config = PluginSettings.from_mapping(config)
        data_dir = Path.cwd() / "data" / "plugin_data" / "astrbot_plugin_groupmate"
        self.bridge = AstrBotBridge(context, self.config, data_dir)
        self.web_api = GroupmateWebAPI(self.bridge)
        self.web_api.register(context)
        logger.info("Groupmate initialized")

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def observe_group_message(self, event: AstrMessageEvent):
        """旁路观察 QQ 群消息，不抢占已有指令。"""
        if self.bridge.should_take_native_wake(event):
            if self.bridge.should_defer_native_wake_to_astrbot(event):
                # 需要联网/外部事实：只观察，不抑制 AstrBot 默认 Agent（保留搜索工具）。
                await self.bridge.observe_only(event)
                return
            # AstrBot ProcessStage 默认 Agent 条件：
            #   is_at_or_wake_command and not event.call_llm and not _has_send_oper
            # call_llm 的语义是「禁止默认 LLM」（见 AstrMessageEvent），默认 False。
            # Groupmate 异步投递，返回时尚未 event.send，不能靠 _has_send_oper；
            # 必须显式置 True，否则 @ 会同时触发 AstrBot 原生回复与 Groupmate。
            if hasattr(event, "should_call_llm"):
                event.should_call_llm(True)
            else:
                event.call_llm = True
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
        """暂停决策与发送，继续观察并记录群消息。"""
        self.bridge.paused = True
        yield event.plain_result("先安静一下，我还会看着群消息。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("groupmate_resume")
    async def groupmate_resume(self, event: AstrMessageEvent):
        """恢复群聊决策与发送。"""
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
        sessions = getattr(getattr(actor, "workflow", None), "sessions", None)
        if sessions is not None:
            sessions.reset(group_id)
        actor._clear_continuation()
        yield event.plain_result("这群刚才的上下文清掉了。")

    async def terminate(self):
        await self.bridge.close()
