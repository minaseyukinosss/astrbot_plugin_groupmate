"""AstrBot composition root for Groupmate Social Runtime v2."""

from __future__ import annotations

from pathlib import Path

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

from .groupmate.adapters.astrbot_bridge import AstrBotSocialRuntimeBridge
from .groupmate.adapters.web_api import (
    AstrBotControlPlaneRoutes,
    ControlPlaneWebAPI,
)
from .groupmate.settings import SocialRuntimeSettings
from .groupmate.social_runtime.control.commands import CommandService
from .groupmate.social_runtime.control.projections import ProjectionConsumer
from .groupmate.social_runtime.control.queries import ProjectionQueries
from .groupmate.social_runtime.control.stream import ProjectionStream


class GroupmatePlugin(Star):
    def __init__(self, context: Context, config) -> None:
        super().__init__(context)
        settings = SocialRuntimeSettings.from_mapping(config)
        data_dir = Path.cwd() / "data" / "plugin_data" / "astrbot_plugin_groupmate"
        self.settings = settings
        self.data_dir = data_dir
        self.bridge = AstrBotSocialRuntimeBridge(context, settings, data_dir)
        self._control_api: ControlPlaneWebAPI | None = None
        self._projection_consumers: tuple[ProjectionConsumer, ...] = ()
        self._control_routes: AstrBotControlPlaneRoutes | None = None
        if settings.runtime_mode != "OFF":
            self._control_routes = AstrBotControlPlaneRoutes(
                context,
                api_factory=self._require_control_api,
            )
            self._control_routes.register()

    async def initialize(self) -> None:
        await self.bridge.start()
        if self.settings.runtime_mode != "OFF":
            path = self.data_dir / self.settings.database_name
            self._projection_consumers = tuple(
                ProjectionConsumer(path, name)
                for name in ProjectionConsumer.PROJECTION_NAMES
            )
            self._control_api = ControlPlaneWebAPI(
                queries=ProjectionQueries(path),
                stream=ProjectionStream(path),
                command_service_for=lambda username: CommandService(
                    path,
                    persona_id=self.settings.persona_id,
                    group_ids=self.settings.enabled_groups,
                    admin_ids=self.settings.control_admin_ids,
                ),
                event_publisher=self._publish_control_event,
                persona_id=self.settings.persona_id,
                group_ids=self.settings.enabled_groups,
                admin_ids=self.settings.control_admin_ids,
            )
            self._refresh_projections()

    # Run after ordinary command handlers so a correctly implemented external
    # plugin can stop propagation before Groupmate sees its functional request.
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=-100)
    async def observe_group_message(self, event: AstrMessageEvent):
        await self.bridge.handle_event(event)
        self._refresh_projections()

    async def _publish_control_event(self, event) -> None:
        await self.bridge.manager.ingest(event)
        await self.bridge.manager.drain()
        self._refresh_projections()

    def _refresh_projections(self) -> None:
        if self._control_api is None:
            return
        try:
            for consumer in self._projection_consumers:
                previous = -1
                while True:
                    progress = consumer.consume(256)
                    if progress.cursor == previous:
                        break
                    previous = progress.cursor
            self._control_api.clear_degraded("projection")
        except Exception as exc:
            self._control_api.mark_degraded("projection", str(exc))

    def _require_control_api(self) -> ControlPlaneWebAPI:
        if self._control_api is None:
            raise RuntimeError("Social Runtime control plane is not initialized")
        return self._control_api

    async def terminate(self) -> None:
        await self.bridge.close()
        self._control_api = None
        self._projection_consumers = ()
