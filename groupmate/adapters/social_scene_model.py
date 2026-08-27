"""Strict JSON adapter for semantic social scene classification."""

from __future__ import annotations

import json
from typing import Mapping, Protocol


MAX_SCENE_RESPONSE_CHARS = 12_000


class TextCompletionPort(Protocol):
    async def complete_text(self, *, system_prompt: str, prompt: str) -> str: ...


class SceneJsonModel:
    def __init__(self, model: TextCompletionPort) -> None:
        self._model = model

    @staticmethod
    def system_prompt() -> str:
        return (
            "你只负责识别当前群聊的具体社会场景，不负责写回复。"
            "返回一个 JSON 对象，包含 scene_kind、target_scope、target_id、"
            "literal_subject、user_move、continuity_event_ids、repetition_count、"
            "constraints、information_gaps、capability_request、confidence。"
            "如存在确定性复读证据，再判断 chorus_target（SELF、MEMBER、OTHER、UNKNOWN）、"
            "chorus_target_id 与 chorus_tone（SAFE_BANTER、SENSITIVE、ATTACK、"
            "DANGEROUS、UNKNOWN）。只能引用输入中已有的事件 ID 和成员 ID；"
            "不得生成复读链 ID、参与者列表或改写复读原文。不要输出解释或 Markdown。"
        )

    async def classify_scene(
        self, facts: Mapping[str, object]
    ) -> Mapping[str, object]:
        prompt = json.dumps(
            dict(facts), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        response = await self._model.complete_text(
            system_prompt=self.system_prompt(), prompt=prompt
        )
        text = str(response or "")
        if not text or len(text) > MAX_SCENE_RESPONSE_CHARS:
            raise ValueError("scene model response size is invalid")
        decoder = json.JSONDecoder()
        value, end = decoder.raw_decode(text.lstrip())
        if text.lstrip()[end:].strip():
            raise ValueError("scene model returned trailing content")
        if not isinstance(value, dict):
            raise ValueError("scene model must return one JSON object")
        return value


__all__ = ("SceneJsonModel",)
