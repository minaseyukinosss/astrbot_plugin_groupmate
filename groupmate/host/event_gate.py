"""AstrBot-owned event classification before Groupmate runtime admission."""

from __future__ import annotations

from typing import Any, Callable, Optional, Sequence, Tuple

from ..models import StringEnum


class HostEventDisposition(StringEnum):
    HOST_COMMAND = "host_command"
    HOST_WAKE_PREFIX = "host_wake_prefix"
    GROUPMATE_MESSAGE = "groupmate_message"
    IGNORE = "ignore"


class HostEventGate:
    """Classify host-owned traffic without mutating the AstrBot event."""

    def __init__(
        self,
        config_resolver: Optional[Callable[[str], Any]] = None,
        enabled_groups: Sequence[str] = (),
    ) -> None:
        self._config_resolver = config_resolver
        self._enabled_groups = frozenset(
            str(group_id).strip()
            for group_id in (enabled_groups or ())
            if str(group_id).strip()
        )

    def classify(self, event: Any) -> HostEventDisposition:
        group_id = self._call_identifier(event, "get_group_id")
        if not group_id or (
            self._enabled_groups and group_id not in self._enabled_groups
        ):
            return HostEventDisposition.IGNORE
        if self._is_stopped(event):
            return HostEventDisposition.IGNORE
        sender_id = self._call_identifier(event, "get_sender_id")
        bot_id = self._call_identifier(event, "get_self_id")
        if sender_id and bot_id and sender_id == bot_id:
            return HostEventDisposition.IGNORE
        if self._has_command_handler(event):
            return HostEventDisposition.HOST_COMMAND

        raw_text = self._raw_text(event)
        if self._starts_with_wake_prefix(event, raw_text):
            return HostEventDisposition.HOST_WAKE_PREFIX
        if bool(getattr(event, "is_at_or_wake_command", False)) and not (
            self._has_explicit_direct_target(event, bot_id)
        ):
            return HostEventDisposition.HOST_WAKE_PREFIX
        return HostEventDisposition.GROUPMATE_MESSAGE

    def _starts_with_wake_prefix(self, event: Any, raw_text: str) -> bool:
        text = str(raw_text or "").strip()
        return bool(text) and any(
            text.startswith(prefix) for prefix in self._wake_prefixes(event)
        )

    def _wake_prefixes(self, event: Any) -> Tuple[str, ...]:
        values = ("/",)
        if self._config_resolver is not None:
            try:
                config = self._config_resolver(
                    str(getattr(event, "unified_msg_origin", "") or "")
                )
                configured = config.get("wake_prefix", values)
                if isinstance(configured, str):
                    configured = (configured,)
                values = tuple(configured or values)
            except Exception:
                values = ("/",)
        normalized = tuple(
            str(value).strip()
            for value in values
            if str(value or "").strip()
        )
        return normalized or ("/",)

    @classmethod
    def _has_command_handler(cls, event: Any) -> bool:
        handlers = cls._extra(event, "activated_handlers", ()) or ()
        for handler in handlers:
            for event_filter in getattr(handler, "event_filters", ()) or ():
                names = (
                    base.__name__.lower()
                    for base in type(event_filter).__mro__
                )
                if any("command" in name for name in names):
                    return True
        return False

    @classmethod
    def _has_explicit_direct_target(cls, event: Any, bot_id: str) -> bool:
        raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
        if isinstance(raw, dict):
            if bool(raw.get("reply_to_bot", False)):
                return True
            for segment in raw.get("message", ()) or ():
                if not isinstance(segment, dict):
                    continue
                if str(segment.get("type", "")).lower() != "at":
                    continue
                data = segment.get("data") or {}
                target = str(data.get("qq", data.get("user_id", "")))
                if target and target == bot_id:
                    return True
        components = getattr(
            getattr(event, "message_obj", None),
            "message",
            (),
        ) or ()
        for component in components:
            component_type = str(getattr(component, "type", "")).lower()
            class_name = component.__class__.__name__.lower()
            if class_name != "reply" and not component_type.endswith("reply"):
                continue
            if str(getattr(component, "sender_id", "") or "") == bot_id:
                return True
        return False

    @staticmethod
    def _raw_text(event: Any) -> str:
        raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
        payload = (
            raw.get("message", raw.get("raw_message", ""))
            if isinstance(raw, dict)
            else raw
        )
        if isinstance(payload, str):
            return payload
        if isinstance(payload, (list, tuple)):
            parts = []
            for segment in payload:
                if not isinstance(segment, dict):
                    continue
                if str(segment.get("type", "")).lower() not in ("text", "plain"):
                    continue
                data = segment.get("data") or {}
                parts.append(
                    str(data.get("text", segment.get("text", "")) or "")
                )
            if parts:
                return "".join(parts)
        return str(getattr(event, "message_str", "") or "")

    @staticmethod
    def _call_identifier(event: Any, method_name: str) -> str:
        method = getattr(event, method_name, None)
        if not callable(method):
            return ""
        try:
            return str(method() or "").strip()
        except Exception:
            return ""

    @staticmethod
    def _is_stopped(event: Any) -> bool:
        method = getattr(event, "is_stopped", None)
        if not callable(method):
            return False
        try:
            return bool(method())
        except Exception:
            return True

    @staticmethod
    def _extra(event: Any, key: str, default: Any) -> Any:
        method = getattr(event, "get_extra", None)
        if not callable(method):
            return default
        try:
            return method(key, default)
        except TypeError:
            value = method(key)
            return default if value is None else value
