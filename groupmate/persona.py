"""Persona loading and bounded dynamic group-context composition."""

from __future__ import annotations

import html
from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple

from .models import MemoryItem, TopicSnapshot
from .relationships import (
    DEFAULT_RELATIONSHIPS,
    RelationshipEntry,
    relationship_map,
    resolve_speaker,
)
from .topics import select_active_messages


class BundledPersonaProvider:
    def __init__(
        self,
        override_prompt: str = "",
        relationships: Optional[Sequence[RelationshipEntry]] = None,
    ) -> None:
        self.override_prompt = override_prompt.strip()
        entries = tuple(relationships) if relationships is not None else DEFAULT_RELATIONSHIPS
        self._relationships: Dict[str, Tuple[str, str]] = relationship_map(entries)

    def set_relationships(self, relationships: Sequence[RelationshipEntry]) -> None:
        self._relationships = relationship_map(relationships)

    def _speaker_context(self, sender_id: str, sender_name: str) -> Tuple[str, str, str]:
        return resolve_speaker(sender_id, sender_name, self._relationships)

    def bundled_system_prompt(self) -> str:
        path = Path(__file__).resolve().parent.parent / "resources" / "aemeath_persona.md"
        return path.read_text(encoding="utf-8").strip()

    async def system_prompt(self, group_id: str) -> str:
        del group_id
        return self.override_prompt or self.bundled_system_prompt()

    def build_user_context(
        self,
        topic: TopicSnapshot,
        memories: Sequence[MemoryItem],
    ) -> str:
        active = select_active_messages(
            topic.messages, topic_created_at=topic.created_at
        )
        message_lines = []
        for message in active:
            content = message.text or "[图片]"
            if message.image_urls and message.text:
                content += " [图片]"
            speaker, relationship, suggested_address = self._speaker_context(
                message.sender_id, message.sender_name
            )
            message_lines.append(
                '<message speaker="{}" relationship="{}" '
                'suggested_address="{}">{}</message>'.format(
                    html.escape(speaker),
                    html.escape(relationship),
                    html.escape(suggested_address),
                    html.escape(content[:300]),
                )
            )

        memory_lines = [html.escape(item.text[:300]) for item in memories[:8]]
        sections = [
            "<group_context>",
            "<recent_messages>",
            "\n".join(message_lines),
            "</recent_messages>",
        ]
        if memory_lines:
            sections.extend(
                ["<relevant_memories>", "\n".join(memory_lines), "</relevant_memories>"]
            )
        sections.extend(
            [
                "<runtime_rule>"
                "只回复当前活跃话题（recent_messages 内最近一段），"
                "不要串联更早的无关讨论；不解释内部决策。"
                "</runtime_rule>",
                "</group_context>",
            ]
        )
        return "\n".join(sections)
