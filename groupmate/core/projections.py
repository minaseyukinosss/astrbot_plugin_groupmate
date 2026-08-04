"""Rebuild in-memory projections from the MessageLedger."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple
from uuid import uuid4

from ..engine.rate_limit import SlidingWindowRateLimiter
from ..engine.topics import TopicWindow
from ..models import ChatMessage, MessageOrigin
from ..policies import ConversationPolicy
from .session import DialogueTurn, GroupSession


@dataclass(frozen=True)
class ContinuationProjection:
    sender_id: str
    expires_at: int
    grant_id: str = ""
    absolute_deadline_at: int = 0


@dataclass(frozen=True)
class ProjectionSnapshot:
    persona_id: str
    group_id: str
    topic_id: str
    topic_opened_at: int
    topic_updated_at: int
    messages: Tuple[ChatMessage, ...]
    session_turns: Tuple[DialogueTurn, ...]
    continuation: Optional[ContinuationProjection]
    recent_outputs: Tuple[str, ...]
    spontaneous_sent_at: Tuple[int, ...]
    last_bot_speak_at: Optional[int]
    rebuilt_at: int
    continuations: Tuple[ContinuationProjection, ...] = ()


class StateProjector:
    def __init__(self, store: Any, character_name: str = "角色") -> None:
        self.store = store
        self.character_name = (character_name or "角色").strip() or "角色"

    def rebuild(
        self,
        persona_id: str,
        group_id: str,
        *,
        now: int,
        policy: ConversationPolicy,
    ) -> ProjectionSnapshot:
        persona_id = str(persona_id or "").strip()
        if not persona_id:
            raise ValueError("persona_id is required")
        group_id = str(group_id)
        limit = max(1, int(policy.history_limit))
        messages = tuple(
            self.store.list_ledger_messages(persona_id, group_id, limit=limit)
        )
        epoch = self.store.latest_open_topic_epoch(persona_id, group_id)

        if epoch:
            topic_id = str(epoch["topic_id"])
            opened_at = int(epoch["opened_at"] or 0)
            topic_messages = tuple(
                item for item in messages if int(item.timestamp) >= opened_at
            )
        else:
            topic_id = uuid4().hex
            opened_at = messages[0].timestamp if messages else 0
            topic_messages = messages[-min(len(messages), limit) :]

        updated_at = (
            max(item.timestamp for item in topic_messages) if topic_messages else opened_at
        )
        session_turns = self._session_turns(messages, max_turns=12)
        continuations = self._continuations(persona_id, group_id, now)
        continuation = continuations[-1] if continuations else None
        bot_deliveries = tuple(
            self.store.list_bot_deliveries(persona_id, group_id, limit=20)
        )
        recent_outputs = tuple(
            item.text for item in bot_deliveries if (item.text or "").strip()
        )
        spontaneous = tuple(
            self.store.list_spontaneous_sent_at(persona_id, group_id, now - 3600)
        )
        last_bot = bot_deliveries[-1].timestamp if bot_deliveries else None
        return ProjectionSnapshot(
            persona_id=persona_id,
            group_id=group_id,
            topic_id=topic_id,
            topic_opened_at=opened_at,
            topic_updated_at=updated_at,
            messages=topic_messages,
            session_turns=session_turns,
            continuation=continuation,
            recent_outputs=recent_outputs[-20:],
            spontaneous_sent_at=spontaneous,
            last_bot_speak_at=last_bot,
            rebuilt_at=int(now),
            continuations=continuations,
        )

    def apply(
        self,
        snapshot: ProjectionSnapshot,
        *,
        window: TopicWindow,
        session: GroupSession,
        rate_limiter: SlidingWindowRateLimiter,
        workflow: Any,
        set_continuation,
    ) -> None:
        window.hydrate(
            topic_id=snapshot.topic_id,
            created_at=snapshot.topic_opened_at,
            updated_at=snapshot.topic_updated_at,
            messages=snapshot.messages,
        )
        session.hydrate(snapshot.session_turns)
        rate_limiter.replace(snapshot.spontaneous_sent_at, now=snapshot.rebuilt_at)
        workflow.hydrate_recent_outputs(
            snapshot.group_id,
            snapshot.recent_outputs,
        )
        set_continuation("", 0)
        continuations = snapshot.continuations
        if not continuations and snapshot.continuation is not None:
            continuations = (snapshot.continuation,)
        for continuation in continuations:
            set_continuation(
                continuation.sender_id,
                continuation.expires_at,
            )

    def _session_turns(
        self, messages: Sequence[ChatMessage], max_turns: int
    ) -> Tuple[DialogueTurn, ...]:
        turns: List[DialogueTurn] = []
        for message in messages:
            if message.is_command:
                continue
            if message.origin is MessageOrigin.SYSTEM_SYNTHETIC:
                continue
            text = (message.text or "").strip()
            if not text and "poke" in tuple(message.segment_types or ()):
                target = str(message.metadata.get("poke_target_id", "") or "").strip()
                if message.is_bot or message.origin is MessageOrigin.BOT_DELIVERY:
                    text = "戳了戳 {}".format(target or "某人")
            if not text:
                continue
            if message.is_bot or message.origin is MessageOrigin.BOT_DELIVERY:
                if message.origin is not MessageOrigin.BOT_DELIVERY:
                    continue
                turns.append(
                    DialogueTurn(
                        role="assistant",
                        speaker=message.sender_name or self.character_name,
                        text=text[:300],
                        timestamp=int(message.timestamp),
                        speaker_id=str(message.sender_id or ""),
                        source_message_id=str(message.message_id or ""),
                    )
                )
            else:
                turns.append(
                    DialogueTurn(
                        role="user",
                        speaker=(message.sender_name or "群友").strip() or "群友",
                        text=text[:300],
                        timestamp=int(message.timestamp),
                        speaker_id=str(message.sender_id or ""),
                        source_message_id=str(message.message_id or ""),
                    )
                )
        return tuple(turns[-max(2, int(max_turns)) :])

    def _continuation(
        self, persona_id: str, group_id: str, now: int
    ) -> Optional[ContinuationProjection]:
        continuations = self._continuations(persona_id, group_id, now)
        return continuations[-1] if continuations else None

    def _continuations(
        self, persona_id: str, group_id: str, now: int
    ) -> Tuple[ContinuationProjection, ...]:
        rows = self.store.list_active_continuation_grants(
            persona_id,
            group_id,
            now,
        )
        return tuple(
            ContinuationProjection(
                sender_id=str(row["sender_id"]),
                expires_at=min(
                    int(row["expires_at"]),
                    int(row["absolute_deadline_at"]),
                ),
                grant_id=str(row["grant_id"]),
                absolute_deadline_at=int(row["absolute_deadline_at"]),
            )
            for row in rows
        )
