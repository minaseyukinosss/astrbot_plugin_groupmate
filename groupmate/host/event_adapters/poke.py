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
        if target_id == bot_id:
            poke_role = "direct"
            metadata_target = bot_id
        else:
            poke_role = "bystander"
            metadata_target = target_id

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
                    "poke_role": poke_role,
                    "target_id": metadata_target,
                    "poker_id": sender_id,
                    "source_adapter": "aiocqhttp_poke",
                },
            )
        )

    @staticmethod
    def _is_aiocqhttp(event: Any) -> bool:
        # AstrBot umo is platform_id:message_type:session_id (e.g. "default:...")
        # Prefer the adapter type name over the configured platform id.
        getter = getattr(event, "get_platform_name", None)
        if callable(getter):
            try:
                if str(getter() or "").strip().casefold() == "aiocqhttp":
                    return True
            except Exception:
                pass
        platform_meta = getattr(event, "platform_meta", None)
        if platform_meta is not None:
            name = str(getattr(platform_meta, "name", "") or "").strip().casefold()
            if name == "aiocqhttp":
                return True
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
            return True, cls._target_from_component(component)

        raw = getattr(message_obj, "raw_message", None)
        if not isinstance(raw, dict):
            return False, ""
        for segment in raw.get("message", ()) or ():
            if not isinstance(segment, dict):
                continue
            if str(segment.get("type", "") or "").lower() != "poke":
                continue
            data = segment.get("data") or {}
            if not isinstance(data, dict):
                return True, ""
            return True, cls._target_from_mapping(data)
        if str(raw.get("sub_type", "") or "").lower() == "poke":
            return True, cls._normalize_target(raw.get("target_id"))
        return False, ""

    @classmethod
    def _target_from_component(cls, component: Any) -> str:
        for attr in ("target_id", "id", "qq"):
            if not hasattr(component, attr):
                continue
            target = cls._normalize_target(getattr(component, attr, None))
            if target:
                return target
        return ""

    @classmethod
    def _target_from_mapping(cls, data: dict) -> str:
        for key in ("target_id", "id", "qq"):
            target = cls._normalize_target(data.get(key))
            if target:
                return target
        return ""

    @staticmethod
    def _normalize_target(value: Any) -> str:
        if callable(value):
            try:
                value = value()
            except Exception:
                return ""
        text = str(value or "").strip()
        if text in ("", "0", "None"):
            return ""
        return text

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
