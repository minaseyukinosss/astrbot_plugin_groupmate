"""Deterministic evidence for short, multi-participant group chorus chains."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .persistence.schema import connect_database, initialize_database
from .social_context import SceneEventFact


_URL = re.compile(r"(?:https?://|www\.)", re.IGNORECASE)


def _required(value: object, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} must not be empty")
    return text


@dataclass(frozen=True)
class ChorusEvidence:
    chain_id: str
    payload: str
    normalized_key: str
    event_ids: tuple[str, ...]
    participant_ids: tuple[str, ...]
    already_joined: bool

    def __post_init__(self) -> None:
        for field in ("chain_id", "payload", "normalized_key"):
            object.__setattr__(self, field, _required(getattr(self, field), field))
        event_ids = tuple(dict.fromkeys(_required(value, "event_id") for value in self.event_ids))
        participant_ids = tuple(
            dict.fromkeys(_required(value, "participant_id") for value in self.participant_ids)
        )
        if len(event_ids) < 2 or len(participant_ids) < 2:
            raise ValueError("chorus evidence requires two events and participants")
        object.__setattr__(self, "event_ids", event_ids)
        object.__setattr__(self, "participant_ids", participant_ids)
        if not isinstance(self.already_joined, bool):
            raise ValueError("already_joined must be a boolean")


class ChorusDetector:
    ALLOWED_ORIGIN = "USER_TEXT"

    def __init__(self, *, window_seconds: int = 45, max_chars: int = 80) -> None:
        if int(window_seconds) < 1:
            raise ValueError("window_seconds must be positive")
        if int(max_chars) < 1:
            raise ValueError("max_chars must be positive")
        self._window_seconds = int(window_seconds)
        self._max_chars = int(max_chars)

    def detect(
        self,
        *,
        events: Iterable[SceneEventFact],
        source_event_id: str,
        group_id: str,
        persona_actor_id: str,
        joined_chain_ids: Iterable[str],
    ) -> ChorusEvidence | None:
        event_list = tuple(events)
        source = next(
            (item for item in event_list if item.event_id == source_event_id),
            None,
        )
        if source is None or not self._eligible(source, persona_actor_id):
            return None
        normalized_key = self._normalize_key(source.text)
        if not normalized_key:
            return None
        matching = tuple(
            sorted(
                (
                    item
                    for item in event_list
                    if self._eligible(item, persona_actor_id)
                    and 0 <= source.occurred_at - item.occurred_at <= self._window_seconds
                    and self._normalize_key(item.text) == normalized_key
                ),
                key=lambda item: (item.occurred_at, item.event_id),
            )
        )
        if source.event_id not in {item.event_id for item in matching}:
            return None
        participants = tuple(
            dict.fromkeys(item.actor_id for item in matching if item.actor_id)
        )
        if len(participants) < 2:
            return None
        chain_id = self._chain_id(group_id, normalized_key, matching[0].event_id)
        return ChorusEvidence(
            chain_id=chain_id,
            # 匹配时折叠空白，发送时保留当前成员实际输入的内部空白。
            payload=source.text.strip(),
            normalized_key=normalized_key,
            event_ids=tuple(item.event_id for item in matching),
            participant_ids=participants,
            already_joined=chain_id in set(joined_chain_ids),
        )

    def _eligible(self, event: SceneEventFact, persona_actor_id: str) -> bool:
        text = event.text.strip()
        return bool(
            event.actor_id
            and event.actor_id != persona_actor_id
            and event.origin_kind == self.ALLOWED_ORIGIN
            and event.parts == ("TEXT",)
            and 1 <= len(text) <= self._max_chars
            and not _URL.search(text)
        )

    @staticmethod
    def _normalize_key(value: str) -> str:
        return " ".join(unicodedata.normalize("NFKC", value).split())

    @staticmethod
    def _chain_id(group_id: str, normalized_key: str, first_event_id: str) -> str:
        canonical = json.dumps(
            {
                "first_event_id": _required(first_event_id, "first_event_id"),
                "group_id": _required(group_id, "group_id"),
                "normalized_key": normalized_key,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
        return f"chorus:{digest}"


class ChorusParticipationRepository:
    """Persist only successfully sent chorus participation, scoped by group."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        initialize_database(self.path)
        with connect_database(self.path) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS social_chorus_participation ("
                "group_id TEXT NOT NULL, chain_id TEXT NOT NULL, "
                "joined_at INTEGER NOT NULL, PRIMARY KEY(group_id, chain_id))"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_social_chorus_joined_at "
                "ON social_chorus_participation(group_id, joined_at)"
            )

    def mark_joined(self, group_id: str, chain_id: str, *, joined_at: int) -> None:
        group = _required(group_id, "group_id")
        chain = _required(chain_id, "chain_id")
        timestamp = int(joined_at)
        if timestamp < 0:
            raise ValueError("joined_at must not be negative")
        with connect_database(self.path) as db:
            db.execute(
                "INSERT INTO social_chorus_participation(group_id, chain_id, joined_at) "
                "VALUES(?, ?, ?) ON CONFLICT(group_id, chain_id) DO NOTHING",
                (group, chain, timestamp),
            )

    def recent_joined_chain_ids(
        self,
        group_id: str,
        *,
        since: int,
        limit: int = 128,
    ) -> tuple[str, ...]:
        group = _required(group_id, "group_id")
        with connect_database(self.path) as db:
            rows = db.execute(
                "SELECT chain_id FROM social_chorus_participation "
                "WHERE group_id=? AND joined_at>=? "
                "ORDER BY joined_at, chain_id LIMIT ?",
                (group, max(0, int(since)), max(1, int(limit))),
            ).fetchall()
        return tuple(str(row["chain_id"]) for row in rows)

    def prune(self, *, before: int) -> int:
        with connect_database(self.path) as db:
            cursor = db.execute(
                "DELETE FROM social_chorus_participation WHERE joined_at<?",
                (max(0, int(before)),),
            )
            return int(cursor.rowcount)


__all__ = (
    "ChorusDetector",
    "ChorusEvidence",
    "ChorusParticipationRepository",
)
