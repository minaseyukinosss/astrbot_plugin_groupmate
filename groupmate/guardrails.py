"""Deterministic user-visible response constraints."""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import List, Sequence, Tuple


@dataclass(frozen=True)
class GuardResult:
    accepted: bool
    text: str
    codes: Tuple[str, ...]
    repairable: bool


class AemeathOutputGuard:
    _NARRATION = re.compile(
        r"[（(][^）)]*(?:不回复|沉默|没人叫|系统|决定|思考|旁白)[^）)]*[）)]",
        re.IGNORECASE,
    )
    _CUSTOMER_SERVICE = re.compile(
        r"(?:有什么(?:想聊|可以帮)|需要我帮|有什么事需要|请问需要|小爱在呢)",
        re.IGNORECASE,
    )
    _SYSTEM_VOCABULARY = re.compile(
        r"(?:prompt|system\s*prompt|模型输出|插件配置|人格调参|系统决定)",
        re.IGNORECASE,
    )
    _FORCED_FOLLOWUP = re.compile(
        r"(?:(?:你呢|那你呢|然后呢)[\s？?。！!~～.…]*$"
        r"|(?:^|[，,。！？!?；;：:.…\r\n])[^\S\r\n]*(?:怎么啦|怎么了)"
        r"[\s？?。！!~～.…]*$"
        r"|有什么想聊)",
        re.IGNORECASE,
    )
    _INTERNAL_ID = re.compile(r"(?:sender_id|user_id|internal_id|内部ID)", re.IGNORECASE)

    def __init__(self, max_chars: int = 60, max_sentences: int = 2) -> None:
        self.max_chars = max(1, max_chars)
        self.max_sentences = max(1, max_sentences)

    def validate(self, text: str, recent_outputs: Sequence[str]) -> GuardResult:
        cleaned = (text or "").strip().strip("`").strip()
        codes: List[str] = []
        non_repairable = set()

        if not cleaned:
            codes.append("empty_output")
            non_repairable.add("empty_output")
        if len(cleaned) > self.max_chars:
            codes.append("too_long")
        if self._sentence_count(cleaned) > self.max_sentences:
            codes.append("too_many_sentences")
        if self._NARRATION.search(cleaned):
            codes.append("decision_narration")
        if self._CUSTOMER_SERVICE.search(cleaned):
            codes.append("customer_service_template")
        if self._SYSTEM_VOCABULARY.search(cleaned):
            codes.append("system_vocabulary")
        if self._FORCED_FOLLOWUP.search(cleaned):
            codes.append("forced_followup")
        if self._INTERNAL_ID.search(cleaned):
            codes.append("internal_id_leak")
            non_repairable.add("internal_id_leak")
        if self._is_duplicate(cleaned, recent_outputs):
            codes.append("duplicate_output")
            non_repairable.add("duplicate_output")

        unique_codes = tuple(dict.fromkeys(codes))
        return GuardResult(
            accepted=not unique_codes,
            text=cleaned,
            codes=unique_codes,
            repairable=bool(unique_codes) and not bool(non_repairable),
        )

    @staticmethod
    def _sentence_count(text: str) -> int:
        chunks = [chunk for chunk in re.split(r"[。！？!?]+", text) if chunk.strip()]
        return len(chunks)

    def _is_duplicate(self, text: str, recent_outputs: Sequence[str]) -> bool:
        normalized = self._normalize(text)
        if not normalized:
            return False
        for previous in recent_outputs[-20:]:
            candidate = self._normalize(previous)
            if candidate and SequenceMatcher(None, normalized, candidate).ratio() >= 0.92:
                return True
        return False

    @staticmethod
    def _normalize(text: str) -> str:
        return re.sub(r"[\s，,。.!！?？~～]+", "", (text or "").lower())
