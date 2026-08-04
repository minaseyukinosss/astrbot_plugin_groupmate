"""AIOCQHTTP poke recognition and safe synthetic-message translation."""

from __future__ import annotations

from hashlib import sha256
from typing import Any, Tuple

from ...models import ChatMessage, MessageOrigin
from .base import (
    HostEventAdapter,
    HostEventAdapterManifest,
    HostEventAdapterResult,
)


class PokeEventAdapter(HostEventAdapter):
    manifest = HostEventAdapterManifest("aiocqhttp_poke", ("poke",))

    def __init__(self, enabled: bool = False) -> None:
        self.enabled = bool(enabled)
        super().__init__()

    def adapt(self, event: Any) -> HostEventAdapterResult:
        if not self._is_aiocqhttp(event):
            return HostEventAdapterResult.not_matched()
        matched, target_id = self._poke_target(event)
        if not matched:
            return HostEventAdapterResult.not_matched()
        if not self.enabled:
            return HostEventAdapterResult.bypassed("disabled")

        group_id = self._identifier(event, "get_group_id")
        sender_id = self._identifier(event, "get_sender_id")
        sender_name = self._identifier(event, "get_sender_name") or sender_id
        bot_id = self._identifier(event, "get_self_id")
        timestamp = self._timestamp(event)
        if not all((group_id, sender_id, bot_id, target_id, timestamp > 0)):
            return HostEventAdapterResult.bypassed("invalid_event")
        if target_id != bot_id:
            return HostEventAdapterResult.bypassed("target_not_bot")

        return HostEventAdapterResult.admitted(
            ChatMessage(
                message_id=self._event_id(
                    event,
                    group_id,
                    sender_id,
                    target_id,
                    timestamp,
                ),
                group_id=group_id,
                sender_id=sender_id,
                sender_name=sender_name,
                text="",
                timestamp=timestamp,
                segment_types=("poke",),
                origin=MessageOrigin.SYSTEM_SYNTHETIC,
                platform="aiocqhttp",
                bot_id=bot_id,
                metadata={
                    "interaction_kind": "poke",
                    "target_id": bot_id,
                    "source_adapter": "aiocqhttp_poke",
                },
            )
        )

    @staticmethod
    def _is_aiocqhttp(event: Any) -> bool:
        origin = str(getattr(event, "unified_msg_origin", "") or "")
        platform, _, _ = origin.partition(":")
        return platform.strip().casefold() == "aiocqhttp"

    @classmethod
    def _poke_target(cls, event: Any) -> Tuple[bool, str]:
        message_obj = getattr(event, "message_obj", None)
        for component in getattr(message_obj, "message", ()) or ():
            component_type = str(
                getattr(component, "type", "") or ""
            ).lower()
            if component_type != "poke" and (
                component.__class__.__name__.lower() != "poke"
            ):
                continue
            return True, cls._component_target(component)

        raw = cls._as_mapping(getattr(message_obj, "raw_message", None))
        if raw is None:
            return False, ""
        for segment in raw.get("message", ()) or ():
            if not isinstance(segment, dict):
                continue
            if str(segment.get("type", "") or "").lower() != "poke":
                continue
            data = segment.get("data") or {}
            if not isinstance(data, dict):
                return True, ""
            # Notice-style targets use target_id/qq; AstrBot notice→Poke
            # stores the user target in id. Send-format poke faces also use
            # id, but those are not notice events with sub_type=poke.
            target = data.get("target_id", data.get("qq", data.get("id", "")))
            return True, str(target or "").strip()
        if str(raw.get("sub_type", "") or "").lower() == "poke":
            return True, str(raw.get("target_id", "") or "").strip()
        return False, ""

    @classmethod
    def _component_target(cls, component: Any) -> str:
        """Resolve poke target from AstrBot Poke component fields.

        Current AstrBot builds notice pokes as ``Poke(id=target_id)`` and
        exposes ``target_id()``. Older builds used ``qq``. Never stringify a
        bound method — that previously caused false ``target_not_bot``.
        """
        method = getattr(component, "target_id", None)
        if callable(method):
            try:
                value = method()
            except Exception:
                value = None
            text = cls._normalize_target(value)
            if text:
                return text
        for attr in ("qq", "id"):
            value = getattr(component, attr, None)
            if callable(value):
                continue
            text = cls._normalize_target(value)
            if text:
                return text
        return ""

    @staticmethod
    def _normalize_target(value: Any) -> str:
        text = str(value or "").strip()
        if not text or text == "0":
            return ""
        return text

    @staticmethod
    def _as_mapping(value: Any):
        if value is None or isinstance(value, (str, bytes, list, tuple)):
            return None
        if isinstance(value, dict):
            return value
        getter = getattr(value, "get", None)
        if not callable(getter):
            return None
        try:
            return {
                "message": getter("message", ()),
                "sub_type": getter("sub_type", ""),
                "target_id": getter("target_id", ""),
                "message_id": getter("message_id", getter("id", "")),
                "time": getter("time", getter("timestamp", 0)),
            }
        except Exception:
            return None

    @staticmethod
    def _identifier(event: Any, method_name: str) -> str:
        method = getattr(event, method_name, None)
        if not callable(method):
            return ""
        try:
            return str(method() or "").strip()
        except Exception:
            return ""

    @staticmethod
    def _timestamp(event: Any) -> int:
        message_obj = getattr(event, "message_obj", None)
        raw = getattr(message_obj, "raw_message", None)
        value = getattr(message_obj, "timestamp", 0)
        if not value and isinstance(raw, dict):
            value = raw.get("time", raw.get("timestamp", 0))
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _event_id(
        event: Any,
        group_id: str,
        sender_id: str,
        target_id: str,
        timestamp: int,
    ) -> str:
        message_obj = getattr(event, "message_obj", None)
        raw = getattr(message_obj, "raw_message", None)
        value = getattr(message_obj, "message_id", "")
        if not value and isinstance(raw, dict):
            value = raw.get("message_id", raw.get("id", ""))
        value = str(value or "").strip()
        if value:
            return value if value.startswith("poke-") else "poke-{}".format(value)

        canonical = "|".join(
            (
                "aiocqhttp",
                group_id,
                sender_id,
                target_id,
                str(int(timestamp)),
                "poke",
            )
        )
        digest = sha256(canonical.encode("utf-8")).hexdigest()[:24]
        return "poke-{}".format(digest)
