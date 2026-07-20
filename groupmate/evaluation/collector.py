"""Build bounded privacy-safe samples from normalized group topics."""

from __future__ import annotations

from typing import Dict, List

from ..models import ChatMessage, TopicSnapshot
from .models import ShadowSample


class ShadowCollector:
    def __init__(self, store_text: bool = False, max_messages: int = 20) -> None:
        self.store_text = bool(store_text)
        self.max_messages = max(1, min(100, int(max_messages)))

    def collect(self, topic: TopicSnapshot) -> ShadowSample:
        messages = self._bounded_messages(topic)
        participants = {message.sender_id for message in messages if not message.is_bot}
        features = {
            "message_count": len(messages),
            "participant_count": len(participants),
            "text_char_count": sum(len(message.text) for message in messages),
            "image_count": sum(len(message.image_urls) for message in messages),
            "has_reply_chain": any(message.reply_to_message_id for message in messages),
            "media_types": sorted(
                {
                    kind
                    for message in messages
                    for kind in message.segment_types
                    if kind not in ("text", "plain")
                }
            ),
        }
        context = self._context(messages) if self.store_text else None
        sender_id = messages[-1].sender_id if messages else ""
        return ShadowSample(features=features, context=context, sender_id=sender_id)

    def _bounded_messages(self, topic: TopicSnapshot) -> List[ChatMessage]:
        if not topic.messages:
            return []
        cutoff = topic.messages[-1].timestamp - 300
        recent = [message for message in topic.messages if message.timestamp >= cutoff]
        return recent[-self.max_messages :]

    @staticmethod
    def _context(messages: List[ChatMessage]):
        aliases: Dict[str, str] = {}
        rows = []
        for message in messages:
            if message.sender_id not in aliases:
                aliases[message.sender_id] = "成员{}".format(len(aliases) + 1)
            text = message.text[:300]
            if message.image_urls:
                text = (text + " [图片]").strip()
            rows.append(
                {
                    "message_id": message.message_id,
                    "sender": aliases[message.sender_id],
                    "text": text,
                    "timestamp": message.timestamp,
                    "reply": bool(message.reply_to_message_id),
                }
            )
        return rows
