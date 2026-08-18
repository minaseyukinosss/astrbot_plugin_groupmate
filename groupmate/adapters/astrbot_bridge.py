"""AstrBot composition boundary for Social Runtime v2."""

from __future__ import annotations

from pathlib import Path

from ..settings import SocialRuntimeSettings
from ..social_runtime.contracts import RuntimeMode
from ..social_runtime.manager import RuntimeModeUnavailable, SocialRuntimeManager
from .astrbot_events import AstrBotEventTranslator


class AstrBotSocialRuntimeBridge:
    def __init__(
        self, context: object, settings: SocialRuntimeSettings, data_dir: Path
    ) -> None:
        self.context = context
        self.settings = settings
        self.data_dir = Path(data_dir)
        self.translator = AstrBotEventTranslator(
            settings.persona_id, bot_qq=settings.bot_qq
        )
        self._manager: SocialRuntimeManager | None = None
        self._started = False

    @property
    def manager(self) -> SocialRuntimeManager:
        if self._manager is None:
            raise RuntimeError("Social Runtime is disabled")
        return self._manager

    async def start(self) -> None:
        if self._started:
            return
        mode = RuntimeMode(self.settings.runtime_mode)
        if mode is RuntimeMode.SOCIAL_RUNTIME:
            raise RuntimeModeUnavailable("current release gate only supports SHADOW")
        if mode is RuntimeMode.SHADOW:
            self._manager = SocialRuntimeManager(
                database_path=self.data_dir / self.settings.database_name,
                persona_id=self.settings.persona_id,
                mode=mode,
                enabled_groups=self.settings.enabled_groups,
            )
            await self._manager.start()
        self._started = True

    async def handle_event(self, event: object):
        if not self._started:
            await self.start()
        if self._manager is None:
            return None
        result = await self._manager.ingest(self.translator.translate(event))
        if result is not None and result.inserted:
            await self._manager.drain()
        return result

    async def close(self) -> None:
        if self._manager is not None:
            await self._manager.close()
            self._manager = None
        self._started = False
