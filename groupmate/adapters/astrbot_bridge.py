"""AstrBot composition boundary for Social Runtime v2."""

from __future__ import annotations

from pathlib import Path

from ..settings import SocialRuntimeSettings
from ..social_runtime.contracts import RuntimeMode
from ..social_runtime.manager import SocialRuntimeManager
from ..social_runtime.ownership import ExternalTriggerPolicy
from .astrbot_events import AstrBotEventTranslator


class AstrBotSocialRuntimeBridge:
    def __init__(
        self,
        context: object,
        settings: SocialRuntimeSettings,
        data_dir: Path,
        *,
        shadow_reviews: object | None = None,
    ) -> None:
        self.context = context
        self.settings = settings
        self.data_dir = Path(data_dir)
        self.translator = AstrBotEventTranslator(
            settings.persona_id,
            bot_qq=settings.bot_qq,
            external_trigger_policy=ExternalTriggerPolicy.from_entries(
                command_prefixes=settings.external_command_prefixes,
                link_domains=settings.external_link_domains,
            ),
        )
        self._manager: SocialRuntimeManager | None = None
        self.shadow_reviews = shadow_reviews
        self.shadow_review_error: str | None = None
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
        if mode in {RuntimeMode.SHADOW, RuntimeMode.SOCIAL_RUNTIME}:
            self._manager = SocialRuntimeManager(
                database_path=self.data_dir / self.settings.database_name,
                persona_id=self.settings.persona_id,
                mode=mode,
                enabled_groups=self.settings.enabled_groups,
                social_runtime_test_groups=self.settings.social_runtime_test_groups,
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
            evaluations = await self._manager.drain()
            if self.shadow_reviews is not None:
                capture = getattr(self.shadow_reviews, "capture_runtime", None)
                if not callable(capture):
                    self.shadow_review_error = (
                        "shadow review recorder contract is invalid"
                    )
                    return result
                for evaluation in evaluations:
                    if not evaluation.accepted:
                        continue
                    try:
                        capture(evaluation)
                        self.shadow_review_error = None
                    except Exception as exc:
                        self.shadow_review_error = (
                            f"{type(exc).__name__}: {exc}"
                        )
        return result

    async def close(self) -> None:
        if self._manager is not None:
            await self._manager.close()
            self._manager = None
        self._started = False
