"""Direct-call pressure derived only from repeated real direct addresses."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Sequence, Tuple

from ..models import ChatMessage, StringEnum, TriggerKind

_CONTENTFUL = re.compile(
    r"[？?]|吗$|呢$|怎么|什么|谁|哪|为什么|如何|帮|看|查|找|"
    r"生成|解释|翻译|设置|删除|发送|执行|处理"
)
_ALIAS_PADDING = re.compile(r"[\s@＠,，。.!！?？~～:：、]+")


class DirectAddressPressureLevel(StringEnum):
    """DirectAddressPressureLevel（直接呼叫压力档位）。"""

    NORMAL = "normal"
    NUDGE = "nudge"
    PESTER = "pester"
    AFTER_BOUNDARY = "after_boundary"


@dataclass(frozen=True)
class DirectAddressPressureState:
    """DirectAddressPressureState（直接呼叫压力状态）。"""

    level: DirectAddressPressureLevel
    count: int = 0
    reason_codes: Tuple[str, ...] = ()


class DirectAddressPressureTracker:
    """DirectAddressPressureTracker（直接呼叫压力跟踪器）。"""

    def __init__(
        self,
        *,
        window_seconds: int = 600,
        nudge_count: int = 2,
        pester_count: int = 3,
    ) -> None:
        self.window_seconds = max(1, int(window_seconds))
        self.nudge_count = max(2, int(nudge_count))
        self.pester_count = max(self.nudge_count + 1, int(pester_count))
        self._events: Dict[Tuple[str, str, str], Tuple[int, ...]] = {}

    def configure(
        self,
        *,
        window_seconds: int,
        nudge_count: int,
        pester_count: int,
    ) -> None:
        """configure（重新配置）：应用当前群策略的压力阈值。"""

        self.window_seconds = max(1, int(window_seconds))
        self.nudge_count = max(2, int(nudge_count))
        self.pester_count = max(
            self.nudge_count + 1,
            int(pester_count),
        )

    def observe(
        self,
        persona_id: str,
        message: ChatMessage,
        trigger: TriggerKind,
        *,
        now: int,
        aliases: Sequence[str],
    ) -> DirectAddressPressureState:
        """observe（观察直接呼叫）：投影当前用户的压力档位。"""

        persona_id = str(persona_id or "").strip()
        if not persona_id:
            raise ValueError("persona_id must not be empty")
        key = (persona_id, message.group_id, message.sender_id)
        if trigger is TriggerKind.NATIVE_DIRECT and message.reply_to_bot:
            return DirectAddressPressureState(
                DirectAddressPressureLevel.NORMAL,
                0,
                ("pressure_excluded_reply",),
            )
        if not self._counts_as_direct_at(message, trigger):
            return DirectAddressPressureState(
                DirectAddressPressureLevel.NORMAL,
                0,
                ("pressure_excluded", trigger.value),
            )
        if self._has_content(message.text, aliases):
            self._events.pop(key, None)
            return DirectAddressPressureState(
                DirectAddressPressureLevel.NORMAL,
                0,
                ("pressure_reset_contentful",),
            )

        cutoff = int(now) - self.window_seconds
        timestamps = tuple(
            value for value in self._events.get(key, ()) if value >= cutoff
        ) + (int(now),)
        self._events[key] = timestamps
        count = len(timestamps)
        if (
            trigger is TriggerKind.HOST_INTERACTION
            and count >= self.pester_count + 2
        ):
            return DirectAddressPressureState(
                DirectAddressPressureLevel.AFTER_BOUNDARY,
                count,
                ("pressure_after_boundary", "poke_spam"),
            )
        if count >= self.pester_count:
            return DirectAddressPressureState(
                DirectAddressPressureLevel.PESTER,
                count,
                ("pressure_pester",)
                + (("poke_spam",) if trigger is TriggerKind.HOST_INTERACTION else ()),
            )
        if count >= self.nudge_count:
            return DirectAddressPressureState(
                DirectAddressPressureLevel.NUDGE,
                count,
                ("pressure_nudge",),
            )
        return DirectAddressPressureState(
            DirectAddressPressureLevel.NORMAL,
            count,
            ("pressure_normal",),
        )

    @staticmethod
    def _counts_as_direct_at(
        message: ChatMessage,
        trigger: TriggerKind,
    ) -> bool:
        if trigger is TriggerKind.HOST_INTERACTION:
            role = str(message.metadata.get("poke_role", "") or "").strip().lower()
            if role == "bystander":
                return False
            return (
                str(message.metadata.get("interaction_kind", "") or "").lower()
                == "poke"
            )
        if trigger is TriggerKind.ALIAS_DIRECT:
            return True
        return bool(
            trigger is TriggerKind.NATIVE_DIRECT
            and message.mentions_bot
            and not message.reply_to_bot
        )

    @staticmethod
    def _has_content(text: str, aliases: Sequence[str]) -> bool:
        cleaned = str(text or "").strip()
        if not cleaned:
            return False
        compact = _ALIAS_PADDING.sub("", cleaned).casefold()
        normalized_aliases = {
            _ALIAS_PADDING.sub("", str(alias or "")).casefold()
            for alias in aliases or ()
            if str(alias or "").strip()
        }
        if compact in normalized_aliases:
            return False
        return bool(_CONTENTFUL.search(cleaned) or len(compact) > 8)
