"""Persona loading and bounded dynamic group-context composition."""

from __future__ import annotations

import html
from pathlib import Path
from typing import Sequence

from .models import MemoryItem, TopicSnapshot


class BundledPersonaProvider:
    def __init__(self, override_prompt: str = "") -> None:
        self.override_prompt = override_prompt.strip()

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
        message_lines = []
        for message in topic.messages[-20:]:
            content = message.text or "[图片]"
            if message.image_urls and message.text:
                content += " [图片]"
            message_lines.append(
                "{}: {}".format(
                    html.escape(message.sender_name or message.sender_id),
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
                "<runtime_rule>只回复当前仍在进行的话题，不解释内部决策。</runtime_rule>",
                "</group_context>",
            ]
        )
        return "\n".join(sections)

