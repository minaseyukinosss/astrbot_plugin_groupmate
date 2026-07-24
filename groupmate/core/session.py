"""群共享近轮对话 Session（AI Core 群 Session 的轻量子集）。"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, Iterable, List, Optional, Tuple


@dataclass(frozen=True)
class DialogueTurn:
    """一轮对模型可见的对话片段。"""

    role: str  # "user" | "assistant"
    speaker: str
    text: str
    timestamp: int


class GroupSession:
    """每群一个会话：记住自己刚说过的话，供续聊装配。"""

    def __init__(
        self,
        group_id: str,
        max_turns: int = 12,
        character_name: str = "角色",
    ) -> None:
        self.group_id = str(group_id)
        self.max_turns = max(2, int(max_turns))
        self.character_name = (character_name or "角色").strip() or "角色"
        self._turns: Deque[DialogueTurn] = deque(maxlen=self.max_turns)

    def clear(self) -> None:
        self._turns.clear()

    def append_user(
        self,
        speaker: str,
        text: str,
        timestamp: int,
    ) -> None:
        cleaned = (text or "").strip()
        if not cleaned:
            return
        self._turns.append(
            DialogueTurn(
                role="user",
                speaker=(speaker or "群友").strip() or "群友",
                text=cleaned[:300],
                timestamp=int(timestamp),
            )
        )

    def append_assistant(
        self,
        text: str,
        timestamp: int,
        speaker: Optional[str] = None,
    ) -> None:
        cleaned = (text or "").strip()
        if not cleaned:
            return
        name = (speaker or self.character_name).strip() or self.character_name
        self._turns.append(
            DialogueTurn(
                role="assistant",
                speaker=name,
                text=cleaned[:300],
                timestamp=int(timestamp),
            )
        )

    def recent_turns(self, limit: int = 6) -> Tuple[DialogueTurn, ...]:
        if limit <= 0:
            return ()
        items: List[DialogueTurn] = list(self._turns)
        return tuple(items[-limit:])

    def __len__(self) -> int:
        return len(self._turns)


class GroupSessionStore:
    """按 group_id 懒创建 Session。"""

    def __init__(
        self,
        max_turns: int = 12,
        character_name: str = "角色",
    ) -> None:
        self.max_turns = max_turns
        self.character_name = (character_name or "角色").strip() or "角色"
        self._sessions: Dict[str, GroupSession] = {}

    def get(self, group_id: str) -> GroupSession:
        key = str(group_id)
        session = self._sessions.get(key)
        if session is None:
            session = GroupSession(
                key,
                max_turns=self.max_turns,
                character_name=self.character_name,
            )
            self._sessions[key] = session
        return session

    def reset(self, group_id: str) -> None:
        key = str(group_id)
        if key in self._sessions:
            self._sessions[key].clear()

    def clear_all(self) -> None:
        self._sessions.clear()

    def values(self) -> Iterable[GroupSession]:
        return self._sessions.values()
