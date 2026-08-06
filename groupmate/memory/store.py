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
    OutboundSegment,
    RelationshipState,
    Sensitivity,
    SocialEvent,
    SocialEventKind,
)
from .migrations import SCHEMA_VERSION, migrate_database
from .privacy import claim_hash
from . import retrieval as memory_retrieval
from .writer import SQLiteWriteWorker


def _require_persona_id(value: str) -> str:
    persona_id = str(value or "").strip()
    if not persona_id:
        raise ValueError("persona_id is required")
    return persona_id


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
    def _message_params(persona_id: str, message: ChatMessage):
        persona_id = _require_persona_id(persona_id)
        origin = message.origin
        if not isinstance(origin, MessageOrigin):
            origin = MessageOrigin(str(origin))
        if origin is MessageOrigin.BOT_DELIVERY and not message.decision_id:
            raise ValueError("BOT_DELIVERY requires decision_id")
        return (
            persona_id,
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

    def save_message(self, persona_id: str, message: ChatMessage) -> bool:
        persona_id = _require_persona_id(persona_id)
        return bool(self._write(lambda db: self._insert_message(db, persona_id, message)))

    async def save_message_async(self, persona_id: str, message: ChatMessage) -> bool:
        persona_id = _require_persona_id(persona_id)
        return bool(
            await self._write_async(lambda db: self._insert_message(db, persona_id, message))
        )

    @classmethod
    def _insert_message(cls, db, persona_id: str, message: ChatMessage) -> bool:
        cursor = db.execute(
            """
            INSERT OR IGNORE INTO messages(
                persona_id, group_id, message_id, sender_id, sender_name, text, timestamp,
                reply_to_message_id, reply_to_bot, mentions_bot, is_bot,
                is_command, image_urls, segment_types, metadata,
                origin, decision_id, ingested_at, platform, bot_id, event_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            cls._message_params(persona_id, message),
        )
        return cursor.rowcount == 1

    def recent_messages(
        self, persona_id: str, group_id: str, limit: int
    ) -> List[ChatMessage]:
        persona_id = _require_persona_id(persona_id)
        rows = self._db.execute(
            """
            SELECT * FROM messages
            WHERE persona_id = ? AND group_id = ?
            ORDER BY timestamp DESC, rowid DESC
            LIMIT ?
            """,
            (persona_id, str(group_id), max(0, int(limit))),
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
        persona_id: str,
        group_id: str,
        subject_id: str,
        display_name: str,
        relationship: str,
        authority: int,
        updated_at: int = 0,
    ) -> bool:
        persona_id = _require_persona_id(persona_id)

        def operation(db):
            existing = db.execute(
                "SELECT authority FROM profiles "
                "WHERE persona_id = ? AND group_id = ? AND subject_id = ?",
                (persona_id, str(group_id), str(subject_id)),
            ).fetchone()
            if existing and int(existing["authority"]) > int(authority):
                return False
            db.execute(
                """
                INSERT INTO profiles(
                    persona_id, group_id, subject_id, display_name, relationship, authority, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(persona_id, group_id, subject_id) DO UPDATE SET
                    display_name = excluded.display_name,
                    relationship = excluded.relationship,
                    authority = excluded.authority,
                    updated_at = excluded.updated_at
                """,
                (
                    persona_id,
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

    def get_profile(
        self, persona_id: str, group_id: str, subject_id: str
    ) -> Optional[Dict[str, Any]]:
        persona_id = _require_persona_id(persona_id)
        row = self._db.execute(
            "SELECT * FROM profiles "
            "WHERE persona_id = ? AND group_id = ? AND subject_id = ?",
            (persona_id, str(group_id), str(subject_id)),
        ).fetchone()
        return dict(row) if row else None

    def add_memory(self, persona_id: str, memory: MemoryItem) -> None:
        persona_id = _require_persona_id(persona_id)
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
                    memory_id, persona_id, group_id, subject_id, kind, text, created_at,
                    expires_at, confidence, importance, authority, source_message_id,
                    status, scope, sensitivity, extractor_version,
                    supersedes_memory_id, source_message_ids_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    memory.memory_id,
                    persona_id,
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
        persona_id: str,
        group_id: str,
        *,
        kind: Optional[MemoryKind] = None,
        now: int,
        limit: int = 20,
        subject_id: Optional[str] = None,
        status_accepted_only: bool = True,
        statuses: Optional[Sequence[MemoryStatus]] = None,
    ) -> List[MemoryItem]:
        persona_id = _require_persona_id(persona_id)
        sql = (
            "SELECT * FROM memories WHERE persona_id = ? AND group_id = ? "
            "AND (expires_at IS NULL OR expires_at > ?)"
        )
        params: List[Any] = [persona_id, str(group_id), int(now)]
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

    def get_memory(self, persona_id: str, memory_id: str) -> Optional[MemoryItem]:
        persona_id = _require_persona_id(persona_id)
        row = self._db.execute(
            "SELECT * FROM memories WHERE persona_id = ? AND memory_id = ?",
            (persona_id, str(memory_id)),
        ).fetchone()
        return self._row_to_memory(row) if row else None

    def search_memories(
        self,
        persona_id: str,
        group_id: str,
        query: str,
        now: int,
        limit: int,
        subject_id: Optional[str] = None,
        subject_ids: Optional[Sequence[str]] = None,
        include_user_in_group: bool = True,
    ) -> List[MemoryItem]:
        persona_id = _require_persona_id(persona_id)
        sql = (
            "SELECT * FROM memories WHERE persona_id = ? AND group_id = ? "
            "AND status = ? "
            "AND (expires_at IS NULL OR expires_at > ?)"
        )
        params: List[Any] = [
            persona_id,
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
        self, persona_id: str, candidate: MemoryCandidate
    ) -> Optional[MemoryCandidate]:
        persona_id = _require_persona_id(persona_id)
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
                "WHERE persona_id=? AND group_id=? AND subject_id=? AND claim_hash=?",
                (persona_id, candidate.group_id, candidate.subject_id, hashed),
            ).fetchone()
            if existing:
                return dict(existing)
            db.execute(
                """
                INSERT INTO memory_candidates(
                    candidate_id, persona_id, group_id, scope, subject_id, kind, claim, claim_hash,
                    source_message_ids_json, confidence, sensitivity,
                    proposed_expires_at, extractor_version, status, created_at,
                    decided_at, decision_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate.candidate_id,
                    persona_id,
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
        return self.get_memory_candidate(persona_id, candidate.candidate_id)

    def get_memory_candidate(
        self, persona_id: str, candidate_id: str
    ) -> Optional[MemoryCandidate]:
        persona_id = _require_persona_id(persona_id)
        row = self._db.execute(
            "SELECT * FROM memory_candidates WHERE persona_id = ? AND candidate_id = ?",
            (persona_id, str(candidate_id)),
        ).fetchone()
        return self._row_to_candidate(row) if row else None

    def list_memory_candidates(
        self,
        persona_id: str,
        group_id: str,
        *,
        status: Optional[CandidateStatus] = None,
        limit: int = 50,
    ) -> List[MemoryCandidate]:
        persona_id = _require_persona_id(persona_id)
        sql = "SELECT * FROM memory_candidates WHERE persona_id = ? AND group_id = ?"
        params: List[Any] = [persona_id, str(group_id)]
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
        persona_id: str,
        candidate_id: str,
        status: CandidateStatus,
        *,
        reason: str = "",
        decided_at: int,
    ) -> None:
        persona_id = _require_persona_id(persona_id)
        status_value = (
            status.value if isinstance(status, CandidateStatus) else str(status)
        )

        def operation(db):
            db.execute(
                "UPDATE memory_candidates SET status=?, decided_at=?, decision_reason=? "
                "WHERE persona_id=? AND candidate_id=?",
                (status_value, int(decided_at), str(reason or ""), persona_id, str(candidate_id)),
            )

        self._write(operation)

    def accept_candidate_memory(
        self,
        persona_id: str,
        candidate_id: str,
        memory: MemoryItem,
        *,
        reason: str,
        decided_at: int,
        superseded_memory_id: Optional[str] = None,
    ) -> None:
        persona_id = _require_persona_id(persona_id)
        def operation(db):
            if superseded_memory_id:
                db.execute(
                    "UPDATE memories SET status=? WHERE persona_id=? AND memory_id=?",
                    (MemoryStatus.SUPERSEDED.value, persona_id, str(superseded_memory_id)),
                )
            source_ids = tuple(memory.source_message_ids or ())
            if not source_ids and memory.source_message_id:
                source_ids = (str(memory.source_message_id),)
            db.execute(
                """
                INSERT OR REPLACE INTO memories(
                    memory_id, persona_id, group_id, subject_id, kind, text, created_at,
                    expires_at, confidence, importance, authority, source_message_id,
                    status, scope, sensitivity, extractor_version,
                    supersedes_memory_id, source_message_ids_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    memory.memory_id,
                    persona_id,
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
                "WHERE persona_id=? AND candidate_id=?",
                (
                    CandidateStatus.ACCEPTED.value,
                    int(decided_at),
                    str(reason or ""),
                    persona_id,
                    str(candidate_id),
                ),
            )

        self._write(operation)

    def correct_memory(
        self,
        persona_id: str,
        memory_id: str,
        new_text: str,
        *,
        authority: int,
        now: int,
        source_message_ids: Optional[Sequence[str]] = None,
    ) -> Optional[MemoryItem]:
        persona_id = _require_persona_id(persona_id)
        old = self.get_memory(persona_id, memory_id)
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
                "UPDATE memories SET status=? WHERE persona_id=? AND memory_id=?",
                (MemoryStatus.SUPERSEDED.value, persona_id, old.memory_id),
            )
            db.execute(
                """
                INSERT INTO memories(
                    memory_id, persona_id, group_id, subject_id, kind, text, created_at,
                    expires_at, confidence, importance, authority, source_message_id,
                    status, scope, sensitivity, extractor_version,
                    supersedes_memory_id, source_message_ids_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    corrected.memory_id,
                    persona_id,
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

    def delete_memory(
        self, persona_id: str, memory_id: str, reason: str, *, now: int
    ) -> bool:
        persona_id = _require_persona_id(persona_id)
        item = self.get_memory(persona_id, memory_id)
        if item is None:
            return False
        hashed = claim_hash(item.text)
        tombstone_id = str(uuid4())
        source_ids = list(item.source_message_ids or ())
        if not source_ids and item.source_message_id:
            source_ids = [str(item.source_message_id)]

        def operation(db):
            db.execute(
                "UPDATE memories SET status=? WHERE persona_id=? AND memory_id=?",
                (MemoryStatus.DELETED.value, persona_id, str(memory_id)),
            )
            db.execute(
                """
                INSERT OR IGNORE INTO memory_tombstones(
                    tombstone_id, persona_id, group_id, subject_id, claim_hash,
                    source_message_ids_json, deleted_at, reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tombstone_id,
                    persona_id,
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

    def has_tombstone(
        self, persona_id: str, group_id: str, subject_id: str, claim_hash_value: str
    ) -> bool:
        persona_id = _require_persona_id(persona_id)
        row = self._db.execute(
            "SELECT 1 FROM memory_tombstones "
            "WHERE persona_id=? AND group_id=? AND subject_id=? AND claim_hash=? LIMIT 1",
            (persona_id, str(group_id), str(subject_id), str(claim_hash_value)),
        ).fetchone()
        return row is not None

    def purge_expired_memories(self, persona_id: str, now: int) -> int:
        persona_id = _require_persona_id(persona_id)
        def operation(db):
            cursor = db.execute(
                "UPDATE memories SET status=? "
                "WHERE persona_id=? AND status=? "
                "AND expires_at IS NOT NULL AND expires_at <= ?",
                (
                    MemoryStatus.EXPIRED.value,
                    persona_id,
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
        persona_id: str,
        decision_id: str,
        group_id: str,
        state: str,
        reason: str,
        timestamp: int,
    ) -> None:
        persona_id = _require_persona_id(persona_id)
        self._write(
            lambda db: db.execute(
                """
                INSERT INTO decisions(
                    persona_id, decision_id, group_id, state, reason, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (persona_id, decision_id, group_id, state, reason, int(timestamp)),
            )
        )

    def recent_decision_ends(
        self, persona_id: str, group_id: str, limit: int = 3
    ) -> List[Dict[str, Any]]:
        persona_id = _require_persona_id(persona_id)
        rows = self._db.execute(
            """
            SELECT decision_id, reason, timestamp
            FROM decisions
            WHERE persona_id = ? AND group_id = ? AND state = 'END'
            ORDER BY timestamp DESC, rowid DESC
            LIMIT ?
            """,
            (persona_id, str(group_id), max(1, int(limit))),
        ).fetchall()
        return [dict(row) for row in rows]

    def decision_group_ids(self, persona_id: str) -> List[str]:
        """Distinct group ids that have decision ledger rows."""
        persona_id = _require_persona_id(persona_id)
        rows = self._db.execute(
            """
            SELECT DISTINCT group_id
            FROM decisions
            WHERE persona_id = ?
            ORDER BY group_id ASC
            """,
            (persona_id,),
        ).fetchall()
        return [str(row["group_id"]) for row in rows if str(row["group_id"] or "").strip()]

    def recent_decisions(
        self,
        persona_id: str,
        *,
        group_id: Optional[str] = None,
        outcome: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Return recent END decisions with path summary fields (no reply text)."""
        persona_id = _require_persona_id(persona_id)
        limit = max(1, min(100, int(limit)))
        outcome_filter = str(outcome or "").strip().lower()
        if outcome_filter in ("", "all"):
            outcome_filter = ""
        elif outcome_filter not in ("sent", "silent"):
            raise ValueError("outcome must be all, sent, or silent")

        group_filter = str(group_id).strip() if group_id else ""
        # Support comma-separated multi-group filter from the plugin page.
        group_filters = [
            part.strip()
            for part in group_filter.split(",")
            if part.strip()
        ] if group_filter else []
        params: List[Any] = [persona_id]
        sql = """
            SELECT decision_id, group_id, reason, timestamp
            FROM decisions
            WHERE persona_id = ? AND state = 'END'
        """
        if len(group_filters) == 1:
            sql += " AND group_id = ?"
            params.append(group_filters[0])
        elif len(group_filters) > 1:
            placeholders = ",".join("?" for _ in group_filters)
            sql += f" AND group_id IN ({placeholders})"
            params.extend(group_filters)
        sql += " ORDER BY timestamp DESC, rowid DESC LIMIT ?"
        # Over-fetch when filtering by outcome so sent/silent still fill the page.
        fetch_limit = limit * 3 if outcome_filter else limit
        params.append(fetch_limit)
        ends = [dict(row) for row in self._db.execute(sql, params).fetchall()]
        if not ends:
            return []

        decision_ids = [str(item["decision_id"]) for item in ends]
        placeholders = ",".join("?" for _ in decision_ids)
        sent_rows = self._db.execute(
            f"""
            SELECT DISTINCT decision_id
            FROM decisions
            WHERE persona_id = ? AND state = 'SEND'
              AND decision_id IN ({placeholders})
            """,
            [persona_id, *decision_ids],
        ).fetchall()
        sent_ids = {str(row["decision_id"]) for row in sent_rows}

        summary_states = (
            "OBSERVE",
            "SCENE",
            "PARTICIPATION",
            "INTENT",
            "ACT",
            "END",
        )
        state_placeholders = ",".join("?" for _ in summary_states)
        stage_rows = self._db.execute(
            f"""
            SELECT decision_id, state, reason, timestamp
            FROM decisions
            WHERE persona_id = ?
              AND decision_id IN ({placeholders})
              AND state IN ({state_placeholders})
            ORDER BY timestamp ASC, rowid ASC
            """,
            [persona_id, *decision_ids, *summary_states],
        ).fetchall()
        stages_by_id: Dict[str, Dict[str, str]] = {}
        for row in stage_rows:
            decision_id = str(row["decision_id"])
            bucket = stages_by_id.setdefault(decision_id, {})
            # Keep the latest reason for each state key.
            bucket[str(row["state"])] = str(row["reason"] or "")

        items: List[Dict[str, Any]] = []
        for end in ends:
            decision_id = str(end["decision_id"])
            sent = decision_id in sent_ids
            if outcome_filter == "sent" and not sent:
                continue
            if outcome_filter == "silent" and sent:
                continue
            stages = stages_by_id.get(decision_id, {})
            items.append(
                {
                    "decision_id": decision_id,
                    "group_id": str(end["group_id"]),
                    "timestamp": int(end["timestamp"] or 0),
                    "sent": sent,
                    "end_reason": str(end["reason"] or ""),
                    "trigger": stages.get("OBSERVE", ""),
                    "scene": stages.get("SCENE", ""),
                    "participation": stages.get("PARTICIPATION", ""),
                    "intent": stages.get("INTENT", ""),
                    "act": stages.get("ACT", ""),
                }
            )
            if len(items) >= limit:
                break
        return items

    def decision_trace(
        self, persona_id: str, decision_id: str, *, context_limit: int = 12
    ) -> Optional[Dict[str, Any]]:
        """Return stage trail plus nearby chat context for one decision."""
        persona_id = _require_persona_id(persona_id)
        decision_id = str(decision_id or "").strip()
        if not decision_id:
            raise ValueError("decision_id is required")
        rows = self._db.execute(
            """
            SELECT decision_id, group_id, state, reason, timestamp
            FROM decisions
            WHERE persona_id = ? AND decision_id = ?
            ORDER BY timestamp ASC, rowid ASC
            """,
            (persona_id, decision_id),
        ).fetchall()
        if not rows:
            return None
        stages = [
            {
                "state": str(row["state"] or ""),
                "reason": str(row["reason"] or ""),
                "timestamp": int(row["timestamp"] or 0),
            }
            for row in rows
        ]
        sent = any(item["state"] == "SEND" for item in stages)
        end_reason = ""
        for item in reversed(stages):
            if item["state"] == "END":
                end_reason = item["reason"]
                break
        summary: Dict[str, str] = {}
        for item in stages:
            if item["state"] in {
                "OBSERVE",
                "SCENE",
                "PARTICIPATION",
                "INTENT",
                "ACT",
                "END",
            }:
                summary[item["state"]] = item["reason"]
        group_id = str(rows[0]["group_id"])
        observe_at = next(
            (item["timestamp"] for item in stages if item["state"] == "OBSERVE"),
            stages[0]["timestamp"],
        )
        return {
            "decision_id": decision_id,
            "group_id": group_id,
            "sent": sent,
            "end_reason": end_reason,
            "trigger": summary.get("OBSERVE", ""),
            "scene": summary.get("SCENE", ""),
            "participation": summary.get("PARTICIPATION", ""),
            "intent": summary.get("INTENT", ""),
            "act": summary.get("ACT", ""),
            "stages": stages,
            "context": self.decision_context_messages(
                persona_id,
                group_id,
                decision_id=decision_id,
                at_timestamp=observe_at,
                limit=context_limit,
            ),
        }

    def decision_context_messages(
        self,
        persona_id: str,
        group_id: str,
        *,
        decision_id: str,
        at_timestamp: int,
        limit: int = 12,
        text_limit: int = 160,
    ) -> List[Dict[str, Any]]:
        """Nearby messages at decision time (admin page context; truncated text)."""
        persona_id = _require_persona_id(persona_id)
        group_id = str(group_id)
        decision_id = str(decision_id or "").strip()
        limit = max(1, min(30, int(limit)))
        text_limit = max(40, min(400, int(text_limit)))
        at_timestamp = int(at_timestamp or 0)

        before_rows = self._db.execute(
            """
            SELECT * FROM messages
            WHERE persona_id = ? AND group_id = ? AND timestamp <= ?
            ORDER BY timestamp DESC, rowid DESC
            LIMIT ?
            """,
            (persona_id, group_id, at_timestamp, limit),
        ).fetchall()
        messages = [self._row_to_message(row) for row in reversed(before_rows)]
        seen = {(item.group_id, item.message_id) for item in messages}

        if decision_id:
            delivery_rows = self._db.execute(
                """
                SELECT * FROM messages
                WHERE persona_id = ? AND group_id = ? AND decision_id = ?
                ORDER BY timestamp ASC, rowid ASC
                """,
                (persona_id, group_id, decision_id),
            ).fetchall()
            for row in delivery_rows:
                message = self._row_to_message(row)
                key = (message.group_id, message.message_id)
                if key not in seen:
                    messages.append(message)
                    seen.add(key)

        focus_id = ""
        for message in reversed(messages):
            if not message.is_bot and message.origin is not MessageOrigin.BOT_DELIVERY:
                focus_id = message.message_id
                break

        payload: List[Dict[str, Any]] = []
        for message in messages:
            text = str(message.text or "").strip()
            has_image = bool(message.image_urls)
            if len(text) > text_limit:
                text = text[: text_limit - 1] + "…"
            if not text and has_image:
                text = "[图片]"
            if not text and "poke" in {
                str(item).lower() for item in (message.segment_types or ())
            }:
                text = "[戳一戳]"
            payload.append(
                {
                    "message_id": message.message_id,
                    "sender_name": message.sender_name or ("机器人" if message.is_bot else "群友"),
                    "is_bot": bool(message.is_bot),
                    "text": text,
                    "timestamp": int(message.timestamp or 0),
                    "has_image": has_image,
                    "is_focus": message.message_id == focus_id,
                    "is_reply": bool(
                        message.decision_id == decision_id and decision_id
                    ),
                }
            )
        return payload

    def enqueue_outbox(
        self,
        persona_id: str,
        decision_id: str,
        group_id: str,
        text: str,
        created_at: int,
        expires_at: Optional[int] = None,
        *,
        quote_message_id: Optional[str] = None,
        segments: Sequence[str] = (),
        outbound: Sequence[OutboundSegment] = (),
        kind: str = "reply",
    ) -> bool:
        persona_id = _require_persona_id(persona_id)

        def operation(db):
            cursor = db.execute(
                """
                INSERT OR IGNORE INTO outbox(
                    persona_id, decision_id, group_id, text, created_at, expires_at, sent_at,
                    status, attempt, quote_message_id, segments_json, outbound_json,
                    kind
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, 'pending', 0, ?, ?, ?, ?)
                """,
                (
                    persona_id,
                    decision_id,
                    group_id,
                    text,
                    int(created_at),
                    expires_at,
                    quote_message_id,
                    json.dumps(tuple(segments), ensure_ascii=False),
                    self._serialize_outbound(outbound),
                    str(kind),
                ),
            )
            return cursor.rowcount == 1

        return bool(self._write(operation))

    async def enqueue_outbox_async(
        self,
        persona_id: str,
        decision_id: str,
        group_id: str,
        text: str,
        created_at: int,
        expires_at: Optional[int] = None,
        *,
        quote_message_id: Optional[str] = None,
        segments: Sequence[str] = (),
        outbound: Sequence[OutboundSegment] = (),
        kind: str = "reply",
    ) -> bool:
        persona_id = _require_persona_id(persona_id)
        return bool(
            await self._write_async(
                lambda db: self._enqueue_outbox_on(
                    db,
                    persona_id,
                    decision_id,
                    group_id,
                    text,
                    created_at,
                    expires_at,
                    quote_message_id=quote_message_id,
                    segments=segments,
                    outbound=outbound,
                    kind=kind,
                )
            )
        )

    @staticmethod
    def _enqueue_outbox_on(
        db,
        persona_id,
        decision_id,
        group_id,
        text,
        created_at,
        expires_at=None,
        *,
        quote_message_id=None,
        segments=(),
        outbound=(),
        kind="reply"
    ):
        persona_id = _require_persona_id(persona_id)
        cursor = db.execute(
            """
            INSERT OR IGNORE INTO outbox(
                persona_id, decision_id, group_id, text, created_at, expires_at, sent_at,
                status, attempt, quote_message_id, segments_json, outbound_json,
                kind
            ) VALUES (?, ?, ?, ?, ?, ?, NULL, 'pending', 0, ?, ?, ?, ?)
            """,
            (
                persona_id,
                decision_id,
                group_id,
                text,
                int(created_at),
                expires_at,
                quote_message_id,
                json.dumps(tuple(segments), ensure_ascii=False),
                SQLiteMemoryStore._serialize_outbound(outbound),
                str(kind),
            ),
        )
        return cursor.rowcount == 1

    @staticmethod
    def _serialize_outbound(outbound: Sequence[OutboundSegment]) -> str:
        items = []
        for segment in tuple(outbound or ()):
            if not isinstance(segment, OutboundSegment):
                raise TypeError("outbound values must be OutboundSegment instances")
            items.append(
                {
                    "kind": segment.kind.value,
                    "text": segment.text,
                    "media_id": segment.media_id,
                    "media_ref": segment.media_ref,
                    "target_user_id": segment.target_user_id,
                }
            )
        return json.dumps(items, ensure_ascii=False, separators=(",", ":"))

    def pending_outbox(self, persona_id: str, now: int) -> List[Dict[str, Any]]:
        persona_id = _require_persona_id(persona_id)
        rows = self._db.execute(
            """
            SELECT * FROM outbox
            WHERE persona_id = ? AND status = 'pending'
              AND (expires_at IS NULL OR expires_at > ?)
            ORDER BY created_at ASC
            """,
            (persona_id, int(now)),
        ).fetchall()
        return [dict(row) for row in rows]

    def outbox_record(
        self, persona_id: str, decision_id: str
    ) -> Optional[Dict[str, Any]]:
        persona_id = _require_persona_id(persona_id)
        row = self._db.execute(
            "SELECT * FROM outbox WHERE persona_id=? AND decision_id=?",
            (persona_id, str(decision_id)),
        ).fetchone()
        return dict(row) if row else None

    async def transition_outbox_async(
        self,
        persona_id: str,
        decision_id: str,
        expected: str,
        status: str,
        *,
        failure_code: str = "",
        failure_detail: str = "",
        increment_attempt: bool = False
    ) -> bool:
        persona_id = _require_persona_id(persona_id)

        def operation(db):
            cursor = db.execute(
                """
                UPDATE outbox
                SET status=?, failure_code=?, failure_detail=?,
                    attempt=attempt + ?
                WHERE persona_id=? AND decision_id=? AND status=?
                """,
                (
                    str(status),
                    failure_code or None,
                    (failure_detail or "")[:500] or None,
                    int(bool(increment_attempt)),
                    persona_id,
                    decision_id,
                    str(expected),
                ),
            )
            return cursor.rowcount == 1

        return bool(await self._write_async(operation))

    async def finalize_delivery_async(
        self,
        persona_id: str,
        decision_id: str,
        sent_at: int,
        bot_message: ChatMessage,
        reason: str = "sent",
    ) -> bool:
        persona_id = _require_persona_id(persona_id)

        def operation(db):
            row = db.execute(
                "SELECT status FROM outbox WHERE persona_id=? AND decision_id=?",
                (persona_id, decision_id),
            ).fetchone()
            if row is None or row["status"] == "sent":
                return False
            if row["status"] != "sending":
                return False
            self._insert_message(db, persona_id, bot_message)
            db.execute(
                "UPDATE outbox SET status='sent', sent_at=?, "
                "failure_code=NULL, failure_detail=NULL "
                "WHERE persona_id=? AND decision_id=?",
                (int(sent_at), persona_id, decision_id),
            )
            db.execute(
                "INSERT INTO decisions(persona_id, decision_id, group_id, state, reason, timestamp) "
                "VALUES (?, ?, ?, 'SEND', ?, ?)",
                (persona_id, decision_id, bot_message.group_id, reason, int(sent_at)),
            )
            db.execute(
                "INSERT INTO decisions(persona_id, decision_id, group_id, state, reason, timestamp) "
                "VALUES (?, ?, ?, 'END', ?, ?)",
                (persona_id, decision_id, bot_message.group_id, reason, int(sent_at)),
            )
            return True

        return bool(await self._write_async(operation))

    async def mark_all_sending_unknown_async(self) -> int:
        def operation(db):
            cursor = db.execute(
                "UPDATE outbox SET status='unknown', failure_code='shutdown' "
                "WHERE status='sending'"
            )
            return cursor.rowcount

        return int(await self._write_async(operation))

    async def mark_sending_unknown_async(self) -> int:
        """Compatibility-free operational alias for host shutdown recovery."""
        return await self.mark_all_sending_unknown_async()

    def list_ledger_messages(
        self, persona_id: str, group_id: str, limit: int = 100
    ) -> List[ChatMessage]:
        persona_id = _require_persona_id(persona_id)
        rows = self._db.execute(
            """
            SELECT * FROM messages
            WHERE persona_id = ? AND group_id = ?
            ORDER BY timestamp ASC, rowid ASC
            LIMIT ?
            """,
            (persona_id, str(group_id), max(0, int(limit))),
        ).fetchall()
        return [self._row_to_message(row) for row in rows]

    def list_bot_deliveries(
        self, persona_id: str, group_id: str, limit: int = 20
    ) -> List[ChatMessage]:
        persona_id = _require_persona_id(persona_id)
        rows = self._db.execute(
            """
            SELECT * FROM messages
            WHERE persona_id = ? AND group_id = ? AND origin = 'BOT_DELIVERY'
              AND decision_id IS NOT NULL AND decision_id != ''
            ORDER BY timestamp DESC, rowid DESC
            LIMIT ?
            """,
            (persona_id, str(group_id), max(0, int(limit))),
        ).fetchall()
        return [self._row_to_message(row) for row in reversed(rows)]

    def list_candidate_sent_at(
        self, persona_id: str, group_id: str, since: int
    ) -> List[int]:
        persona_id = _require_persona_id(persona_id)
        rows = self._db.execute(
            """
            SELECT sent_at FROM outbox
            WHERE persona_id = ? AND group_id = ? AND status = 'sent'
              AND kind IN ('reply', 'candidate')
              AND sent_at IS NOT NULL AND sent_at > ?
            ORDER BY sent_at ASC
            """,
            (persona_id, str(group_id), int(since)),
        ).fetchall()
        # Spontaneous/candidate deliveries are tagged as kind=reply by workflow;
        # use BOT_DELIVERY messages joined with outbox kind when available.
        return [int(row["sent_at"]) for row in rows]

    def list_spontaneous_sent_at(
        self, persona_id: str, group_id: str, since: int
    ) -> List[int]:
        persona_id = _require_persona_id(persona_id)
        rows = self._db.execute(
            """
            SELECT sent_at FROM outbox
            WHERE persona_id = ? AND group_id = ?
              AND status = 'sent' AND kind = 'candidate'
              AND sent_at IS NOT NULL AND sent_at > ?
            ORDER BY sent_at ASC
            """,
            (persona_id, str(group_id), int(since)),
        ).fetchall()
        return [int(row["sent_at"]) for row in rows]

    def latest_open_topic_epoch(
        self, persona_id: str, group_id: str
    ) -> Optional[Dict[str, Any]]:
        persona_id = _require_persona_id(persona_id)
        row = self._db.execute(
            """
            SELECT * FROM topic_epochs
            WHERE persona_id = ? AND group_id = ? AND closed_at IS NULL
            ORDER BY opened_at DESC
            LIMIT 1
            """,
            (persona_id, str(group_id)),
        ).fetchone()
        return dict(row) if row else None

    def open_topic_epoch(
        self,
        persona_id: str,
        group_id: str,
        topic_id: str,
        opened_at: int,
        last_message_id: Optional[str] = None,
    ) -> bool:
        persona_id = _require_persona_id(persona_id)
        def operation(db):
            return self._open_topic_epoch_on(
                db,
                persona_id,
                group_id,
                topic_id,
                opened_at,
                last_message_id,
            )

        return bool(self._write(operation))

    async def open_topic_epoch_async(
        self,
        persona_id: str,
        group_id: str,
        topic_id: str,
        opened_at: int,
        last_message_id: Optional[str] = None,
        close_existing_reason: str = "HARD_WAKE",
    ) -> bool:
        persona_id = _require_persona_id(persona_id)
        return bool(
            await self._write_async(
                lambda db: self._open_topic_epoch_on(
                    db,
                    persona_id,
                    group_id,
                    topic_id,
                    opened_at,
                    last_message_id,
                    close_existing_reason,
                )
            )
        )

    @staticmethod
    def _open_topic_epoch_on(
        db,
        persona_id: str,
        group_id: str,
        topic_id: str,
        opened_at: int,
        last_message_id: Optional[str] = None,
        close_existing_reason: str = "HARD_WAKE",
    ) -> bool:
        persona_id = _require_persona_id(persona_id)
        open_row = db.execute(
            "SELECT topic_id FROM topic_epochs "
            "WHERE persona_id=? AND group_id=? AND closed_at IS NULL LIMIT 1",
            (persona_id, str(group_id)),
        ).fetchone()
        if open_row is not None:
            db.execute(
                "UPDATE topic_epochs SET closed_at=?, close_reason=? "
                "WHERE persona_id=? AND group_id=? AND topic_id=? AND closed_at IS NULL",
                (
                    int(opened_at),
                    str(close_existing_reason),
                    persona_id,
                    str(group_id),
                    open_row["topic_id"],
                ),
            )
        db.execute(
            """
            INSERT INTO topic_epochs(
                persona_id, group_id, topic_id, opened_at, closed_at,
                close_reason, last_message_id
            ) VALUES (?, ?, ?, ?, NULL, NULL, ?)
            """,
            (
                persona_id,
                str(group_id),
                str(topic_id),
                int(opened_at),
                last_message_id,
            ),
        )
        return True

    def close_topic_epoch(
        self,
        persona_id: str,
        group_id: str,
        topic_id: str,
        closed_at: int,
        close_reason: str,
        last_message_id: Optional[str] = None,
    ) -> bool:
        persona_id = _require_persona_id(persona_id)
        def operation(db):
            return self._close_topic_epoch_on(
                db,
                persona_id,
                group_id,
                topic_id,
                closed_at,
                close_reason,
                last_message_id,
            )

        return bool(self._write(operation))

    async def close_topic_epoch_async(
        self,
        persona_id: str,
        group_id: str,
        topic_id: str,
        closed_at: int,
        close_reason: str,
        last_message_id: Optional[str] = None,
    ) -> bool:
        persona_id = _require_persona_id(persona_id)
        return bool(
            await self._write_async(
                lambda db: self._close_topic_epoch_on(
                    db,
                    persona_id,
                    group_id,
                    topic_id,
                    closed_at,
                    close_reason,
                    last_message_id,
                )
            )
        )

    @staticmethod
    def _close_topic_epoch_on(
        db,
        persona_id: str,
        group_id: str,
        topic_id: str,
        closed_at: int,
        close_reason: str,
        last_message_id: Optional[str] = None,
    ) -> bool:
        persona_id = _require_persona_id(persona_id)
        cursor = db.execute(
            """
            UPDATE topic_epochs
            SET closed_at=?, close_reason=?, last_message_id=COALESCE(?, last_message_id)
            WHERE persona_id=? AND group_id=? AND topic_id=? AND closed_at IS NULL
            """,
            (
                int(closed_at),
                str(close_reason),
                last_message_id,
                persona_id,
                str(group_id),
                str(topic_id),
            ),
        )
        return cursor.rowcount == 1

    def grant_continuation(
        self,
        *,
        persona_id: str,
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
        persona_id = _require_persona_id(persona_id)
        absolute = int(granted_at) + max(1, int(max_total_seconds))
        def operation(db):
            db.execute(
                """
                INSERT INTO continuation_grants(
                    grant_id, persona_id, group_id, sender_id, opened_by_decision_id,
                    opened_by_message_id, trigger_kind, granted_at, expires_at,
                    max_total_seconds, absolute_deadline_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(grant_id),
                    persona_id,
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

    async def grant_continuation_async(
        self,
        *,
        persona_id: str,
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
        persona_id = _require_persona_id(persona_id)
        return bool(
            await self._write_async(
                lambda db: self._grant_continuation_on(
                    db,
                    persona_id=persona_id,
                    grant_id=grant_id,
                    group_id=group_id,
                    sender_id=sender_id,
                    opened_by_decision_id=opened_by_decision_id,
                    opened_by_message_id=opened_by_message_id,
                    trigger_kind=trigger_kind,
                    granted_at=granted_at,
                    expires_at=expires_at,
                    max_total_seconds=max_total_seconds,
                )
            )
        )

    @staticmethod
    def _grant_continuation_on(db, **kwargs) -> bool:
        persona_id = _require_persona_id(kwargs.get("persona_id"))
        granted_at = int(kwargs["granted_at"])
        max_total_seconds = max(1, int(kwargs["max_total_seconds"]))
        db.execute(
            """
            INSERT INTO continuation_grants(
                grant_id, persona_id, group_id, sender_id, opened_by_decision_id,
                opened_by_message_id, trigger_kind, granted_at, expires_at,
                max_total_seconds, absolute_deadline_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(kwargs["grant_id"]),
                persona_id,
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
        self,
        persona_id: str,
        group_id: str,
        now: int,
        sender_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        persona_id = _require_persona_id(persona_id)
        sql = """
            SELECT * FROM continuation_grants
            WHERE persona_id = ? AND group_id = ?
              AND expires_at >= ?
              AND absolute_deadline_at >= ?
        """
        params: List[Any] = [persona_id, str(group_id), int(now), int(now)]
        if sender_id is not None:
            sql += " AND sender_id = ?"
            params.append(str(sender_id))
        sql += " ORDER BY granted_at DESC LIMIT 1"
        row = self._db.execute(sql, tuple(params)).fetchone()
        return dict(row) if row else None

    def list_active_continuation_grants(
        self, persona_id: str, group_id: str, now: int
    ) -> List[Dict[str, Any]]:
        persona_id = _require_persona_id(persona_id)
        rows = self._db.execute(
            """
            SELECT * FROM continuation_grants
            WHERE persona_id = ? AND group_id = ?
              AND expires_at >= ?
              AND absolute_deadline_at >= ?
            ORDER BY granted_at DESC
            """,
            (persona_id, str(group_id), int(now), int(now)),
        ).fetchall()
        latest_by_sender: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            item = dict(row)
            latest_by_sender.setdefault(str(item["sender_id"]), item)
        return sorted(
            latest_by_sender.values(), key=lambda item: int(item["granted_at"])
        )

    def append_social_event(self, persona_id: str, event: SocialEvent) -> bool:
        persona_id = _require_persona_id(persona_id)
        kind = event.kind
        if not isinstance(kind, SocialEventKind):
            kind = SocialEventKind(str(kind))

        def operation(db):
            cursor = db.execute(
                """
                INSERT OR IGNORE INTO social_events(
                    event_id, persona_id, group_id, user_id, kind, source_message_id,
                    confidence, occurred_at, decision_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(event.event_id),
                    persona_id,
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
        persona_id: str,
        group_id: str,
        user_id: Optional[str] = None,
        limit: int = 200,
    ) -> List[SocialEvent]:
        persona_id = _require_persona_id(persona_id)
        sql = """
            SELECT * FROM social_events
            WHERE persona_id = ? AND group_id = ?
        """
        params: List[Any] = [persona_id, str(group_id)]
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
        self, persona_id: str, group_id: str, user_id: str
    ) -> Optional[RelationshipState]:
        persona_id = _require_persona_id(persona_id)
        row = self._db.execute(
            """
            SELECT * FROM relationship_state
            WHERE persona_id = ? AND group_id = ? AND user_id = ?
            """,
            (persona_id, str(group_id), str(user_id)),
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

    def upsert_relationship_state(
        self, persona_id: str, state: RelationshipState
    ) -> None:
        persona_id = _require_persona_id(persona_id)
        def operation(db):
            db.execute(
                """
                INSERT INTO relationship_state(
                    persona_id, group_id, user_id, familiarity, affinity, trust,
                    boundary_pressure, interaction_count, last_interaction_at,
                    configured_relationship, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(persona_id, group_id, user_id) DO UPDATE SET
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
                    persona_id,
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
        persona_id: str,
        group_id: str,
        user_id: str,
        *,
        configured_relationship: Optional[str] = None,
        seed_affinity: int = 0,
        now: int = 0,
    ) -> RelationshipState:
        persona_id = _require_persona_id(persona_id)
        from ..social.projector import SocialStateProjector

        events = self.list_social_events(
            persona_id, group_id, user_id=user_id, limit=5000
        )
        state = SocialStateProjector().project(
            events,
            group_id=group_id,
            user_id=user_id,
            configured_relationship=configured_relationship,
            seed_affinity=int(seed_affinity),
            now=int(now),
        )
        self.upsert_relationship_state(persona_id, state)
        return state

    def record_social_interaction(
        self,
        persona_id: str,
        event: SocialEvent,
        *,
        configured_relationship: Optional[str] = None,
        now: int = 0,
    ) -> Optional[RelationshipState]:
        """幂等写入事件并增量投影；重复 source 返回已有状态且不双计。"""
        persona_id = _require_persona_id(persona_id)
        from ..social.affinity import initial_affinity_for_relationship
        from ..social.projector import SocialStateProjector

        inserted = self.append_social_event(persona_id, event)
        current = self.get_relationship_state(
            persona_id, event.group_id, event.user_id
        )
        if current is None:
            current = RelationshipState(
                group_id=event.group_id,
                user_id=event.user_id,
                affinity=initial_affinity_for_relationship(
                    configured_relationship or ""
                ),
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
        )
        self.upsert_relationship_state(persona_id, state)
        return state

    def close(self) -> None:
        self._writer.close()
        self._db.close()

    async def flush_async(self) -> None:
        await self._writer.flush_async()
