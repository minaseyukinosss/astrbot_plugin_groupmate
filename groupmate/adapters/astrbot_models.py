"""Small AstrBot model boundary used by cognition and reply generation."""

from __future__ import annotations

import json
from typing import Mapping


class AstrBotModelPort:
    def __init__(self, context: object, provider_id: str) -> None:
        provider = str(provider_id).strip()
        if not provider:
            raise ValueError("AstrBot model provider must not be empty")
        self.context = context
        self.provider_id = provider

    async def complete_json(
        self, *, schema: Mapping[str, object], payload: Mapping[str, object]
    ) -> object:
        prompt = json.dumps(
            {"schema": dict(schema), "input": dict(payload)},
            ensure_ascii=False,
            sort_keys=True,
        )
        response = await self.context.llm_generate(
            chat_provider_id=self.provider_id,
            system_prompt=(
                "你是 Groupmate 的结构化群聊观察器。只根据输入事实输出符合 "
                "schema 的 JSON，不输出推理过程、Markdown 或额外说明。"
            ),
            prompt=prompt,
            temperature=0.1,
        )
        return json.loads(self._response_text(response))

    async def complete_text(self, *, system_prompt: str, prompt: str) -> str:
        response = await self.context.llm_generate(
            chat_provider_id=self.provider_id,
            system_prompt=str(system_prompt),
            prompt=str(prompt),
            temperature=0.7,
        )
        return self._response_text(response)

    @staticmethod
    def _response_text(response: object) -> str:
        text = str(getattr(response, "completion_text", "") or "").strip()
        if text.startswith("```") and text.endswith("```"):
            first_newline = text.find("\n")
            if first_newline >= 0:
                text = text[first_newline + 1 : -3].strip()
        if not text:
            raise ValueError("AstrBot model returned empty content")
        return text


__all__ = ("AstrBotModelPort",)
