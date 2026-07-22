"""AstrBot Plugin Page API for the Groupmate control center."""

from __future__ import annotations

import inspect
import json
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


PLUGIN_NAME = "astrbot_plugin_groupmate"
_LABELS = {"must_respond", "may_respond", "must_silence", "skipped"}
_ACTIONS = {"all", "respond", "ignore", "bypass"}
_FILTER_LABELS = _LABELS | {"all", "unlabeled"}


def _setting(bridge: Any, key: str, default: Any = None) -> Any:
    getter = getattr(bridge, "_setting", None)
    if callable(getter):
        return getter(key, default)
    settings = getattr(bridge, "settings", None)
    if isinstance(settings, dict):
        return settings.get(key, default)
    config = getattr(bridge, "config", None)
    if isinstance(config, dict):
        return config.get(key, default)
    return default


def normalize_label(value: Any) -> Optional[str]:
    label = str(value or "").strip()
    return label if label in _LABELS else None


def parse_shadow_filters(params: Any) -> Tuple[str, str, int, Optional[str]]:
    def get(name: str, default: Any = None) -> Any:
        if isinstance(params, dict):
            return params.get(name, default)
        getter = getattr(params, "get", None)
        return getter(name, default) if callable(getter) else default

    label = str(get("label", "unlabeled") or "unlabeled").strip()
    action = str(get("action", "all") or "all").strip()
    if label not in _FILTER_LABELS:
        raise ValueError("invalid shadow label filter")
    if action not in _ACTIONS:
        raise ValueError("invalid shadow action filter")
    try:
        limit = int(get("limit", 20) or 20)
    except (TypeError, ValueError):
        raise ValueError("invalid shadow page limit")
    if not 1 <= limit <= 50:
        raise ValueError("invalid shadow page limit")
    cursor = get("cursor")
    if cursor is not None:
        cursor = str(cursor).strip() or None
    return label, action, limit, cursor


def _parse_context(raw_context: Any) -> Optional[list]:
    if not raw_context:
        return None
    try:
        context = json.loads(raw_context) if isinstance(raw_context, str) else raw_context
    except (RecursionError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(context, list):
        return None
    safe = []
    allowed = {
        "index",
        "sender",
        "text",
        "seconds_from_start",
        "reply",
        "mentions_bot",
        "reply_to_bot",
        "is_command",
        "is_bot",
        "segment_types",
    }
    for item in context:
        if not isinstance(item, dict):
            continue
        safe.append({key: item[key] for key in allowed if key in item})
    return safe or None


def _message_preview(context: Optional[list]) -> str:
    if not context:
        return "未保存文本"
    for item in reversed(context):
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()[:300]
    return "未保存文本"


def serialize_shadow_decision(
    row: Dict[str, Any],
    include_context: bool = False,
    include_preview: Optional[bool] = None,
) -> Dict[str, Any]:
    context = _parse_context(row.get("context_json"))
    if include_preview is None:
        include_preview = include_context
    result = {
        "decision_id": str(row.get("decision_id", "")),
        "trigger": str(row.get("trigger", "")),
        "action": str(row.get("action", "")),
        "confidence": float(row.get("confidence", 0.0) or 0.0),
        "reason_code": str(row.get("reason_code", "")),
        "would_rate_limit": bool(row.get("would_rate_limit", False)),
        "label": str(row.get("label", "unlabeled")),
        "labeled_at": row.get("labeled_at"),
        "model_id": str(row.get("model_id", "")),
        "policy_version": str(row.get("policy_version", "")),
        "latency_ms": float(row.get("latency_ms", 0.0) or 0.0),
        "error_code": row.get("error_code"),
        "created_at": int(row.get("created_at", 0) or 0),
        "message_preview": _message_preview(context) if include_preview else "未保存文本",
    }
    if include_context and context:
        result["context"] = context
    return result


def build_overview_payload(bridge: Any) -> Dict[str, Any]:
    status = bridge.status()
    stats = status.get("shadow") or bridge.memory.shadow_stats()
    labels = dict(stats.get("labels") or {})
    total = int(stats.get("total", 0) or 0)
    unlabeled = int(labels.get("unlabeled", 0) or 0)
    labeled_count = max(0, total - unlabeled)
    groups = status.get("groups") or {}
    if isinstance(groups, dict):
        group_count = len(groups)
    elif isinstance(groups, (list, tuple, set)):
        group_count = len(groups)
    else:
        group_count = 0
    page = bridge.memory.shadow_decision_page(label="unlabeled", action="all", limit=3)
    store_text = bool(_setting(bridge, "shadow_store_message_text", False))
    recent = [
        serialize_shadow_decision(
            row, include_context=False, include_preview=store_text
        )
        for row in page["items"]
    ]
    return {
        "runtime": {
            "paused": bool(status.get("paused", False)),
            "shadow_mode": bool(status.get("shadow_mode", False)),
            "initialized_group_count": group_count,
            "last_updated_at": max(
                (int(item.get("created_at", 0)) for item in recent), default=0
            ),
        },
        "pending_count": unlabeled,
        "recent": recent,
        "actions": dict(stats.get("actions") or {}),
        "labels": labels,
        "reasons": dict(stats.get("reasons") or {}),
        "data_policy": {
            "stores_message_text": store_text,
            "shadow_retention_days": int(
                _setting(bridge, "shadow_retention_days", 7) or 7
            ),
            "labeled_count": labeled_count,
            "sample_sufficient": labeled_count >= 100,
        },
    }


def label_shadow_decision(
    bridge: Any, decision_id: str, label: str, labeled_at: int
) -> Optional[Dict[str, Any]]:
    normalized = normalize_label(label)
    if normalized is None:
        raise ValueError("invalid shadow label")
    decision_id = str(decision_id or "").strip()
    if not decision_id or len(decision_id) > 128:
        raise ValueError("invalid decision id")
    if bridge.memory.get_shadow_decision(decision_id) is None:
        return None
    bridge.memory.label_shadow_decision(decision_id, normalized, int(labeled_at))
    row = bridge.memory.get_shadow_decision(decision_id)
    if row is None:
        return None
    include_context = bool(_setting(bridge, "shadow_store_message_text", False))
    return serialize_shadow_decision(row, include_context=include_context)


def set_runtime_paused(bridge: Any, paused: bool) -> Dict[str, Any]:
    bridge.paused = bool(paused)
    return {"paused": bridge.paused, "runtime_only": True}


def _web_helpers():
    try:
        from astrbot.api.web import (
            error_response,
            file_response,
            json_response,
            request,
        )

        return request, json_response, error_response, file_response
    except ImportError:
        from quart import jsonify, request, send_file

        def json_response(payload):
            return jsonify(payload)

        def error_response(message, status_code=400):
            return jsonify({"status": "error", "message": str(message)}), status_code

        def file_response(path, filename=None, content_type=None):
            return send_file(
                str(path),
                as_attachment=True,
                attachment_filename=filename,
                mimetype=content_type,
            )

        return request, json_response, error_response, file_response


async def _resolve_response(response):
    if inspect.isawaitable(response):
        return await response
    return response


async def _read_json(request):
    getter = getattr(request, "json", None)
    if callable(getter):
        try:
            value = getter(default={})
        except TypeError:
            value = getter()
        if inspect.isawaitable(value):
            value = await value
        return value if isinstance(value, dict) else {}
    getter = getattr(request, "get_json", None)
    if callable(getter):
        value = getter(silent=True)
        if inspect.isawaitable(value):
            value = await value
        return value if isinstance(value, dict) else {}
    return {}


class GroupmateWebAPI:
    def __init__(self, bridge: Any, data_dir: Path) -> None:
        self.bridge = bridge
        self.data_dir = Path(data_dir)

    def register(self, context: Any) -> None:
        context.register_web_api(
            f"/{PLUGIN_NAME}/dashboard/overview",
            self.overview,
            ["GET"],
            "Groupmate overview",
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/shadow/decisions",
            self.decisions,
            ["GET"],
            "List shadow decisions",
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/shadow/decisions/<decision_id>/label",
            self.label,
            ["POST"],
            "Label a shadow decision",
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/runtime/pause",
            self.pause,
            ["POST"],
            "Pause Groupmate",
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/runtime/resume",
            self.resume,
            ["POST"],
            "Resume Groupmate",
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/shadow/export",
            self.export,
            ["GET"],
            "Export reviewed shadow data",
        )

    async def overview(self):
        _, json_response, error_response, _ = _web_helpers()
        try:
            getter = getattr(self.bridge, "web_overview", None)
            payload = getter() if callable(getter) else build_overview_payload(self.bridge)
            return await _resolve_response(json_response(payload))
        except Exception:
            return await _resolve_response(error_response("概览暂时无法读取。", status_code=500))

    async def decisions(self):
        request, json_response, error_response, _ = _web_helpers()
        try:
            params = getattr(request, "query", getattr(request, "args", {}))
            label, action, limit, cursor = parse_shadow_filters(params)
            getter = getattr(self.bridge, "web_shadow_decisions", None)
            if callable(getter):
                payload = getter(label=label, action=action, limit=limit, cursor=cursor)
            else:
                page = self.bridge.memory.shadow_decision_page(
                    label=label, action=action, limit=limit, cursor=cursor
                )
                include_context = bool(
                    _setting(self.bridge, "shadow_store_message_text", False)
                )
                payload = {
                    "items": [
                        serialize_shadow_decision(row, include_context=include_context)
                        for row in page["items"]
                    ],
                    "next_cursor": page["next_cursor"],
                    "has_more": page["has_more"],
                }
            return await _resolve_response(json_response(payload))
        except ValueError as exc:
            return await _resolve_response(error_response(str(exc), status_code=400))
        except Exception:
            return await _resolve_response(error_response("影子决策暂时无法读取。", status_code=500))

    async def label(self, decision_id: str):
        request, json_response, error_response, _ = _web_helpers()
        try:
            payload = await _read_json(request)
            label = payload.get("label")
            labeler = getattr(self.bridge, "web_label_shadow_decision", None)
            if callable(labeler):
                result = labeler(decision_id, label, int(time.time()))
            else:
                result = label_shadow_decision(
                    self.bridge,
                    decision_id,
                    label,
                    labeled_at=int(time.time()),
                )
            if result is None:
                return await _resolve_response(error_response("没有找到这条影子决策。", status_code=404))
            return await _resolve_response(json_response(result))
        except ValueError as exc:
            return await _resolve_response(error_response(str(exc), status_code=400))
        except Exception:
            return await _resolve_response(error_response("影子决策暂时无法标注。", status_code=500))

    async def pause(self):
        _, json_response, _, _ = _web_helpers()
        return await _resolve_response(json_response(set_runtime_paused(self.bridge, True)))

    async def resume(self):
        _, json_response, _, _ = _web_helpers()
        return await _resolve_response(json_response(set_runtime_paused(self.bridge, False)))

    async def export(self):
        _, _, error_response, file_response = _web_helpers()
        from .evaluation.shadow_export import export_labeled_shadow_dataset

        target_dir = self.data_dir / "exports"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / "shadow_reviewed.jsonl"
        try:
            export_labeled_shadow_dataset(self.bridge.memory, target)
        except ValueError as exc:
            return await _resolve_response(error_response(str(exc), status_code=400))
        except Exception:
            return await _resolve_response(error_response("评测集暂时无法导出。", status_code=500))
        return await _resolve_response(
            file_response(target, filename="shadow_reviewed.jsonl", content_type="application/json")
        )
