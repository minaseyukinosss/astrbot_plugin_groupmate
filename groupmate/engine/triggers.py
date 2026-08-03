"""Deterministic first-stage routing for incoming group messages.

Wake contract (open-source stable):

1. Platform ``@`` / reply-to-bot → ``NATIVE_DIRECT``
2. Leading plain-text ``@alias`` without a real At segment → ``COPIED_AT``
3. Message equals alias, or starts with alias → ``ALIAS_DIRECT``
4. Alias appears only mid/end sentence → ``ALIAS_MENTION``
5. Explicit summon verbs before alias (叫/喊/问问) → ``ALIAS_DIRECT``

Colloquial tails after a leading alias are not enumerated. In Chinese group chat,
a sentence-initial nickname is a vocative; mid-sentence mention is discussion.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from ..models import ChatMessage, MessageOrigin, TriggerKind


@dataclass(frozen=True)
class TriggerResult:
    kind: TriggerKind
    reason: str
    alias: str = ""


class TriggerRouter:
    """Classify whether a message is a direct wake, soft mention, or candidate."""

    _SUMMON_RE_TEMPLATE = r"(?:叫|喊|问问){alias}"

    def __init__(self, aliases: Sequence[str]) -> None:
        self.aliases = tuple(str(alias).strip() for alias in aliases if str(alias).strip())

    def classify(self, message: ChatMessage) -> TriggerResult:
        if message.is_bot or not message.has_content:
            return TriggerResult(TriggerKind.IGNORE, "ignored_sender_or_empty")
        if message.is_command:
            return TriggerResult(TriggerKind.COMMAND, "existing_command")
        if message.origin is MessageOrigin.SYSTEM_SYNTHETIC:
            kind = str(message.metadata.get("interaction_kind", "") or "")
            if kind == "poke" and message.segment_types == ("poke",):
                return TriggerResult(
                    TriggerKind.HOST_INTERACTION,
                    "host_interaction:poke",
                )
            return TriggerResult(TriggerKind.IGNORE, "invalid_host_interaction")
        if message.mentions_bot or message.reply_to_bot:
            return TriggerResult(TriggerKind.NATIVE_DIRECT, "native_direct")

        text = re.sub(r"\s+", "", message.text or "")
        if not text:
            return TriggerResult(TriggerKind.IGNORE, "ignored_sender_or_empty")

        for alias in sorted(self.aliases, key=len, reverse=True):
            alias = alias.strip()
            if not alias:
                continue
            if text.startswith("@" + alias) or text == "@" + alias:
                # Real platform At already returned NATIVE_DIRECT above.
                # Leading "@别名" here is almost always a copied plain-text At.
                return TriggerResult(TriggerKind.COPIED_AT, "copied_plain_at", alias)
            if self._is_prefix_address(text, alias):
                return TriggerResult(TriggerKind.ALIAS_DIRECT, "alias_direct", alias)
            if self._is_explicit_summon(text, alias):
                return TriggerResult(TriggerKind.ALIAS_DIRECT, "alias_summon", alias)
            if alias in text:
                return TriggerResult(TriggerKind.ALIAS_MENTION, "alias_mentioned", alias)

        return TriggerResult(TriggerKind.CANDIDATE, "ordinary_group_message")

    @staticmethod
    def _is_prefix_address(text: str, alias: str) -> bool:
        if text == alias:
            return True
        if text.startswith(alias):
            return True
        return False

    def _is_explicit_summon(self, text: str, alias: str) -> bool:
        pattern = self._SUMMON_RE_TEMPLATE.format(alias=re.escape(alias))
        return bool(re.search(pattern, text))
