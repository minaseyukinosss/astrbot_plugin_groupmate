"""AstrBot composition root for Groupmate Social Runtime v2."""

from __future__ import annotations

from pathlib import Path

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

from .groupmate.adapters.astrbot_bridge import AstrBotSocialRuntimeBridge
from .groupmate.adapters.affection_card import AFFECTION_CARD_TEMPLATE
from .groupmate.adapters.web_api import (
    AstrBotControlPlaneRoutes,
    ControlPlaneWebAPI,
)
from .groupmate.settings import SOCIAL_RUNTIME_DATABASE_NAME, SocialRuntimeSettings
from .groupmate.social_runtime.control.commands import CommandService
from .groupmate.social_runtime.control.projections import ProjectionConsumer
from .groupmate.social_runtime.control.queries import ProjectionQueries
from .groupmate.social_runtime.control.stream import ProjectionStream
from .eval.shadow import ShadowReviewRepository


class GroupmatePlugin(Star):
    def __init__(self, context: Context, config) -> None:
        super().__init__(context)
        settings = SocialRuntimeSettings.from_mapping(config)
        data_dir = Path(get_astrbot_plugin_data_path()) / "astrbot_plugin_groupmate"
        self.settings = settings
        self.data_dir = data_dir
        self.bridge = AstrBotSocialRuntimeBridge(context, settings, data_dir)
        self._control_api: ControlPlaneWebAPI | None = None
        self._projection_consumers: tuple[ProjectionConsumer, ...] = ()
        self._control_routes: AstrBotControlPlaneRoutes | None = None
        self._shadow_reviews: ShadowReviewRepository | None = None
        if settings.enabled_groups:
            self._control_routes = AstrBotControlPlaneRoutes(
                context,
                api_factory=self._require_control_api,
            )
            self._control_routes.register()

    async def initialize(self) -> None:
        await self.bridge.start()
        if self.settings.enabled_groups:
            path = self.data_dir / SOCIAL_RUNTIME_DATABASE_NAME
            self._shadow_reviews = ShadowReviewRepository(path)
            self.bridge.shadow_reviews = self._shadow_reviews
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
                    admin_ids=self.settings.control_admin_ids or (username,),
                    shadow_repository=self._shadow_reviews,
                ),
                event_publisher=self._publish_control_event,
                persona_id=self.settings.persona_id,
                group_ids=self.settings.enabled_groups,
                admin_ids=self.settings.control_admin_ids,
                participants=self.bridge.trace_repository.participants,
                message_media=self.bridge.trace_repository.media,
                runtime_mode=self.settings.runtime_mode,
                runtime_ready=(
                    self.settings.runtime_mode != "OFF"
                    and bool(self.settings.generation_provider)
                    and bool(self.settings.cognition_api_key)
                    and bool(self.settings.cognition_api_base)
                    and bool(self.settings.cognition_model)
                ),
                runtime_blockers=tuple(
                    reason
                    for blocked, reason in (
                        (self.settings.runtime_mode == "OFF", "运行模式为 OFF"),
                        (
                            not self.settings.generation_provider,
                            "未选择最终回复模型",
                        ),
                        (
                            not self.settings.cognition_api_key,
                            "未配置认知模型 API Key",
                        ),
                        (
                            not self.settings.cognition_api_base,
                            "认知模型 API 地址无效",
                        ),
                        (
                            not self.settings.cognition_model,
                            "未配置认知模型名称",
                        ),
                    )
                    if blocked
                ),
            )
            self._refresh_projections()

    # Run after ordinary command handlers so a correctly implemented external
    # plugin can stop propagation before Groupmate sees its functional request.
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=1000)
    async def observe_group_message_arrival(self, event: AstrMessageEvent):
        await self.bridge.observe_event(event)

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=-100)
    async def observe_group_message(self, event: AstrMessageEvent):
        query = await self.bridge.prepare_affection_query(event)
        if query is not None:
            event.stop_event()
            try:
                rendered_pages = []
                for page in query.pages:
                    url = await self.html_render(
                        AFFECTION_CARD_TEMPLATE,
                        page.context,
                        options={
                            "type": "png",
                            "full_page": False,
                            "clip": {
                                "x": 0,
                                "y": 0,
                                "width": page.context["render_width"],
                                "height": page.context["render_height"],
                            },
                            "animations": "disabled",
                            "caret": "hide",
                        },
                    )
                    if not url:
                        raise RuntimeError("affection card renderer returned no image")
                    rendered_pages.append(url)
                for url in rendered_pages:
                    yield event.image_result(url)
            except Exception:
                yield event.plain_result(query.text_fallback)
            self._refresh_projections()
            return
        await self.bridge.handle_event(event)
        self._refresh_projections()

    async def _publish_control_event(self, event) -> None:
        try:
            manager = self.bridge.manager
        except RuntimeError:
            manager = None
        if manager is not None:
            await manager.ingest(event)
            await manager.drain()
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
        self._shadow_reviews = None
