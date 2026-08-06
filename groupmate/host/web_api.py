"""Groupmate WebUI 插件页 API。"""

from __future__ import annotations

from typing import Any

PLUGIN_NAME = "astrbot_plugin_groupmate"


def _request_params(request: Any) -> Any:
    return getattr(request, "query", getattr(request, "args", {}))


def _param(params: Any, name: str, default: Any = None) -> Any:
    if isinstance(params, dict):
        return params.get(name, default)
    getter = getattr(params, "get", None)
    return getter(name, default) if callable(getter) else default


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
        register(
            f"/{PLUGIN_NAME}/decisions",
            self.decisions,
            ["GET"],
            "List recent Groupmate decisions",
        )
        register(
            f"/{PLUGIN_NAME}/decisions/<decision_id>",
            self.decision_detail,
            ["GET"],
            "Decision stage trail",
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

    async def decisions(self):
        from astrbot.api.web import error_response, json_response, request

        params = _request_params(request)
        group_id = str(_param(params, "group_id", "") or "").strip() or None
        outcome = str(_param(params, "outcome", "all") or "all").strip()
        try:
            limit = int(_param(params, "limit", 50) or 50)
        except (TypeError, ValueError):
            return error_response("limit must be an integer", status_code=400)
        try:
            payload = self.bridge.list_decisions(
                group_id=group_id,
                outcome=outcome,
                limit=limit,
            )
        except ValueError as exc:
            return error_response(str(exc), status_code=400)
        return json_response(payload)

    async def decision_detail(self, decision_id: str):
        from astrbot.api.web import error_response, json_response

        decision_id = str(decision_id or "").strip()
        if not decision_id:
            return error_response("decision_id is required", status_code=400)
        payload = self.bridge.get_decision_trace(decision_id)
        if payload is None:
            return error_response("decision not found", status_code=404)
        return json_response(payload)
