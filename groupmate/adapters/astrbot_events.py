"""Pure-fact translation from AstrBot/OneBot group events."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Callable, Mapping

from ..social_runtime.contracts import SocialEventEnvelope
from ..social_runtime.ownership import (
    ExternalTriggerPolicy,
    InteractionOwner,
    InteractionOwnership,
)


def _call_text(event: object, name: str) -> str:
    value = getattr(event, name, None)
    if not callable(value):
        return ""
    try:
        return str(value() or "").strip()
    except Exception:
        return ""


def _attribute_text(event: object, name: str) -> str:
    value = getattr(event, name, None)
    if callable(value) or value is None:
        return ""
    try:
        return str(value).strip()
    except Exception:
        return ""


def _json_value(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return str(value)


class AstrBotEventTranslator:
    """Preserves observable platform facts without making social decisions."""

    def __init__(
        self,
        persona_id: str,
        *,
        external_trigger_policy: ExternalTriggerPolicy | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.persona_id = persona_id
        self.platform = "qq"
        self.external_trigger_policy = (
            external_trigger_policy or ExternalTriggerPolicy.create()
        )
        self._clock = time.time if clock is None else clock

    def translate(self, host_event: object) -> SocialEventEnvelope:
        raw = self._raw_message(host_event)
        group_id = str(raw.get("group_id") or _call_text(host_event, "get_group_id"))
        actor_id = str(raw.get("user_id") or _call_text(host_event, "get_sender_id"))
        now = int(self._clock())
        occurred_at = int(raw.get("time") or now)
        segments = self._segments(raw, host_event)
        source_id = str(raw.get("message_id") or "").strip()
        if not source_id:
            facts = {
                "platform": self.platform,
                "group_id": group_id,
                "actor_id": actor_id,
                "occurred_at": occurred_at,
                "segments": segments,
            }
            digest = hashlib.sha256(
                json.dumps(facts, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            source_id = f"fingerprint:{digest}"

        text_parts, mentions, media = [], [], []
        reply_to = ""
        reply_to_actor_id = ""
        for segment in segments:
            kind = str(segment.get("type") or "")
            data = segment.get("data") or {}
            if kind == "text":
                text_parts.append(str(data.get("text") or ""))
            elif kind == "reply" and not reply_to:
                reply_to = str(data.get("id") or "")
                reply_to_actor_id = str(
                    data.get("sender_id") or data.get("qq") or ""
                ).strip()
            elif kind == "at":
                mention = str(data.get("qq") or "")
                if mention and mention not in mentions:
                    mentions.append(mention)
            elif kind in {"image", "video", "record", "file"}:
                fact = {"type": kind}
                for key in ("url", "file", "path", "id"):
                    if data.get(key) not in (None, ""):
                        fact[key] = _json_value(data[key])
                media.append(fact)

        message_text = "".join(text_parts) or str(
            getattr(host_event, "message_str", "") or ""
        )
        sender = raw.get("sender") if isinstance(raw.get("sender"), Mapping) else {}
        sender_name = str(sender.get("card") or sender.get("nickname") or _call_text(host_event, "get_sender_name"))
        sender_role = str(
            raw.get("sender_role") or sender.get("role") or ""
        ).strip().casefold()
        automation_hint = str(
            raw.get("automation_hint") or ""
        ).strip().casefold()
        message_obj = getattr(host_event, "message_obj", None)
        bot_id = str(
            getattr(message_obj, "self_id", "")
            or raw.get("self_id")
            or _call_text(host_event, "get_self_id")
            or ""
        ).strip()
        platform_id = _call_text(host_event, "get_platform_id") or self.platform
        session = _attribute_text(host_event, "unified_msg_origin") or None
        is_self = bool(bot_id and actor_id and actor_id == bot_id)
        ownership = self.external_trigger_policy.classify(message_text)
        if ownership is None:
            ownership = InteractionOwnership(
                owner=InteractionOwner.UNKNOWN,
                social_eligible=not is_self,
                owner_ref=None,
                source=(
                    "unattributed_self_output" if is_self else "unclassified_input"
                ),
            )
        event_id = f"{self.platform}:{source_id}"
        return SocialEventEnvelope.create(
            event_id=event_id,
            event_type="platform.message",
            occurred_at=occurred_at,
            received_at=now,
            persona_id=self.persona_id,
            group_id=group_id,
            actor_id=actor_id,
            source_message_id=source_id,
            correlation_id=event_id,
            causation_id=f"qq:{reply_to}" if reply_to else None,
            payload={
                "platform": self.platform,
                "platform_id": platform_id,
                "session": session,
                "bot_id": bot_id,
                "group_name": str(raw.get("group_name") or "").strip()[:60]
                or None,
                "text": message_text,
                "segments": segments,
                "reply_to": reply_to or None,
                "reply_to_actor_id": reply_to_actor_id or None,
                "reply_to_bot": bool(
                    bot_id and reply_to_actor_id and bot_id == reply_to_actor_id
                ),
                "mentions": mentions,
                "mentions_bot": bool(bot_id and bot_id in mentions),
                "media": media,
                "sender": {"id": actor_id, "name": sender_name},
                "sender_role": sender_role or None,
                "automation_hint": automation_hint or None,
                "is_self": is_self,
                "interaction_owner": ownership.owner.value,
                "social_eligible": ownership.social_eligible,
                "owner_ref": ownership.owner_ref,
                "ownership_source": ownership.source,
                "external_trigger_kind": ownership.trigger_kind,
                "external_trigger_value": ownership.trigger_value,
            },
        )

    @staticmethod
    def _raw_message(host_event: object) -> dict[str, object]:
        if isinstance(host_event, Mapping):
            return dict(host_event)
        raw = getattr(getattr(host_event, "message_obj", None), "raw_message", None)
        return dict(raw) if isinstance(raw, Mapping) else {}

    @staticmethod
    def _segments(raw: Mapping[str, object], host_event: object) -> list[dict]:
        component_segments = AstrBotEventTranslator._component_segments(host_event)
        value = raw.get("message")
        if isinstance(value, str):
            raw_segments = [{"type": "text", "data": {"text": value}}]
            return AstrBotEventTranslator._enrich_segments(
                raw_segments, component_segments
            )
        if isinstance(value, (list, tuple)):
            result = []
            for item in value:
                if isinstance(item, Mapping):
                    data = item.get("data") if isinstance(item.get("data"), Mapping) else {}
                    result.append({"type": str(item.get("type") or ""), "data": _json_value(data)})
            return AstrBotEventTranslator._enrich_segments(
                result, component_segments
            )
        if component_segments:
            return component_segments
        text = str(getattr(host_event, "message_str", "") or "")
        return [{"type": "text", "data": {"text": text}}] if text else []

    @staticmethod
    def _component_segments(host_event: object) -> list[dict]:
        message_obj = getattr(host_event, "message_obj", None)
        components = getattr(message_obj, "message", None)
        if not isinstance(components, (list, tuple)):
            return []
        result: list[dict] = []
        for component in components:
            converter = getattr(component, "toDict", None)
            converted = None
            if callable(converter):
                try:
                    converted = converter()
                except Exception:
                    converted = None
            converted_map = converted if isinstance(converted, Mapping) else {}
            kind = str(converted_map.get("type") or "").strip().lower()
            data_value = converted_map.get("data")
            data = dict(data_value) if isinstance(data_value, Mapping) else {}
            if not kind:
                class_name = type(component).__name__.lower()
                kind = {"plain": "text", "atall": "at"}.get(
                    class_name, class_name
                )
            if kind == "at":
                qq = str(getattr(component, "qq", data.get("qq", "")) or "")
                name = str(getattr(component, "name", "") or "").strip()
                data["qq"] = qq
                if name:
                    data["name"] = name
            elif kind == "reply":
                for field in (
                    "id",
                    "sender_id",
                    "sender_nickname",
                    "time",
                    "message_str",
                ):
                    value = getattr(component, field, None)
                    if value not in (None, "", 0):
                        data[field] = _json_value(value)
            result.append({"type": kind, "data": _json_value(data)})
        return result

    @staticmethod
    def _enrich_segments(
        raw_segments: list[dict],
        component_segments: list[dict],
    ) -> list[dict]:
        if not component_segments:
            return raw_segments
        used: set[int] = set()
        for raw_segment in raw_segments:
            kind = str(raw_segment.get("type") or "").lower()
            if kind not in {"at", "reply"}:
                continue
            raw_data = raw_segment.get("data")
            if not isinstance(raw_data, dict):
                continue
            identity_field = "qq" if kind == "at" else "id"
            raw_identity = str(raw_data.get(identity_field) or "")
            for index, component_segment in enumerate(component_segments):
                if index in used or component_segment.get("type") != kind:
                    continue
                component_data = component_segment.get("data")
                if not isinstance(component_data, Mapping):
                    continue
                component_identity = str(
                    component_data.get(identity_field) or ""
                )
                if (
                    raw_identity
                    and component_identity
                    and raw_identity != component_identity
                ):
                    continue
                if kind == "at":
                    name = str(component_data.get("name") or "").strip()
                    if name:
                        raw_data["name"] = name
                else:
                    for field in (
                        "sender_id",
                        "sender_nickname",
                        "time",
                        "message_str",
                    ):
                        value = component_data.get(field)
                        if value not in (None, "", 0):
                            raw_data[field] = _json_value(value)
                used.add(index)
                break
        return raw_segments


__all__ = ("AstrBotEventTranslator",)
