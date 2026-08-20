"""Subprocess fixture that crashes after durable SHADOW capture, before ack."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from eval.shadow import ShadowReviewRepository
from groupmate.adapters.astrbot_bridge import AstrBotSocialRuntimeBridge
from groupmate.settings import SocialRuntimeSettings


GROUP = "fake-group-capacity"


class _CrashAfterDurableCapture(ShadowReviewRepository):
    def capture_runtime(self, evaluation):
        result = super().capture_runtime(evaluation)
        del result
        os._exit(23)


async def _main(data_dir: Path) -> None:
    repository = _CrashAfterDurableCapture(
        data_dir / "groupmate-social-runtime-v2.db"
    )
    bridge = AstrBotSocialRuntimeBridge(
        object(),
        SocialRuntimeSettings.from_mapping(
            {"runtime_mode": "SHADOW", "enabled_groups": [GROUP]}
        ),
        data_dir,
        shadow_reviews=repository,
    )
    await bridge.start()
    await bridge.handle_event(
        {
            "message_id": "abrupt-shadow-capture",
            "group_id": GROUP,
            "user_id": "fake-user",
            "time": 100,
            "message": [
                {"type": "text", "data": {"text": "@你 crash window"}},
                {"type": "at", "data": {"qq": "323537051"}},
            ],
        }
    )
    raise AssertionError("capture boundary did not terminate the process")


if __name__ == "__main__":
    asyncio.run(_main(Path(sys.argv[1])))
