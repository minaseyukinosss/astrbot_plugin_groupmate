"""Deterministic first-stage routing for incoming group messages."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import ChatMessage, GroupPolicy, TriggerKind


@dataclass(frozen=True)
class TriggerResult:
    kind: TriggerKind
    reason: str
    alias: str = ""


class TriggerRouter:
    _DIRECT_PUNCTUATION = "，,。.!！?？:：~～、"
    _DIRECT_TAILS = (
        "在吗",
        "在不在",
        "干嘛",
        "出来",
        "看看",
        "听我说",
        "你觉得",
        "你怎么看",
        "帮我",
        "说句话",
        "醒醒",
    )

    def __init__(self, policy: GroupPolicy) -> None:
        self.policy = policy

    def classify(self, message: ChatMessage) -> TriggerResult:
        if message.is_bot or not message.has_content:
            return TriggerResult(TriggerKind.IGNORE, "ignored_sender_or_empty")
        if message.is_command:
            return TriggerResult(TriggerKind.COMMAND, "existing_command")
        if message.mentions_bot or message.reply_to_bot:
            return TriggerResult(TriggerKind.NATIVE_DIRECT, "native_direct")

        text = re.sub(r"\s+", "", message.text)
        for alias in sorted(self.policy.aliases, key=len, reverse=True):
            alias = alias.strip()
            if not alias or alias not in text:
                continue
            if self._is_direct_address(text, alias):
                return TriggerResult(TriggerKind.ALIAS_DIRECT, "alias_direct", alias)
            return TriggerResult(TriggerKind.ALIAS_MENTION, "alias_mentioned", alias)

        return TriggerResult(TriggerKind.CANDIDATE, "ordinary_group_message")

    def _is_direct_address(self, text: str, alias: str) -> bool:
        if text == alias:
            return True
        if text.startswith("@" + alias):
            return True
        if text.startswith(alias):
            tail = text[len(alias) :]
            if not tail:
                return True
            if tail[0] in self._DIRECT_PUNCTUATION:
                return True
            if any(tail.startswith(cue) for cue in self._DIRECT_TAILS):
                return True
        return bool(re.search(r"(?:叫|喊|问问)" + re.escape(alias), text))

