"""Groupmate WebUI 插件页 API。"""

from __future__ import annotations

from typing import Any

PLUGIN_NAME = "astrbot_plugin_groupmate"


class GroupmateWebAPI:
    def __init__(self, bridge: Any) -> None:
        self.bridge = bridge

    def register(self, context: Any) -> None:
        register = getattr(context, "register_web_api", None)
        if register is None:
            return
        register(
            f"/{PLUGIN_NAME}/status",
            self.status,
            ["GET"],
            "Groupmate runtime status",
        )
        register(
            f"/{PLUGIN_NAME}/runtime",
            self.set_runtime,
            ["POST"],
            "Pause or resume Groupmate",
        )

    async def status(self):
        from astrbot.api.web import json_response

        return json_response(self.bridge.status())

    async def set_runtime(self):
        from astrbot.api.web import error_response, json_response, request

        payload = await request.json(default={})
        if "paused" not in payload or not isinstance(payload.get("paused"), bool):
            return error_response("paused must be a boolean", status_code=400)
        self.bridge.paused = bool(payload["paused"])
        return json_response({"paused": self.bridge.paused})
