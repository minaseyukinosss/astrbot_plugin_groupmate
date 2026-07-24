"""自我情景：用户回指「你之前说过」时从 Session 召回。"""

from __future__ import annotations

import html
import re
from typing import Optional, Sequence

from .session import DialogueTurn, GroupSession

SELF_RECALL_RE = re.compile(
    r"(你之前|你上次|你不是说|你说过|你还记得|你刚才说|你答应)"
)


def needs_self_recall(query: str) -> bool:
    return bool(SELF_RECALL_RE.search(query or ""))


def format_self_episodes(
    session: Optional[GroupSession],
    *,
    limit: int = 3,
) -> str:
    if session is None:
        return ""
    turns: Sequence[DialogueTurn] = session.recent_turns(12)
    assistant = [turn for turn in turns if turn.role == "assistant"][-limit:]
    if not assistant:
        return ""
    lines = [
        html.escape(f"{turn.speaker}：{turn.text[:200]}") for turn in assistant
    ]
    return "\n".join(
        [
            "<self_episodes>",
            "你最近说过：",
            "\n".join(lines),
            "若对方在回指这些话，要接得上；不要否认自己刚说过的内容。",
            "</self_episodes>",
        ]
    )
