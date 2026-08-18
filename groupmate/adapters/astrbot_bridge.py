"""Fail-closed AstrBot bridge used until the Phase A runtime is assembled."""

from __future__ import annotations

from pathlib import Path

from ..settings import SocialRuntimeSettings


class RuntimeNotReadyError(RuntimeError):
    """Raised when a host tries to start an incomplete runtime."""


class AstrBotSocialRuntimeBridge:
    def __init__(self, context: object, settings: SocialRuntimeSettings, data_dir: Path) -> None:
        self.context = context
        self.settings = settings
        self.data_dir = Path(data_dir)

    async def start(self) -> None:
        raise RuntimeNotReadyError("Social Runtime v2 foundation incomplete")

    async def handle_event(self, event: object) -> None:
        del event
        raise RuntimeNotReadyError("Social Runtime v2 foundation incomplete")

    async def close(self) -> None:
        return None
