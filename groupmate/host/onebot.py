"""OneBot / NapCat 消息翻译与历史拉取。"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, List, Optional, Sequence

from ..models import ChatMessage


class OneBotTranslator:
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
                name = data.get("name") or data.get("display_name")
                if name:
                    text_parts.append("@" + str(name))
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
            metadata={"raw": raw},
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
        native_direct = bool(getattr(event, "is_at_or_wake_command", False))
        return replace(
            message,
            is_command=is_command,
            mentions_bot=message.mentions_bot or native_direct,
            metadata=dict(message.metadata, native_direct=native_direct),
        )


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
        return [
            OneBotTranslator.from_history(row, self.bot_id)
            for row in (rows or [])
            if isinstance(row, dict)
        ]
