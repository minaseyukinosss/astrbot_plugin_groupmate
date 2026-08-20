"""Pure-fact translation from AstrBot/OneBot group events."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Mapping

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
    ) -> None:
        self.persona_id = persona_id
        self.platform = "qq"
        self.external_trigger_policy = (
            external_trigger_policy or ExternalTriggerPolicy.create()
        )

    def translate(self, host_event: object) -> SocialEventEnvelope:
        raw = self._raw_message(host_event)
        group_id = str(raw.get("group_id") or _call_text(host_event, "get_group_id"))
        actor_id = str(raw.get("user_id") or _call_text(host_event, "get_sender_id"))
        now = int(time.time())
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
        for segment in segments:
            kind = str(segment.get("type") or "")
            data = segment.get("data") or {}
            if kind == "text":
                text_parts.append(str(data.get("text") or ""))
            elif kind == "reply" and not reply_to:
                reply_to = str(data.get("id") or "")
            elif kind == "at":
                mention = str(data.get("qq") or "")
                if mention and mention not in mentions:
                    mentions.append(mention)
            elif kind in {"image", "video", "record", "file"}:
                fact = {"type": kind}
                for key in ("url", "file", "id"):
                    if data.get(key) not in (None, ""):
                        fact[key] = _json_value(data[key])
                media.append(fact)

        message_text = "".join(text_parts) or str(
            getattr(host_event, "message_str", "") or ""
        )
        sender = raw.get("sender") if isinstance(raw.get("sender"), Mapping) else {}
        sender_name = str(sender.get("card") or sender.get("nickname") or _call_text(host_event, "get_sender_name"))
        message_obj = getattr(host_event, "message_obj", None)
        bot_id = str(
            getattr(message_obj, "self_id", "") or raw.get("self_id") or ""
        ).strip()
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
                "text": message_text,
                "segments": segments,
                "reply_to": reply_to or None,
                "mentions": mentions,
                "mentions_bot": bool(bot_id and bot_id in mentions),
                "media": media,
                "sender": {"id": actor_id, "name": sender_name},
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
        value = raw.get("message")
        if isinstance(value, str):
            return [{"type": "text", "data": {"text": value}}]
        if isinstance(value, (list, tuple)):
            result = []
            for item in value:
                if isinstance(item, Mapping):
                    data = item.get("data") if isinstance(item.get("data"), Mapping) else {}
                    result.append({"type": str(item.get("type") or ""), "data": _json_value(data)})
            return result
        text = str(getattr(host_event, "message_str", "") or "")
        return [{"type": "text", "data": {"text": text}}] if text else []


__all__ = ("AstrBotEventTranslator",)
