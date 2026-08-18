"""AstrBot composition root for Groupmate Social Runtime v2."""

from __future__ import annotations

from pathlib import Path

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

from .groupmate.adapters.astrbot_bridge import AstrBotSocialRuntimeBridge
from .groupmate.settings import SocialRuntimeSettings


class GroupmatePlugin(Star):
    def __init__(self, context: Context, config) -> None:
        super().__init__(context)
        settings = SocialRuntimeSettings.from_mapping(config)
        data_dir = Path.cwd() / "data" / "plugin_data" / "astrbot_plugin_groupmate"
        self.bridge = AstrBotSocialRuntimeBridge(context, settings, data_dir)

    async def initialize(self) -> None:
        await self.bridge.start()

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def observe_group_message(self, event: AstrMessageEvent):
        await self.bridge.handle_event(event)

    async def terminate(self) -> None:
        await self.bridge.close()
