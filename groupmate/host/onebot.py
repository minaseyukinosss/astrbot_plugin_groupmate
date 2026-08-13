"""OneBot / NapCat 消息翻译与历史拉取。"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, List, Mapping, Optional, Sequence

from ..models import ChatMessage


class OneBotTranslator:
    @staticmethod
    def _usable_mention_name(value: Any, user_id: str = "") -> str:
        name = str(value or "").strip().lstrip("@").strip()
        if not name or name in {"某人", "群成员", "unknown", "未知"}:
            return ""
        if user_id and name == str(user_id):
            return ""
        return name[:80]

    @staticmethod
    def _coerce_timestamp(value: Any) -> int:
        try:
            timestamp = int(value or 0)
        except (TypeError, ValueError):
            timestamp = 0
        if timestamp <= 0:
            import time

            return int(time.time())
        return timestamp

    @classmethod
    def from_history(cls, raw: Dict[str, Any], bot_id: str) -> ChatMessage:
        segments = raw.get("message") or raw.get("content") or []
        if isinstance(segments, str):
            segments = [{"type": "text", "data": {"text": segments}}]
        text_parts: List[str] = []
        image_urls: List[str] = []
        segment_types: List[str] = []
        reply_id: Optional[str] = None
        mentions_bot = False
        mentioned_user_ids: List[str] = []
        mention_names: Dict[str, str] = {}
        anonymous_mention_ids: List[str] = []
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            kind = str(segment.get("type", "")).lower()
            data = segment.get("data") or {}
            segment_types.append(kind)
            if kind in ("text", "plain"):
                text = data.get("text") or segment.get("text") or ""
                if text:
                    text_parts.append(str(text))
            elif kind == "at":
                qq = str(data.get("qq", data.get("user_id", "")))
                if qq and qq == str(bot_id):
                    mentions_bot = True
                elif qq and qq not in ("all", "0"):
                    mentioned_user_ids.append(qq)
                name = cls._usable_mention_name(
                    data.get("name") or data.get("display_name"), qq
                )
                if name:
                    if qq and qq not in ("all", "0") and qq != str(bot_id):
                        mention_names[qq] = name
                    text_parts.append("@" + str(name))
                elif qq and qq not in ("all", "0") and qq != str(bot_id):
                    # Prefer a human-readable cue; never embed raw QQ in chat text.
                    text_parts.append("@某人")
                    anonymous_mention_ids.append(qq)
            elif kind == "reply":
                reply_id = str(data.get("id", data.get("message_id", ""))) or None
            elif kind == "image":
                url = data.get("url") or data.get("file")
                if url:
                    image_urls.append(str(url))
            elif kind in ("record", "video", "file"):
                text_parts.append("[{}]".format(kind))

        sender = raw.get("sender") or {}
        sender_id = str(raw.get("user_id", sender.get("user_id", "")))
        group_id = str(raw.get("group_id", ""))
        timestamp = cls._coerce_timestamp(raw.get("time", raw.get("timestamp", 0)))
        reply_to_bot = bool(raw.get("reply_to_bot", False))
        if reply_id and str(raw.get("reply_sender_id", "")) == str(bot_id):
            reply_to_bot = True
        return ChatMessage(
            message_id=str(raw.get("message_id", raw.get("id", ""))),
            group_id=group_id,
            sender_id=sender_id,
            sender_name=str(
                sender.get("card") or sender.get("nickname") or sender_id
            ),
            text="".join(text_parts).strip(),
            timestamp=timestamp,
            reply_to_message_id=reply_id,
            reply_to_bot=reply_to_bot,
            mentions_bot=mentions_bot,
            is_bot=sender_id == str(bot_id),
            image_urls=tuple(dict.fromkeys(image_urls)),
            segment_types=tuple(segment_types),
            mentioned_user_ids=tuple(dict.fromkeys(mentioned_user_ids)),
            metadata={
                "raw": raw,
                "mention_names": mention_names,
                "anonymous_mention_ids": anonymous_mention_ids,
            },
        )

    @classmethod
    def from_event(
        cls,
        event: Any,
        bot_id: str,
        is_command: bool = False,
    ) -> ChatMessage:
        raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
        if isinstance(raw, dict):
            message = cls.from_history(raw, bot_id)
        else:
            message = ChatMessage(
                message_id=str(getattr(event.message_obj, "message_id", "")),
                group_id=str(event.get_group_id()),
                sender_id=str(event.get_sender_id()),
                sender_name=str(event.get_sender_name()),
                text=str(event.message_str or ""),
                timestamp=cls._coerce_timestamp(
                    getattr(event.message_obj, "timestamp", 0)
                ),
                is_bot=str(event.get_sender_id()) == str(bot_id),
            )
        reply_id = message.reply_to_message_id
        reply_to_bot = message.reply_to_bot
        components = getattr(getattr(event, "message_obj", None), "message", ()) or ()
        mentioned_user_ids = list(message.mentioned_user_ids)
        mention_names = dict(message.metadata.get("mention_names") or {})
        anonymous_mention_ids = list(
            message.metadata.get("anonymous_mention_ids") or ()
        )
        native_direct = bool(getattr(event, "is_at_or_wake_command", False))
        for component in components:
            component_type = str(getattr(component, "type", "")).lower()
            class_name = component.__class__.__name__.lower()
            if class_name == "reply" or component_type.endswith("reply"):
                resolved_id = str(getattr(component, "id", "") or "")
                if resolved_id:
                    reply_id = resolved_id
                sender_id = str(getattr(component, "sender_id", "") or "")
                if sender_id and sender_id == str(bot_id):
                    reply_to_bot = True
                continue
            if class_name not in {"at", "mention"} and not (
                component_type.endswith("at") or component_type.endswith("mention")
            ):
                continue
            target_id = str(
                getattr(component, "qq", "")
                or getattr(component, "user_id", "")
                or getattr(component, "target", "")
                or ""
            )
            if target_id == str(bot_id):
                native_direct = True
                continue
            if not target_id or target_id in {"all", "0"}:
                continue
            mentioned_user_ids.append(target_id)
            name = cls._usable_mention_name(
                getattr(component, "name", "")
                or getattr(component, "display_name", ""),
                target_id,
            )
            if name:
                mention_names[target_id] = name
            elif target_id not in anonymous_mention_ids:
                anonymous_mention_ids.append(target_id)
        translated = replace(
            message,
            reply_to_message_id=reply_id,
            reply_to_bot=reply_to_bot,
            is_command=is_command,
            mentions_bot=message.mentions_bot or native_direct,
            mentioned_user_ids=tuple(dict.fromkeys(mentioned_user_ids)),
            metadata=dict(
                message.metadata,
                native_direct=native_direct,
                mention_names=mention_names,
                anonymous_mention_ids=anonymous_mention_ids,
            ),
        )
        return cls.enrich_mentions(translated, mention_names)

    @classmethod
    def enrich_mentions(
        cls,
        message: ChatMessage,
        names: Mapping[str, str],
    ) -> ChatMessage:
        """Replace anonymous visual labels while retaining platform target ids."""
        resolved = dict(message.metadata.get("mention_names") or {})
        anonymous = list(message.metadata.get("anonymous_mention_ids") or ())
        text = str(message.text or "")
        for user_id in message.mentioned_user_ids:
            name = cls._usable_mention_name(names.get(str(user_id), ""), str(user_id))
            if not name:
                continue
            resolved[str(user_id)] = name
            if str(user_id) in anonymous and "@某人" in text:
                text = text.replace("@某人", "@" + name, 1)
                anonymous.remove(str(user_id))
        return replace(
            message,
            text=text,
            metadata=dict(
                message.metadata,
                mention_names=resolved,
                anonymous_mention_ids=anonymous,
            ),
        )


async def resolve_member_name(bot: Any, group_id: str, user_id: str) -> str:
    call = getattr(bot, "call_action", None)
    if not callable(call):
        return ""
    group_value = int(group_id) if str(group_id).isdigit() else group_id
    user_value = int(user_id) if str(user_id).isdigit() else user_id
    for action, kwargs in (
        (
            "get_group_member_info",
            {"group_id": group_value, "user_id": user_value, "no_cache": False},
        ),
        ("get_stranger_info", {"user_id": user_value, "no_cache": False}),
    ):
        try:
            result = await call(action, **kwargs)
        except Exception:
            continue
        if isinstance(result, dict) and isinstance(result.get("data"), dict):
            result = result["data"]
        if isinstance(result, dict):
            name = OneBotTranslator._usable_mention_name(
                result.get("card") or result.get("nickname") or result.get("name"),
                user_id,
            )
        else:
            name = OneBotTranslator._usable_mention_name(
                getattr(result, "card", "")
                or getattr(result, "nickname", "")
                or getattr(result, "name", ""),
                user_id,
            )
        if name:
            return name
    return ""


class NapCatHistoryPort:
    def __init__(self, bot: Any, bot_id: str) -> None:
        self.bot = bot
        self.bot_id = str(bot_id)

    async def fetch_recent(self, group_id: str, count: int) -> Sequence[ChatMessage]:
        response = await self.bot.call_action(
            "get_group_msg_history",
            group_id=int(group_id),
            count=int(count),
            reverseOrder=True,
        )
        rows = response.get("messages", []) if isinstance(response, dict) else response
        messages = [
            OneBotTranslator.from_history(row, self.bot_id)
            for row in (rows or [])
            if isinstance(row, dict)
        ]
        names: Dict[str, str] = {}
        for message in messages:
            names.update(message.metadata.get("mention_names") or {})
        for user_id in dict.fromkeys(
            user_id
            for message in messages
            for user_id in message.mentioned_user_ids
        ):
            if user_id not in names:
                name = await resolve_member_name(self.bot, str(group_id), user_id)
                if name:
                    names[user_id] = name
        return [OneBotTranslator.enrich_mentions(message, names) for message in messages]
