"""SQLite persistence for bounded chat context and social memory."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from uuid import uuid4

from ..models import (
    CandidateStatus,
    ChatMessage,
    MemoryCandidate,
    MemoryItem,
    MemoryKind,
    MemoryScope,
    MemoryStatus,
    MessageOrigin,
    RelationshipState,
    Sensitivity,
    SocialEvent,
    SocialEventKind,
)
from .migrations import SCHEMA_VERSION, migrate_database
from .privacy import claim_hash
from . import retrieval as memory_retrieval
from .writer import SQLiteWriteWorker


class SQLiteMemoryStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        migrate_database(self.path)
        self._db = sqlite3.connect(str(self.path))
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.execute("PRAGMA journal_mode = WAL")
        self._db.execute("PRAGMA busy_timeout = 5000")
        self._writer = SQLiteWriteWorker(self.path)
        self._write(
            lambda db: db.execute(
                "UPDATE outbox SET status='unknown', failure_code='startup_recovery' "
                "WHERE status='sending'"
            )
        )

    def _write(self, operation):
        return self._writer.execute(operation)

    async def _write_async(self, operation):
        return await self._writer.execute_async(operation)

    def schema_version(self) -> int:
        row = self._db.execute(
            "SELECT value FROM schema_meta WHERE key = 'version'"
        ).fetchone()
        return int(row["value"]) if row else 0

    @staticmethod
    def _message_params(message: ChatMessage):
        origin = message.origin
        if not isinstance(origin, MessageOrigin):
            origin = MessageOrigin(str(origin))
        if origin is MessageOrigin.BOT_DELIVERY and not message.decision_id:
            raise ValueError("BOT_DELIVERY requires decision_id")
        return (
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
            origin.value,
            message.decision_id,
            int(message.ingested_at or message.timestamp),
            str(message.platform or ""),
            str(message.bot_id or ""),
            int(message.event_version or 1),
        )

    def save_message(self, message: ChatMessage) -> bool:
        return bool(self._write(lambda db: self._insert_message(db, message)))

    async def save_message_async(self, message: ChatMessage) -> bool:
        return bool(await self._write_async(lambda db: self._insert_message(db, message)))

    @classmethod
    def _insert_message(cls, db, message: ChatMessage) -> bool:
        cursor = db.execute(
            """
            INSERT OR IGNORE INTO messages(
                group_id, message_id, sender_id, sender_name, text, timestamp,
                reply_to_message_id, reply_to_bot, mentions_bot, is_bot,
                is_command, image_urls, segment_types, metadata,
                origin, decision_id, ingested_at, platform, bot_id, event_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            cls._message_params(message),
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
        keys = set(row.keys())
        origin_raw = row["origin"] if "origin" in keys else "PLATFORM_REALTIME"
        try:
            origin = MessageOrigin(str(origin_raw))
        except ValueError:
            origin = MessageOrigin.PLATFORM_REALTIME
        metadata = json.loads(row["metadata"])
        mentioned_raw = metadata.get("mentioned_user_ids") if isinstance(metadata, dict) else ()
        if not isinstance(mentioned_raw, (list, tuple)):
            mentioned_raw = ()
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
            metadata=metadata,
            origin=origin,
            decision_id=row["decision_id"] if "decision_id" in keys else None,
            ingested_at=int(row["ingested_at"] or 0) if "ingested_at" in keys else 0,
            platform=str(row["platform"] or "") if "platform" in keys else "",
            bot_id=str(row["bot_id"] or "") if "bot_id" in keys else "",
            event_version=int(row["event_version"] or 1)
            if "event_version" in keys
            else 1,
            mentioned_user_ids=tuple(str(item) for item in mentioned_raw if item),
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
        def operation(db):
            existing = db.execute(
                "SELECT authority FROM profiles WHERE group_id = ? AND subject_id = ?",
                (str(group_id), str(subject_id)),
            ).fetchone()
            if existing and int(existing["authority"]) > int(authority):
                return False
            db.execute(
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

        return bool(self._write(operation))

    def get_profile(self, group_id: str, subject_id: str) -> Optional[Dict[str, Any]]:
        row = self._db.execute(
            "SELECT * FROM profiles WHERE group_id = ? AND subject_id = ?",
            (str(group_id), str(subject_id)),
        ).fetchone()
        return dict(row) if row else None

    def add_memory(self, memory: MemoryItem) -> None:
        def operation(db):
            source_ids = tuple(memory.source_message_ids or ())
            if not source_ids and memory.source_message_id:
                source_ids = (str(memory.source_message_id),)
            status = memory.status
            if not isinstance(status, MemoryStatus):
                status = MemoryStatus(str(status))
            scope = memory.scope
            if not isinstance(scope, MemoryScope):
                scope = MemoryScope(str(scope))
            sensitivity = memory.sensitivity
            if not isinstance(sensitivity, Sensitivity):
                sensitivity = Sensitivity(str(sensitivity))
            db.execute(
                """
                INSERT OR REPLACE INTO memories(
                    memory_id, group_id, subject_id, kind, text, created_at,
                    expires_at, confidence, importance, authority, source_message_id,
                    status, scope, sensitivity, extractor_version,
                    supersedes_memory_id, source_message_ids_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    source_ids[0] if source_ids else memory.source_message_id,
                    status.value,
                    scope.value,
                    sensitivity.value,
                    memory.extractor_version or "rules-v1",
                    memory.supersedes_memory_id,
                    json.dumps(list(source_ids), ensure_ascii=False),
                ),
            )

        self._write(operation)

    def list_memories(
        self,
        group_id: str,
        *,
        kind: Optional[MemoryKind] = None,
        now: int,
        limit: int = 20,
        subject_id: Optional[str] = None,
        status_accepted_only: bool = True,
        statuses: Optional[Sequence[MemoryStatus]] = None,
    ) -> List[MemoryItem]:
        sql = (
            "SELECT * FROM memories WHERE group_id = ? "
            "AND (expires_at IS NULL OR expires_at > ?)"
        )
        params: List[Any] = [str(group_id), int(now)]
        if kind is not None:
            sql += " AND kind = ?"
            params.append(kind.value)
        if subject_id is not None:
            sql += " AND subject_id = ?"
            params.append(str(subject_id))
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            sql += " AND status IN ({})".format(placeholders)
            params.extend(
                item.value if isinstance(item, MemoryStatus) else str(item)
                for item in statuses
            )
        elif status_accepted_only:
            sql += " AND status = ?"
            params.append(MemoryStatus.ACCEPTED.value)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(0, int(limit)))
        rows = self._db.execute(sql, tuple(params)).fetchall()
        return [self._row_to_memory(row) for row in rows]

    def get_memory(self, memory_id: str) -> Optional[MemoryItem]:
        row = self._db.execute(
            "SELECT * FROM memories WHERE memory_id = ?",
            (str(memory_id),),
        ).fetchone()
        return self._row_to_memory(row) if row else None

    def search_memories(
        self,
        group_id: str,
        query: str,
        now: int,
        limit: int,
        subject_id: Optional[str] = None,
        subject_ids: Optional[Sequence[str]] = None,
        include_user_in_group: bool = True,
    ) -> List[MemoryItem]:
        sql = (
            "SELECT * FROM memories WHERE group_id = ? "
            "AND status = ? "
            "AND (expires_at IS NULL OR expires_at > ?)"
        )
        params: List[Any] = [
            str(group_id),
            MemoryStatus.ACCEPTED.value,
            int(now),
        ]
        rows = self._db.execute(sql, tuple(params)).fetchall()
        items = [self._row_to_memory(row) for row in rows]
        return memory_retrieval.search_memories(
            items,
            query=query,
            now=now,
            limit=limit,
            subject_id=subject_id,
            subject_ids=subject_ids,
            include_user_in_group=include_user_in_group,
        )

    def append_memory_candidate(
        self, candidate: MemoryCandidate
    ) -> Optional[MemoryCandidate]:
        hashed = candidate.claim_hash or claim_hash(candidate.claim)
        source_ids = tuple(
            str(item) for item in candidate.source_message_ids if str(item).strip()
        )
        status = candidate.status
        if not isinstance(status, CandidateStatus):
            status = CandidateStatus(str(status))
        scope = candidate.scope
        if not isinstance(scope, MemoryScope):
            scope = MemoryScope(str(scope))
        sensitivity = candidate.sensitivity
        if not isinstance(sensitivity, Sensitivity):
            sensitivity = Sensitivity(str(sensitivity))
        kind = candidate.kind
        if not isinstance(kind, MemoryKind):
            kind = MemoryKind(str(kind))

        def operation(db):
            existing = db.execute(
                "SELECT * FROM memory_candidates "
                "WHERE group_id=? AND subject_id=? AND claim_hash=?",
                (candidate.group_id, candidate.subject_id, hashed),
            ).fetchone()
            if existing:
                return dict(existing)
            db.execute(
                """
                INSERT INTO memory_candidates(
                    candidate_id, group_id, scope, subject_id, kind, claim, claim_hash,
                    source_message_ids_json, confidence, sensitivity,
                    proposed_expires_at, extractor_version, status, created_at,
                    decided_at, decision_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate.candidate_id,
                    candidate.group_id,
                    scope.value,
                    candidate.subject_id,
                    kind.value,
                    candidate.claim.strip(),
                    hashed,
                    json.dumps(list(source_ids), ensure_ascii=False),
                    max(0.0, min(1.0, float(candidate.confidence))),
                    sensitivity.value,
                    candidate.proposed_expires_at,
                    candidate.extractor_version or "rules-v1",
                    status.value,
                    int(candidate.created_at),
                    candidate.decided_at,
                    candidate.decision_reason or "",
                ),
            )
            return None

        existing_row = self._write(operation)
        if existing_row is not None:
            return self._row_to_candidate(existing_row)
        return self.get_memory_candidate(candidate.candidate_id)

    def get_memory_candidate(self, candidate_id: str) -> Optional[MemoryCandidate]:
        row = self._db.execute(
            "SELECT * FROM memory_candidates WHERE candidate_id = ?",
            (str(candidate_id),),
        ).fetchone()
        return self._row_to_candidate(row) if row else None

    def list_memory_candidates(
        self,
        group_id: str,
        *,
        status: Optional[CandidateStatus] = None,
        limit: int = 50,
    ) -> List[MemoryCandidate]:
        sql = "SELECT * FROM memory_candidates WHERE group_id = ?"
        params: List[Any] = [str(group_id)]
        if status is not None:
            sql += " AND status = ?"
            params.append(
                status.value if isinstance(status, CandidateStatus) else str(status)
            )
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(0, int(limit)))
        rows = self._db.execute(sql, tuple(params)).fetchall()
        return [self._row_to_candidate(row) for row in rows]

    def decide_candidate(
        self,
        candidate_id: str,
        status: CandidateStatus,
        *,
        reason: str = "",
        decided_at: int,
    ) -> None:
        status_value = (
            status.value if isinstance(status, CandidateStatus) else str(status)
        )

        def operation(db):
            db.execute(
                "UPDATE memory_candidates SET status=?, decided_at=?, decision_reason=? "
                "WHERE candidate_id=?",
                (status_value, int(decided_at), str(reason or ""), str(candidate_id)),
            )

        self._write(operation)

    def accept_candidate_memory(
        self,
        candidate_id: str,
        memory: MemoryItem,
        *,
        reason: str,
        decided_at: int,
        superseded_memory_id: Optional[str] = None,
    ) -> None:
        def operation(db):
            if superseded_memory_id:
                db.execute(
                    "UPDATE memories SET status=? WHERE memory_id=?",
                    (MemoryStatus.SUPERSEDED.value, str(superseded_memory_id)),
                )
            source_ids = tuple(memory.source_message_ids or ())
            if not source_ids and memory.source_message_id:
                source_ids = (str(memory.source_message_id),)
            db.execute(
                """
                INSERT OR REPLACE INTO memories(
                    memory_id, group_id, subject_id, kind, text, created_at,
                    expires_at, confidence, importance, authority, source_message_id,
                    status, scope, sensitivity, extractor_version,
                    supersedes_memory_id, source_message_ids_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    source_ids[0] if source_ids else None,
                    MemoryStatus.ACCEPTED.value,
                    memory.scope.value
                    if isinstance(memory.scope, MemoryScope)
                    else str(memory.scope),
                    memory.sensitivity.value
                    if isinstance(memory.sensitivity, Sensitivity)
                    else str(memory.sensitivity),
                    memory.extractor_version or "rules-v1",
                    memory.supersedes_memory_id or superseded_memory_id,
                    json.dumps(list(source_ids), ensure_ascii=False),
                ),
            )
            db.execute(
                "UPDATE memory_candidates SET status=?, decided_at=?, decision_reason=? "
                "WHERE candidate_id=?",
                (
                    CandidateStatus.ACCEPTED.value,
                    int(decided_at),
                    str(reason or ""),
                    str(candidate_id),
                ),
            )

        self._write(operation)

    def correct_memory(
        self,
        memory_id: str,
        new_text: str,
        *,
        authority: int,
        now: int,
        source_message_ids: Optional[Sequence[str]] = None,
    ) -> Optional[MemoryItem]:
        old = self.get_memory(memory_id)
        if old is None:
            return None
        source_ids = tuple(
            str(item)
            for item in (source_message_ids or old.source_message_ids or ())
            if str(item).strip()
        )
        corrected = MemoryItem(
            memory_id=str(uuid4()),
            group_id=old.group_id,
            subject_id=old.subject_id,
            kind=old.kind,
            text=str(new_text).strip(),
            created_at=int(now),
            expires_at=old.expires_at,
            confidence=max(old.confidence, 0.9),
            importance=old.importance,
            authority=max(0, int(authority)),
            source_message_id=source_ids[0] if source_ids else old.source_message_id,
            status=MemoryStatus.ACCEPTED,
            scope=old.scope,
            sensitivity=Sensitivity.NONE,
            extractor_version=old.extractor_version,
            supersedes_memory_id=old.memory_id,
            source_message_ids=source_ids,
        )

        def operation(db):
            db.execute(
                "UPDATE memories SET status=? WHERE memory_id=?",
                (MemoryStatus.SUPERSEDED.value, old.memory_id),
            )
            db.execute(
                """
                INSERT INTO memories(
                    memory_id, group_id, subject_id, kind, text, created_at,
                    expires_at, confidence, importance, authority, source_message_id,
                    status, scope, sensitivity, extractor_version,
                    supersedes_memory_id, source_message_ids_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    corrected.memory_id,
                    corrected.group_id,
                    corrected.subject_id,
                    corrected.kind.value,
                    corrected.text,
                    corrected.created_at,
                    corrected.expires_at,
                    corrected.confidence,
                    corrected.importance,
                    corrected.authority,
                    corrected.source_message_id,
                    MemoryStatus.ACCEPTED.value,
                    corrected.scope.value,
                    corrected.sensitivity.value,
                    corrected.extractor_version,
                    corrected.supersedes_memory_id,
                    json.dumps(list(source_ids), ensure_ascii=False),
                ),
            )

        self._write(operation)
        return corrected

    def delete_memory(self, memory_id: str, reason: str, *, now: int) -> bool:
        item = self.get_memory(memory_id)
        if item is None:
            return False
        hashed = claim_hash(item.text)
        tombstone_id = str(uuid4())
        source_ids = list(item.source_message_ids or ())
        if not source_ids and item.source_message_id:
            source_ids = [str(item.source_message_id)]

        def operation(db):
            db.execute(
                "UPDATE memories SET status=? WHERE memory_id=?",
                (MemoryStatus.DELETED.value, str(memory_id)),
            )
            db.execute(
                """
                INSERT OR IGNORE INTO memory_tombstones(
                    tombstone_id, group_id, subject_id, claim_hash,
                    source_message_ids_json, deleted_at, reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tombstone_id,
                    item.group_id,
                    item.subject_id,
                    hashed,
                    json.dumps(source_ids, ensure_ascii=False),
                    int(now),
                    str(reason or "deleted"),
                ),
            )

        self._write(operation)
        return True

    def has_tombstone(self, group_id: str, subject_id: str, claim_hash_value: str) -> bool:
        row = self._db.execute(
            "SELECT 1 FROM memory_tombstones "
            "WHERE group_id=? AND subject_id=? AND claim_hash=? LIMIT 1",
            (str(group_id), str(subject_id), str(claim_hash_value)),
        ).fetchone()
        return row is not None

    def purge_expired_memories(self, now: int) -> int:
        def operation(db):
            cursor = db.execute(
                "UPDATE memories SET status=? "
                "WHERE status=? AND expires_at IS NOT NULL AND expires_at <= ?",
                (
                    MemoryStatus.EXPIRED.value,
                    MemoryStatus.ACCEPTED.value,
                    int(now),
                ),
            )
            return int(cursor.rowcount or 0)

        return int(self._write(operation) or 0)

    def _row_to_memory(self, row: sqlite3.Row) -> MemoryItem:
        try:
            kind = MemoryKind(row["kind"])
        except ValueError:
            kind = MemoryKind.EPISODIC
        keys = set(row.keys())
        status = MemoryStatus.ACCEPTED
        if "status" in keys and row["status"]:
            try:
                status = MemoryStatus(row["status"])
            except ValueError:
                status = MemoryStatus.ACCEPTED
        scope = MemoryScope.USER_IN_GROUP
        if "scope" in keys and row["scope"]:
            try:
                scope = MemoryScope(row["scope"])
            except ValueError:
                scope = MemoryScope.USER_IN_GROUP
        sensitivity = Sensitivity.NONE
        if "sensitivity" in keys and row["sensitivity"]:
            try:
                sensitivity = Sensitivity(row["sensitivity"])
            except ValueError:
                sensitivity = Sensitivity.NONE
        source_ids: Tuple[str, ...] = ()
        if "source_message_ids_json" in keys and row["source_message_ids_json"]:
            try:
                parsed = json.loads(row["source_message_ids_json"])
                if isinstance(parsed, list):
                    source_ids = tuple(str(item) for item in parsed if str(item))
            except (TypeError, ValueError):
                source_ids = ()
        return MemoryItem(
            memory_id=row["memory_id"],
            group_id=row["group_id"],
            subject_id=row["subject_id"],
            kind=kind,
            text=row["text"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            confidence=row["confidence"],
            importance=row["importance"],
            authority=row["authority"],
            source_message_id=row["source_message_id"],
            status=status,
            scope=scope,
            sensitivity=sensitivity,
            extractor_version=(
                row["extractor_version"]
                if "extractor_version" in keys and row["extractor_version"]
                else "rules-v1"
            ),
            supersedes_memory_id=(
                row["supersedes_memory_id"]
                if "supersedes_memory_id" in keys
                else None
            ),
            source_message_ids=source_ids,
        )

    def _row_to_candidate(self, row) -> MemoryCandidate:
        if isinstance(row, dict):
            data = row
        else:
            data = dict(row)
        try:
            kind = MemoryKind(data["kind"])
        except ValueError:
            kind = MemoryKind.EPISODIC
        try:
            scope = MemoryScope(data["scope"])
        except ValueError:
            scope = MemoryScope.USER_IN_GROUP
        try:
            sensitivity = Sensitivity(data["sensitivity"])
        except ValueError:
            sensitivity = Sensitivity.NONE
        try:
            status = CandidateStatus(data["status"])
        except ValueError:
            status = CandidateStatus.PENDING
        try:
            source_ids = tuple(
                str(item)
                for item in json.loads(data.get("source_message_ids_json") or "[]")
                if str(item)
            )
        except (TypeError, ValueError):
            source_ids = ()
        return MemoryCandidate(
            candidate_id=data["candidate_id"],
            group_id=data["group_id"],
            scope=scope,
            subject_id=data["subject_id"],
            kind=kind,
            claim=data["claim"],
            source_message_ids=source_ids,
            confidence=float(data["confidence"]),
            sensitivity=sensitivity,
            proposed_expires_at=data.get("proposed_expires_at"),
            extractor_version=data.get("extractor_version") or "rules-v1",
            status=status,
            created_at=int(data["created_at"]),
            decided_at=data.get("decided_at"),
            decision_reason=data.get("decision_reason") or "",
            claim_hash=data.get("claim_hash") or claim_hash(data["claim"]),
        )

    @staticmethod
    def _tokens(text: str) -> set:
        return memory_retrieval._tokens(text)

    @staticmethod
    def _char_ngrams(text: str, size: int = 3) -> set:
        return memory_retrieval._char_ngrams(text, size=size)

    def record_transition(
        self,
        decision_id: str,
        group_id: str,
        state: str,
        reason: str,
        timestamp: int,
    ) -> None:
        self._write(
            lambda db: db.execute(
                """
                INSERT INTO decisions(decision_id, group_id, state, reason, timestamp)
                VALUES (?, ?, ?, ?, ?)
                """,
                (decision_id, group_id, state, reason, int(timestamp)),
            )
        )

    def recent_decision_ends(
        self, group_id: str, limit: int = 3
    ) -> List[Dict[str, Any]]:
        rows = self._db.execute(
            """
            SELECT decision_id, reason, timestamp
            FROM decisions
            WHERE group_id = ? AND state = 'END'
            ORDER BY timestamp DESC, rowid DESC
            LIMIT ?
            """,
            (str(group_id), max(1, int(limit))),
        ).fetchall()
        return [dict(row) for row in rows]

    def enqueue_outbox(
        self,
        decision_id: str,
        group_id: str,
        text: str,
        created_at: int,
        expires_at: Optional[int] = None,
        *,
        quote_message_id: Optional[str] = None,
        segments: Sequence[str] = (),
        kind: str = "reply",
    ) -> bool:
        def operation(db):
            cursor = db.execute(
                """
                INSERT OR IGNORE INTO outbox(
                    decision_id, group_id, text, created_at, expires_at, sent_at,
                    status, attempt, quote_message_id, segments_json, kind
                ) VALUES (?, ?, ?, ?, ?, NULL, 'pending', 0, ?, ?, ?)
                """,
                (
                    decision_id,
                    group_id,
                    text,
                    int(created_at),
                    expires_at,
                    quote_message_id,
                    json.dumps(tuple(segments), ensure_ascii=False),
                    str(kind),
                ),
            )
            return cursor.rowcount == 1

        return bool(self._write(operation))

    async def enqueue_outbox_async(self, *args, **kwargs) -> bool:
        return bool(
            await self._write_async(
                lambda db: self._enqueue_outbox_on(db, *args, **kwargs)
            )
        )

    @staticmethod
    def _enqueue_outbox_on(
        db,
        decision_id,
        group_id,
        text,
        created_at,
        expires_at=None,
        *,
        quote_message_id=None,
        segments=(),
        kind="reply"
    ):
        cursor = db.execute(
            """
            INSERT OR IGNORE INTO outbox(
                decision_id, group_id, text, created_at, expires_at, sent_at,
                status, attempt, quote_message_id, segments_json, kind
            ) VALUES (?, ?, ?, ?, ?, NULL, 'pending', 0, ?, ?, ?)
            """,
            (
                decision_id,
                group_id,
                text,
                int(created_at),
                expires_at,
                quote_message_id,
                json.dumps(tuple(segments), ensure_ascii=False),
                str(kind),
            ),
        )
        return cursor.rowcount == 1

    def pending_outbox(self, now: int) -> List[Dict[str, Any]]:
        rows = self._db.execute(
            """
            SELECT * FROM outbox
            WHERE status = 'pending' AND (expires_at IS NULL OR expires_at > ?)
            ORDER BY created_at ASC
            """,
            (int(now),),
        ).fetchall()
        return [dict(row) for row in rows]

    def outbox_record(self, decision_id: str) -> Optional[Dict[str, Any]]:
        row = self._db.execute(
            "SELECT * FROM outbox WHERE decision_id=?", (str(decision_id),)
        ).fetchone()
        return dict(row) if row else None

    def mark_outbox_sent(self, decision_id: str, sent_at: int) -> None:
        self._write(
            lambda db: db.execute(
                "UPDATE outbox SET sent_at = ?, status='sent' WHERE decision_id = ?",
                (int(sent_at), decision_id),
            )
        )

    async def transition_outbox_async(
        self,
        decision_id: str,
        expected: str,
        status: str,
        *,
        failure_code: str = "",
        failure_detail: str = "",
        increment_attempt: bool = False
    ) -> bool:
        def operation(db):
            cursor = db.execute(
                """
                UPDATE outbox
                SET status=?, failure_code=?, failure_detail=?,
                    attempt=attempt + ?
                WHERE decision_id=? AND status=?
                """,
                (
                    str(status),
                    failure_code or None,
                    (failure_detail or "")[:500] or None,
                    int(bool(increment_attempt)),
                    decision_id,
                    str(expected),
                ),
            )
            return cursor.rowcount == 1

        return bool(await self._write_async(operation))

    async def finalize_delivery_async(
        self,
        decision_id: str,
        sent_at: int,
        bot_message: ChatMessage,
        reason: str = "sent",
    ) -> bool:
        def operation(db):
            row = db.execute(
                "SELECT status FROM outbox WHERE decision_id=?", (decision_id,)
            ).fetchone()
            if row is None or row["status"] == "sent":
                return False
            if row["status"] != "sending":
                return False
            self._insert_message(db, bot_message)
            db.execute(
                "UPDATE outbox SET status='sent', sent_at=?, "
                "failure_code=NULL, failure_detail=NULL WHERE decision_id=?",
                (int(sent_at), decision_id),
            )
            db.execute(
                "INSERT INTO decisions(decision_id, group_id, state, reason, timestamp) "
                "VALUES (?, ?, 'SEND', ?, ?)",
                (decision_id, bot_message.group_id, reason, int(sent_at)),
            )
            db.execute(
                "INSERT INTO decisions(decision_id, group_id, state, reason, timestamp) "
                "VALUES (?, ?, 'END', ?, ?)",
                (decision_id, bot_message.group_id, reason, int(sent_at)),
            )
            return True

        return bool(await self._write_async(operation))

    async def mark_sending_unknown_async(self) -> int:
        def operation(db):
            cursor = db.execute(
                "UPDATE outbox SET status='unknown', failure_code='shutdown' "
                "WHERE status='sending'"
            )
            return cursor.rowcount

        return int(await self._write_async(operation))

    def list_ledger_messages(
        self, group_id: str, limit: int = 100
    ) -> List[ChatMessage]:
        rows = self._db.execute(
            """
            SELECT * FROM messages
            WHERE group_id = ?
            ORDER BY timestamp ASC, rowid ASC
            LIMIT ?
            """,
            (str(group_id), max(0, int(limit))),
        ).fetchall()
        return [self._row_to_message(row) for row in rows]

    def list_bot_deliveries(
        self, group_id: str, limit: int = 20
    ) -> List[ChatMessage]:
        rows = self._db.execute(
            """
            SELECT * FROM messages
            WHERE group_id = ? AND origin = 'BOT_DELIVERY'
              AND decision_id IS NOT NULL AND decision_id != ''
            ORDER BY timestamp DESC, rowid DESC
            LIMIT ?
            """,
            (str(group_id), max(0, int(limit))),
        ).fetchall()
        return [self._row_to_message(row) for row in reversed(rows)]

    def list_candidate_sent_at(self, group_id: str, since: int) -> List[int]:
        rows = self._db.execute(
            """
            SELECT sent_at FROM outbox
            WHERE group_id = ? AND status = 'sent'
              AND kind IN ('reply', 'candidate')
              AND sent_at IS NOT NULL AND sent_at > ?
            ORDER BY sent_at ASC
            """,
            (str(group_id), int(since)),
        ).fetchall()
        # Spontaneous/candidate deliveries are tagged as kind=reply by workflow;
        # use BOT_DELIVERY messages joined with outbox kind when available.
        return [int(row["sent_at"]) for row in rows]

    def list_spontaneous_sent_at(self, group_id: str, since: int) -> List[int]:
        rows = self._db.execute(
            """
            SELECT sent_at FROM outbox
            WHERE group_id = ? AND status = 'sent' AND kind = 'candidate'
              AND sent_at IS NOT NULL AND sent_at > ?
            ORDER BY sent_at ASC
            """,
            (str(group_id), int(since)),
        ).fetchall()
        return [int(row["sent_at"]) for row in rows]

    def latest_open_topic_epoch(self, group_id: str) -> Optional[Dict[str, Any]]:
        row = self._db.execute(
            """
            SELECT * FROM topic_epochs
            WHERE group_id = ? AND closed_at IS NULL
            ORDER BY opened_at DESC
            LIMIT 1
            """,
            (str(group_id),),
        ).fetchone()
        return dict(row) if row else None

    def open_topic_epoch(
        self,
        group_id: str,
        topic_id: str,
        opened_at: int,
        last_message_id: Optional[str] = None,
    ) -> bool:
        def operation(db):
            open_row = db.execute(
                "SELECT topic_id FROM topic_epochs "
                "WHERE group_id=? AND closed_at IS NULL LIMIT 1",
                (str(group_id),),
            ).fetchone()
            if open_row is not None:
                db.execute(
                    "UPDATE topic_epochs SET closed_at=?, close_reason='HARD_WAKE' "
                    "WHERE group_id=? AND topic_id=? AND closed_at IS NULL",
                    (int(opened_at), str(group_id), open_row["topic_id"]),
                )
            db.execute(
                """
                INSERT INTO topic_epochs(
                    group_id, topic_id, opened_at, closed_at, close_reason, last_message_id
                ) VALUES (?, ?, ?, NULL, NULL, ?)
                """,
                (str(group_id), str(topic_id), int(opened_at), last_message_id),
            )
            return True

        return bool(self._write(operation))

    async def open_topic_epoch_async(self, *args, **kwargs) -> bool:
        return bool(
            await self._write_async(
                lambda db: self._open_topic_epoch_on(db, *args, **kwargs)
            )
        )

    @staticmethod
    def _open_topic_epoch_on(
        db,
        group_id: str,
        topic_id: str,
        opened_at: int,
        last_message_id: Optional[str] = None,
        close_existing_reason: str = "HARD_WAKE",
    ) -> bool:
        open_row = db.execute(
            "SELECT topic_id FROM topic_epochs "
            "WHERE group_id=? AND closed_at IS NULL LIMIT 1",
            (str(group_id),),
        ).fetchone()
        if open_row is not None:
            db.execute(
                "UPDATE topic_epochs SET closed_at=?, close_reason=? "
                "WHERE group_id=? AND topic_id=? AND closed_at IS NULL",
                (
                    int(opened_at),
                    str(close_existing_reason),
                    str(group_id),
                    open_row["topic_id"],
                ),
            )
        db.execute(
            """
            INSERT INTO topic_epochs(
                group_id, topic_id, opened_at, closed_at, close_reason, last_message_id
            ) VALUES (?, ?, ?, NULL, NULL, ?)
            """,
            (str(group_id), str(topic_id), int(opened_at), last_message_id),
        )
        return True

    def close_topic_epoch(
        self,
        group_id: str,
        topic_id: str,
        closed_at: int,
        close_reason: str,
        last_message_id: Optional[str] = None,
    ) -> bool:
        def operation(db):
            cursor = db.execute(
                """
                UPDATE topic_epochs
                SET closed_at=?, close_reason=?, last_message_id=COALESCE(?, last_message_id)
                WHERE group_id=? AND topic_id=? AND closed_at IS NULL
                """,
                (
                    int(closed_at),
                    str(close_reason),
                    last_message_id,
                    str(group_id),
                    str(topic_id),
                ),
            )
            return cursor.rowcount == 1

        return bool(self._write(operation))

    async def close_topic_epoch_async(self, *args, **kwargs) -> bool:
        return bool(
            await self._write_async(
                lambda db: self._close_topic_epoch_on(db, *args, **kwargs)
            )
        )

    @staticmethod
    def _close_topic_epoch_on(
        db,
        group_id: str,
        topic_id: str,
        closed_at: int,
        close_reason: str,
        last_message_id: Optional[str] = None,
    ) -> bool:
        cursor = db.execute(
            """
            UPDATE topic_epochs
            SET closed_at=?, close_reason=?, last_message_id=COALESCE(?, last_message_id)
            WHERE group_id=? AND topic_id=? AND closed_at IS NULL
            """,
            (
                int(closed_at),
                str(close_reason),
                last_message_id,
                str(group_id),
                str(topic_id),
            ),
        )
        return cursor.rowcount == 1

    def grant_continuation(
        self,
        *,
        grant_id: str,
        group_id: str,
        sender_id: str,
        opened_by_decision_id: str,
        opened_by_message_id: str,
        trigger_kind: str,
        granted_at: int,
        expires_at: int,
        max_total_seconds: int,
    ) -> bool:
        absolute = int(granted_at) + max(1, int(max_total_seconds))
        def operation(db):
            db.execute(
                """
                INSERT INTO continuation_grants(
                    grant_id, group_id, sender_id, opened_by_decision_id,
                    opened_by_message_id, trigger_kind, granted_at, expires_at,
                    max_total_seconds, absolute_deadline_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(grant_id),
                    str(group_id),
                    str(sender_id),
                    str(opened_by_decision_id),
                    str(opened_by_message_id),
                    str(trigger_kind),
                    int(granted_at),
                    int(expires_at),
                    max(1, int(max_total_seconds)),
                    absolute,
                ),
            )
            return True

        return bool(self._write(operation))

    async def grant_continuation_async(self, **kwargs) -> bool:
        return bool(
            await self._write_async(lambda db: self._grant_continuation_on(db, **kwargs))
        )

    @staticmethod
    def _grant_continuation_on(db, **kwargs) -> bool:
        granted_at = int(kwargs["granted_at"])
        max_total_seconds = max(1, int(kwargs["max_total_seconds"]))
        db.execute(
            """
            INSERT INTO continuation_grants(
                grant_id, group_id, sender_id, opened_by_decision_id,
                opened_by_message_id, trigger_kind, granted_at, expires_at,
                max_total_seconds, absolute_deadline_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(kwargs["grant_id"]),
                str(kwargs["group_id"]),
                str(kwargs["sender_id"]),
                str(kwargs["opened_by_decision_id"]),
                str(kwargs["opened_by_message_id"]),
                str(kwargs["trigger_kind"]),
                granted_at,
                int(kwargs["expires_at"]),
                max_total_seconds,
                granted_at + max_total_seconds,
            ),
        )
        return True

    def latest_continuation_grant(
        self, group_id: str, now: int, sender_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        sql = """
            SELECT * FROM continuation_grants
            WHERE group_id = ?
              AND expires_at >= ?
              AND absolute_deadline_at >= ?
        """
        params: List[Any] = [str(group_id), int(now), int(now)]
        if sender_id is not None:
            sql += " AND sender_id = ?"
            params.append(str(sender_id))
        sql += " ORDER BY granted_at DESC LIMIT 1"
        row = self._db.execute(sql, tuple(params)).fetchone()
        return dict(row) if row else None

    def get_favorability(self, group_id: str, user_id: str) -> Optional[int]:
        row = self._db.execute(
            "SELECT score FROM favorability WHERE group_id = ? AND user_id = ?",
            (str(group_id), str(user_id)),
        ).fetchone()
        return int(row["score"]) if row else None

    def set_favorability(
        self,
        group_id: str,
        user_id: str,
        score: int,
        updated_at: int,
    ) -> int:
        from ..core.favorability import clamp_score

        value = clamp_score(score)
        def operation(db):
            db.execute(
                """
                INSERT INTO favorability(group_id, user_id, score, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(group_id, user_id) DO UPDATE SET
                    score = excluded.score,
                    updated_at = excluded.updated_at
                """,
                (str(group_id), str(user_id), value, int(updated_at)),
            )
        self._write(operation)
        return value

    def adjust_favorability(
        self,
        group_id: str,
        user_id: str,
        delta: int,
        updated_at: int,
        *,
        default: int = 0,
    ) -> int:
        current = self.get_favorability(group_id, user_id)
        from ..core.favorability import apply_delta

        return self.set_favorability(
            group_id,
            user_id,
            apply_delta(current, delta, default=default),
            updated_at,
        )

    def append_social_event(self, event: SocialEvent) -> bool:
        kind = event.kind
        if not isinstance(kind, SocialEventKind):
            kind = SocialEventKind(str(kind))

        def operation(db):
            cursor = db.execute(
                """
                INSERT OR IGNORE INTO social_events(
                    event_id, group_id, user_id, kind, source_message_id,
                    confidence, occurred_at, decision_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(event.event_id),
                    str(event.group_id),
                    str(event.user_id),
                    kind.value,
                    str(event.source_message_id),
                    float(event.confidence),
                    int(event.occurred_at),
                    event.decision_id,
                ),
            )
            return cursor.rowcount == 1

        return bool(self._write(operation))

    def list_social_events(
        self,
        group_id: str,
        user_id: Optional[str] = None,
        limit: int = 200,
    ) -> List[SocialEvent]:
        sql = """
            SELECT * FROM social_events
            WHERE group_id = ?
        """
        params: List[Any] = [str(group_id)]
        if user_id is not None:
            sql += " AND user_id = ?"
            params.append(str(user_id))
        sql += " ORDER BY occurred_at ASC, event_id ASC LIMIT ?"
        params.append(max(1, int(limit)))
        rows = self._db.execute(sql, tuple(params)).fetchall()
        events: List[SocialEvent] = []
        for row in rows:
            try:
                kind = SocialEventKind(str(row["kind"]))
            except ValueError:
                kind = SocialEventKind.NEUTRAL
            events.append(
                SocialEvent(
                    event_id=str(row["event_id"]),
                    group_id=str(row["group_id"]),
                    user_id=str(row["user_id"]),
                    kind=kind,
                    source_message_id=str(row["source_message_id"]),
                    confidence=float(row["confidence"]),
                    occurred_at=int(row["occurred_at"]),
                    decision_id=row["decision_id"],
                )
            )
        return events

    def get_relationship_state(
        self, group_id: str, user_id: str
    ) -> Optional[RelationshipState]:
        row = self._db.execute(
            """
            SELECT * FROM relationship_state
            WHERE group_id = ? AND user_id = ?
            """,
            (str(group_id), str(user_id)),
        ).fetchone()
        if row is None:
            return None
        return RelationshipState(
            group_id=str(row["group_id"]),
            user_id=str(row["user_id"]),
            familiarity=int(row["familiarity"]),
            affinity=int(row["affinity"]),
            trust=int(row["trust"]),
            boundary_pressure=int(row["boundary_pressure"]),
            interaction_count=int(row["interaction_count"]),
            last_interaction_at=int(row["last_interaction_at"]),
            configured_relationship=row["configured_relationship"],
            updated_at=int(row["updated_at"]),
        )

    def upsert_relationship_state(self, state: RelationshipState) -> None:
        def operation(db):
            db.execute(
                """
                INSERT INTO relationship_state(
                    group_id, user_id, familiarity, affinity, trust,
                    boundary_pressure, interaction_count, last_interaction_at,
                    configured_relationship, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(group_id, user_id) DO UPDATE SET
                    familiarity = excluded.familiarity,
                    affinity = excluded.affinity,
                    trust = excluded.trust,
                    boundary_pressure = excluded.boundary_pressure,
                    interaction_count = excluded.interaction_count,
                    last_interaction_at = excluded.last_interaction_at,
                    configured_relationship = excluded.configured_relationship,
                    updated_at = excluded.updated_at
                """,
                (
                    str(state.group_id),
                    str(state.user_id),
                    int(state.familiarity),
                    int(state.affinity),
                    int(state.trust),
                    int(state.boundary_pressure),
                    int(state.interaction_count),
                    int(state.last_interaction_at),
                    state.configured_relationship,
                    int(state.updated_at),
                ),
            )

        self._write(operation)

    def rebuild_relationship_state(
        self,
        group_id: str,
        user_id: str,
        *,
        configured_relationship: Optional[str] = None,
        seed_affinity: int = 0,
        now: int = 0,
    ) -> RelationshipState:
        from ..social.projector import SocialStateProjector

        events = self.list_social_events(group_id, user_id=user_id, limit=5000)
        state = SocialStateProjector().project(
            events,
            group_id=group_id,
            user_id=user_id,
            configured_relationship=configured_relationship,
            seed_affinity=int(seed_affinity),
            now=int(now),
        )
        self.upsert_relationship_state(state)
        self.set_favorability(
            group_id, user_id, state.affinity, state.updated_at or now
        )
        return state

    def record_social_interaction(
        self,
        event: SocialEvent,
        *,
        soft_trigger: bool = False,
        configured_relationship: Optional[str] = None,
        now: int = 0,
    ) -> Optional[RelationshipState]:
        """幂等写入事件并增量投影；重复 source 返回已有状态且不双计。"""
        from ..social.projector import SocialStateProjector

        inserted = self.append_social_event(event)
        current = self.get_relationship_state(event.group_id, event.user_id)
        if current is None:
            fav = self.get_favorability(event.group_id, event.user_id)
            current = RelationshipState(
                group_id=event.group_id,
                user_id=event.user_id,
                affinity=int(fav) if fav is not None else 0,
                configured_relationship=configured_relationship,
                updated_at=int(now or event.occurred_at),
            )
        if not inserted:
            return current
        state = SocialStateProjector().apply_event(
            current,
            event,
            configured_relationship=configured_relationship,
            now=int(now or event.occurred_at),
            soft_trigger=soft_trigger,
        )
        self.upsert_relationship_state(state)
        self.set_favorability(
            event.group_id, event.user_id, state.affinity, state.updated_at
        )
        return state

    def close(self) -> None:
        self._writer.close()
        self._db.close()

    async def flush_async(self) -> None:
        await self._writer.flush_async()
