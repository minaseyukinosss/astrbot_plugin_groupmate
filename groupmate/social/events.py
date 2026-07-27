"""确定性社会事件分类器（首期规则，无 LLM）。"""

from __future__ import annotations

import re
from typing import Optional

from ..models import ChatMessage, SocialEvent, SocialEventKind
from uuid import uuid4


_PATTERNS = (
    (SocialEventKind.HARASSMENT, re.compile(r"(傻逼|去死|滚|骚扰|摸摸|亲一下)", re.I)),
    (SocialEventKind.BOUNDARY_PUSH, re.compile(r"(老婆|老公|亲爱的|约会吗)", re.I)),
    (SocialEventKind.APOLOGY, re.compile(r"(对不起|抱歉|我错了|不好意思)", re.I)),
    (SocialEventKind.THANKS, re.compile(r"(谢谢|感谢|多谢|谢啦|thx|thanks)", re.I)),
    (SocialEventKind.PRAISE, re.compile(r"(厉害|真棒|好看|好强|太强了|牛逼|yyds)", re.I)),
    (SocialEventKind.HELP_REQUEST, re.compile(r"(帮我|怎么办|教我|怎么弄|求助)", re.I)),
    (SocialEventKind.HELPED, re.compile(r"(帮到了|有用|解决了|搞定了)", re.I)),
    (SocialEventKind.FRIENDLY_TEASE, re.compile(r"(哈哈你|你傻|逗你|开玩笑|损友)", re.I)),
    (SocialEventKind.CORRECTION, re.compile(r"(不对|你错了|更正|其实是)", re.I)),
)


class SocialEventClassifier:
    def classify(
        self,
        message: ChatMessage,
        *,
        user_id: str,
        soft_trigger: bool = False,
        decision_id: Optional[str] = None,
        occurred_at: Optional[int] = None,
    ) -> SocialEvent:
        text = message.text or ""
        kind = SocialEventKind.NEUTRAL
        confidence = 0.55
        for candidate, pattern in _PATTERNS:
            if pattern.search(text):
                kind = candidate
                confidence = 0.85
                break
        if kind is SocialEventKind.NEUTRAL and soft_trigger:
            confidence = 0.6
        return SocialEvent(
            event_id=uuid4().hex,
            group_id=message.group_id,
            user_id=str(user_id),
            kind=kind,
            source_message_id=message.message_id,
            confidence=confidence,
            occurred_at=int(occurred_at if occurred_at is not None else message.timestamp),
            decision_id=decision_id,
        )
