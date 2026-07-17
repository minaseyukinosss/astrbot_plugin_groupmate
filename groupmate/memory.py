"""SQLite persistence for bounded chat context and social memory."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .models import ChatMessage, MemoryItem, MemoryKind


SCHEMA_VERSION = 1


class SQLiteMemoryStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(self.path))
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.execute("PRAGMA journal_mode = WAL")
        self._migrate()

    def _migrate(self) -> None:
        with self._db:
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS messages (
                    group_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    sender_id TEXT NOT NULL,
                    sender_name TEXT NOT NULL,
                    text TEXT NOT NULL,
                    timestamp INTEGER NOT NULL,
                    reply_to_message_id TEXT,
                    reply_to_bot INTEGER NOT NULL,
                    mentions_bot INTEGER NOT NULL,
                    is_bot INTEGER NOT NULL,
                    is_command INTEGER NOT NULL,
                    image_urls TEXT NOT NULL,
                    segment_types TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    PRIMARY KEY (group_id, message_id)
                );
                CREATE INDEX IF NOT EXISTS idx_messages_group_time
                    ON messages(group_id, timestamp DESC);

                CREATE TABLE IF NOT EXISTS profiles (
                    group_id TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    relationship TEXT NOT NULL,
                    authority INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (group_id, subject_id)
                );

                CREATE TABLE IF NOT EXISTS memories (
                    memory_id TEXT PRIMARY KEY,
                    group_id TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    text TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER,
                    confidence REAL NOT NULL,
                    importance REAL NOT NULL,
                    authority INTEGER NOT NULL,
                    source_message_id TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_memories_group_subject
                    ON memories(group_id, subject_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    decision_id TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    timestamp INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_decisions_id
                    ON decisions(decision_id, id);

                CREATE TABLE IF NOT EXISTS outbox (
                    decision_id TEXT PRIMARY KEY,
                    group_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER,
                    sent_at INTEGER
                );
                """
            )
            self._db.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('version', ?)",
                (str(SCHEMA_VERSION),),
            )

    def save_message(self, message: ChatMessage) -> bool:
        with self._db:
            cursor = self._db.execute(
                """
                INSERT OR IGNORE INTO messages(
                    group_id, message_id, sender_id, sender_name, text, timestamp,
                    reply_to_message_id, reply_to_bot, mentions_bot, is_bot,
                    is_command, image_urls, segment_types, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.group_id,
                    message.message_id,
                    message.sender_id,
                    message.sender_name,
                    message.text,
                    message.timestamp,
                    message.reply_to_message_id,
                    int(message.reply_to_bot),
                    int(message.mentions_bot),
                    int(message.is_bot),
                    int(message.is_command),
                    json.dumps(message.image_urls, ensure_ascii=False),
                    json.dumps(message.segment_types, ensure_ascii=False),
                    json.dumps(message.metadata, ensure_ascii=False, default=str),
                ),
            )
        return cursor.rowcount == 1

    def recent_messages(self, group_id: str, limit: int) -> List[ChatMessage]:
        rows = self._db.execute(
            """
            SELECT * FROM messages
            WHERE group_id = ?
            ORDER BY timestamp DESC, rowid DESC
            LIMIT ?
            """,
            (str(group_id), max(0, int(limit))),
        ).fetchall()
        return [self._row_to_message(row) for row in reversed(rows)]

    def _row_to_message(self, row: sqlite3.Row) -> ChatMessage:
        return ChatMessage(
            message_id=row["message_id"],
            group_id=row["group_id"],
            sender_id=row["sender_id"],
            sender_name=row["sender_name"],
            text=row["text"],
            timestamp=row["timestamp"],
            reply_to_message_id=row["reply_to_message_id"],
            reply_to_bot=bool(row["reply_to_bot"]),
            mentions_bot=bool(row["mentions_bot"]),
            is_bot=bool(row["is_bot"]),
            is_command=bool(row["is_command"]),
            image_urls=tuple(json.loads(row["image_urls"])),
            segment_types=tuple(json.loads(row["segment_types"])),
            metadata=json.loads(row["metadata"]),
        )

    def upsert_profile(
        self,
        group_id: str,
        subject_id: str,
        display_name: str,
        relationship: str,
        authority: int,
        updated_at: int = 0,
    ) -> bool:
        existing = self._db.execute(
            "SELECT authority FROM profiles WHERE group_id = ? AND subject_id = ?",
            (str(group_id), str(subject_id)),
        ).fetchone()
        if existing and int(existing["authority"]) > int(authority):
            return False
        with self._db:
            self._db.execute(
                """
                INSERT INTO profiles(
                    group_id, subject_id, display_name, relationship, authority, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(group_id, subject_id) DO UPDATE SET
                    display_name = excluded.display_name,
                    relationship = excluded.relationship,
                    authority = excluded.authority,
                    updated_at = excluded.updated_at
                """,
                (
                    str(group_id),
                    str(subject_id),
                    display_name.strip(),
                    relationship.strip(),
                    int(authority),
                    int(updated_at),
                ),
            )
        return True

    def get_profile(self, group_id: str, subject_id: str) -> Optional[Dict[str, Any]]:
        row = self._db.execute(
            "SELECT * FROM profiles WHERE group_id = ? AND subject_id = ?",
            (str(group_id), str(subject_id)),
        ).fetchone()
        return dict(row) if row else None

    def add_memory(self, memory: MemoryItem) -> None:
        with self._db:
            self._db.execute(
                """
                INSERT OR REPLACE INTO memories(
                    memory_id, group_id, subject_id, kind, text, created_at,
                    expires_at, confidence, importance, authority, source_message_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    memory.memory_id,
                    memory.group_id,
                    memory.subject_id,
                    memory.kind.value,
                    memory.text.strip(),
                    memory.created_at,
                    memory.expires_at,
                    max(0.0, min(1.0, memory.confidence)),
                    max(0.0, min(1.0, memory.importance)),
                    memory.authority,
                    memory.source_message_id,
                ),
            )

    def search_memories(
        self,
        group_id: str,
        query: str,
        now: int,
        limit: int,
        subject_id: Optional[str] = None,
    ) -> List[MemoryItem]:
        sql = (
            "SELECT * FROM memories WHERE group_id = ? "
            "AND (expires_at IS NULL OR expires_at > ?)"
        )
        params: List[Any] = [str(group_id), int(now)]
        if subject_id is not None:
            sql += " AND subject_id = ?"
            params.append(str(subject_id))
        rows = self._db.execute(sql, tuple(params)).fetchall()
        query_tokens = self._tokens(query)
        ranked: List[Tuple[float, MemoryItem]] = []
        for row in rows:
            item = self._row_to_memory(row)
            item_tokens = self._tokens(item.text)
            overlap = (
                len(query_tokens & item_tokens) / max(1, len(query_tokens))
                if query_tokens
                else 0.0
            )
            age = max(0, int(now) - item.created_at)
            recency = max(0.0, 1.0 - age / (30 * 24 * 3600.0))
            authority = min(max(item.authority, 0), 10) / 10.0
            score = (
                overlap * 0.5
                + item.importance * 0.2
                + item.confidence * 0.15
                + authority * 0.1
                + recency * 0.05
            )
            if overlap > 0 or not query_tokens:
                ranked.append((score, item))
        ranked.sort(key=lambda pair: (pair[0], pair[1].created_at), reverse=True)
        return [item for _, item in ranked[: max(0, int(limit))]]

    def _row_to_memory(self, row: sqlite3.Row) -> MemoryItem:
        return MemoryItem(
            memory_id=row["memory_id"],
            group_id=row["group_id"],
            subject_id=row["subject_id"],
            kind=MemoryKind(row["kind"]),
            text=row["text"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            confidence=row["confidence"],
            importance=row["importance"],
            authority=row["authority"],
            source_message_id=row["source_message_id"],
        )

    @staticmethod
    def _tokens(text: str) -> set:
        lowered = (text or "").lower()
        latin = set(re.findall(r"[a-z0-9_]+", lowered))
        chinese = re.findall(r"[\u4e00-\u9fff]", lowered)
        latin.update(chinese)
        latin.update("".join(pair) for pair in zip(chinese, chinese[1:]))
        return latin

    def record_transition(
        self,
        decision_id: str,
        group_id: str,
        state: str,
        reason: str,
        timestamp: int,
    ) -> None:
        with self._db:
            self._db.execute(
                """
                INSERT INTO decisions(decision_id, group_id, state, reason, timestamp)
                VALUES (?, ?, ?, ?, ?)
                """,
                (decision_id, group_id, state, reason, int(timestamp)),
            )

    def enqueue_outbox(
        self,
        decision_id: str,
        group_id: str,
        text: str,
        created_at: int,
        expires_at: Optional[int] = None,
    ) -> bool:
        with self._db:
            cursor = self._db.execute(
                """
                INSERT OR IGNORE INTO outbox(
                    decision_id, group_id, text, created_at, expires_at, sent_at
                ) VALUES (?, ?, ?, ?, ?, NULL)
                """,
                (decision_id, group_id, text, int(created_at), expires_at),
            )
        return cursor.rowcount == 1

    def pending_outbox(self, now: int) -> List[Dict[str, Any]]:
        rows = self._db.execute(
            """
            SELECT * FROM outbox
            WHERE sent_at IS NULL AND (expires_at IS NULL OR expires_at > ?)
            ORDER BY created_at ASC
            """,
            (int(now),),
        ).fetchall()
        return [dict(row) for row in rows]

    def mark_outbox_sent(self, decision_id: str, sent_at: int) -> None:
        with self._db:
            self._db.execute(
                "UPDATE outbox SET sent_at = ? WHERE decision_id = ?",
                (int(sent_at), decision_id),
            )

    def close(self) -> None:
        self._db.close()

