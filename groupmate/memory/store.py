"""SQLite persistence for bounded chat context and social memory."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from uuid import uuid4

from ..models import (
    CandidateStatus,
    ChatMessage,
    ContinuityItem,
    ContinuityKind,
    ContinuityStatus,
    MemoryCandidate,
    MemoryItem,
    MemoryKind,
    MemoryScope,
    MemoryStatus,
    MessageOrigin,
    OutboundSegment,
    RelationshipState,
    Sensitivity,
    SelfCommitment,
    SelfCommitmentStatus,
    SocialEvent,
    SocialEventKind,
    SocialEventStatus,
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
        inserted = cursor.rowcount == 1
        if inserted:
            if not message.is_bot:
                cls._observe_member_row(
                    db,
                    persona_id,
                    message.group_id,
                    message.sender_id,
                    message.sender_name,
                    message.timestamp,
                )
            mention_names = message.metadata.get("mention_names") or {}
            if isinstance(mention_names, dict):
                for subject_id in message.mentioned_user_ids:
                    cls._observe_member_row(
                        db,
                        persona_id,
                        message.group_id,
                        subject_id,
                        mention_names.get(str(subject_id), ""),
                        message.timestamp,
                    )
        return inserted

    @staticmethod
    def _nickname_history(value: Any) -> List[Dict[str, Any]]:
        try:
            items = json.loads(value or "[]") if not isinstance(value, list) else value
        except (TypeError, ValueError):
            items = []
        history = []
        for item in items if isinstance(items, list) else ():
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            history.append(
                {
                    "name": name,
                    "first_seen_at": int(item.get("first_seen_at") or 0),
                    "last_seen_at": int(item.get("last_seen_at") or 0),
                }
            )
        return history[-30:]

    @classmethod
    def _observe_member_row(
        cls,
        db,
        persona_id: str,
        group_id: str,
        subject_id: str,
        display_name: str,
        seen_at: int,
    ) -> bool:
        group_id = str(group_id or "").strip()
        subject_id = str(subject_id or "").strip()
        display_name = str(display_name or "").strip().lstrip("@").strip()[:80]
        if not group_id or not subject_id or subject_id in {"0", "all"}:
            return False
        existing = db.execute(
            "SELECT * FROM profiles WHERE persona_id=? AND group_id=? AND subject_id=?",
            (persona_id, group_id, subject_id),
        ).fetchone()
        if display_name == subject_id:
            display_name = ""
        if existing is None and not display_name:
            return False
        timestamp = max(0, int(seen_at or 0))
        history = cls._nickname_history(
            existing["nickname_history_json"] if existing is not None else "[]"
        )
        if display_name:
            matched = next((item for item in history if item["name"] == display_name), None)
            if matched is None:
                history.append(
                    {
                        "name": display_name,
                        "first_seen_at": timestamp,
                        "last_seen_at": timestamp,
                    }
                )
            else:
                matched["last_seen_at"] = max(matched["last_seen_at"], timestamp)
        if existing is None:
            db.execute(
                """
                INSERT INTO profiles(
                    persona_id, group_id, subject_id, display_name, relationship,
                    authority, updated_at, preferred_address, nickname_history_json,
                    first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, '', 0, ?, '', ?, ?, ?)
                """,
                (
                    persona_id,
                    group_id,
                    subject_id,
                    display_name or "群成员",
                    timestamp,
                    json.dumps(history[-30:], ensure_ascii=False),
                    timestamp,
                    timestamp,
                ),
            )
            return True
        db.execute(
            "UPDATE profiles SET display_name=?, nickname_history_json=?, "
            "last_seen_at=?, first_seen_at=CASE WHEN first_seen_at=0 THEN ? "
            "ELSE MIN(first_seen_at, ?) END, updated_at=MAX(updated_at, ?) "
            "WHERE persona_id=? AND group_id=? AND subject_id=?",
            (
                display_name or str(existing["display_name"]),
                json.dumps(history[-30:], ensure_ascii=False),
                max(int(existing["last_seen_at"] or 0), timestamp),
                timestamp,
                timestamp,
                timestamp,
                persona_id,
                group_id,
                subject_id,
            ),
        )
        return True

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

    def get_message(
        self, persona_id: str, group_id: str, message_id: str
    ) -> Optional[ChatMessage]:
        persona_id = _require_persona_id(persona_id)
        row = self._db.execute(
            "SELECT * FROM messages WHERE persona_id=? AND group_id=? AND message_id=?",
            (persona_id, str(group_id), str(message_id)),
        ).fetchone()
        return self._row_to_message(row) if row is not None else None

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
                    persona_id, group_id, subject_id, display_name, relationship,
                    authority, updated_at, nickname_history_json, first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(persona_id, group_id, subject_id) DO UPDATE SET
                    display_name = excluded.display_name,
                    relationship = excluded.relationship,
                    authority = excluded.authority,
                    updated_at = excluded.updated_at,
                    nickname_history_json = excluded.nickname_history_json,
                    first_seen_at = CASE WHEN profiles.first_seen_at=0
                        THEN excluded.first_seen_at ELSE profiles.first_seen_at END,
                    last_seen_at = MAX(profiles.last_seen_at, excluded.last_seen_at)
                """,
                (
                    persona_id,
                    str(group_id),
                    str(subject_id),
                    display_name.strip(),
                    relationship.strip(),
                    int(authority),
                    int(updated_at),
                    json.dumps(
                        [{
                            "name": display_name.strip(),
                            "first_seen_at": int(updated_at),
                            "last_seen_at": int(updated_at),
                        }] if display_name.strip() else [],
                        ensure_ascii=False,
                    ),
                    int(updated_at),
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

    def resolve_member_subject_id(
        self, persona_id: str, group_id: str, subject_id: str
    ) -> str:
        persona_id = _require_persona_id(persona_id)
        current = str(subject_id)
        seen = {current}
        for _ in range(8):
            row = self._db.execute(
                "SELECT canonical_subject_id FROM member_identity_links "
                "WHERE persona_id=? AND group_id=? AND source_subject_id=? AND active=1",
                (persona_id, str(group_id), current),
            ).fetchone()
            if row is None:
                return current
            target = str(row["canonical_subject_id"])
            if not target or target in seen:
                return current
            current = target
            seen.add(current)
        return current

    def member_subject_ids(
        self, persona_id: str, group_id: str, subject_id: str
    ) -> Tuple[str, ...]:
        persona_id = _require_persona_id(persona_id)
        canonical = self.resolve_member_subject_id(persona_id, group_id, subject_id)
        rows = self._db.execute(
            "SELECT source_subject_id FROM member_identity_links "
            "WHERE persona_id=? AND group_id=? AND canonical_subject_id=? AND active=1",
            (persona_id, str(group_id), canonical),
        ).fetchall()
        return tuple(
            dict.fromkeys(
                [canonical]
                + [str(row["source_subject_id"]) for row in rows]
            )
        )

    def member_display_name(
        self, persona_id: str, group_id: str, subject_id: str
    ) -> str:
        persona_id = _require_persona_id(persona_id)
        canonical = self.resolve_member_subject_id(persona_id, group_id, subject_id)
        profile = self.get_profile(persona_id, group_id, canonical)
        if not profile:
            return ""
        preferred = str(profile.get("preferred_address") or "").strip()
        display = str(profile.get("display_name") or "").strip()
        if preferred:
            return preferred
        return "" if display == canonical else display

    def member_name_index(
        self, persona_id: str, group_id: str
    ) -> Dict[str, str]:
        """Return only unambiguous human names, mapped to canonical subjects."""
        persona_id = _require_persona_id(persona_id)
        rows = self._db.execute(
            "SELECT * FROM profiles WHERE persona_id=? AND group_id=?",
            (persona_id, str(group_id)),
        ).fetchall()
        candidates: Dict[str, set] = {}
        for row in rows:
            subject_id = str(row["subject_id"])
            canonical = self.resolve_member_subject_id(
                persona_id, group_id, subject_id
            )
            names = [
                str(row["preferred_address"] or "").strip(),
                str(row["display_name"] or "").strip(),
            ]
            names.extend(
                item["name"]
                for item in self._nickname_history(row["nickname_history_json"])
            )
            for name in names:
                key = name.lstrip("@").strip().lower()
                if not key or key == subject_id.lower():
                    continue
                candidates.setdefault(key, set()).add(canonical)
        return {
            name: next(iter(subjects)) if len(subjects) == 1 else ""
            for name, subjects in candidates.items()
        }

    @classmethod
    def _profile_payload(cls, row) -> Dict[str, Any]:
        preferred = str(row["preferred_address"] or "").strip()
        display_name = str(row["display_name"] or "群成员").strip()
        return {
            "group_id": str(row["group_id"]),
            "subject_id": str(row["subject_id"]),
            "display_name": display_name,
            "preferred_address": preferred,
            "address": preferred or display_name,
            "relationship": str(row["relationship"] or ""),
            "authority": int(row["authority"] or 0),
            "nickname_history": cls._nickname_history(row["nickname_history_json"]),
            "first_seen_at": int(row["first_seen_at"] or 0),
            "last_seen_at": int(row["last_seen_at"] or row["updated_at"] or 0),
            "updated_at": int(row["updated_at"] or 0),
        }

    def list_member_profiles(
        self,
        persona_id: str,
        *,
        group_id: Optional[str] = None,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        persona_id = _require_persona_id(persona_id)
        sql = (
            "SELECT p.*, l.canonical_subject_id FROM profiles p "
            "LEFT JOIN member_identity_links l ON l.persona_id=p.persona_id "
            "AND l.group_id=p.group_id AND l.source_subject_id=p.subject_id "
            "AND l.active=1 WHERE p.persona_id=?"
        )
        params: List[Any] = [persona_id]
        if group_id is not None:
            sql += " AND p.group_id=?"
            params.append(str(group_id))
        sql += " ORDER BY p.last_seen_at DESC, p.updated_at DESC LIMIT ?"
        params.append(max(1, min(1000, int(limit))))
        rows = self._db.execute(sql, tuple(params)).fetchall()
        names = {
            (str(row["group_id"]), str(row["subject_id"])): self._profile_payload(row)["address"]
            for row in rows
        }
        items = []
        for row in rows:
            item = self._profile_payload(row)
            canonical = str(row["canonical_subject_id"] or "").strip()
            item["canonical_subject_id"] = canonical
            item["identity_status"] = "linked" if canonical else "independent"
            item["canonical_name"] = names.get((item["group_id"], canonical), "")
            items.append(item)
        return items

    @staticmethod
    def _row_to_continuity(row) -> ContinuityItem:
        return ContinuityItem(
            item_id=str(row["item_id"]),
            group_id=str(row["group_id"]),
            subject_id=str(row["subject_id"]),
            kind=ContinuityKind(str(row["kind"])),
            summary=str(row["summary"]),
            source_message_id=str(row["source_message_id"]),
            source_quote=str(row["source_quote"]),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
            due_at=row["due_at"],
            confidence=float(row["confidence"]),
            extractor_version=str(row["extractor_version"]),
            status=ContinuityStatus(str(row["status"])),
            resolution_message_id=row["resolution_message_id"],
            resolution_quote=str(row["resolution_quote"] or ""),
            resolved_at=row["resolved_at"],
        )

    def append_continuity_item(
        self, persona_id: str, item: ContinuityItem
    ) -> Optional[ContinuityItem]:
        persona_id = _require_persona_id(persona_id)

        def operation(db):
            cursor = db.execute(
                """
                INSERT OR IGNORE INTO continuity_items(
                    item_id, persona_id, group_id, subject_id, kind, summary,
                    source_message_id, source_quote, created_at, updated_at,
                    due_at, confidence, extractor_version, status,
                    resolution_message_id, resolution_quote, resolved_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.item_id,
                    persona_id,
                    item.group_id,
                    item.subject_id,
                    item.kind.value,
                    item.summary,
                    item.source_message_id,
                    item.source_quote,
                    item.created_at,
                    item.updated_at,
                    item.due_at,
                    item.confidence,
                    item.extractor_version,
                    item.status.value,
                    item.resolution_message_id,
                    item.resolution_quote,
                    item.resolved_at,
                ),
            )
            if cursor.rowcount != 1:
                return None
            return db.execute(
                "SELECT * FROM continuity_items WHERE persona_id=? AND item_id=?",
                (persona_id, item.item_id),
            ).fetchone()

        row = self._write(operation)
        return self._row_to_continuity(row) if row is not None else None

    def get_continuity_item(
        self, persona_id: str, item_id: str
    ) -> Optional[ContinuityItem]:
        persona_id = _require_persona_id(persona_id)
        row = self._db.execute(
            "SELECT * FROM continuity_items WHERE persona_id=? AND item_id=?",
            (persona_id, str(item_id)),
        ).fetchone()
        return self._row_to_continuity(row) if row is not None else None

    def list_continuity_items(
        self,
        persona_id: str,
        *,
        group_id: Optional[str] = None,
        subject_id: Optional[str] = None,
        subject_ids: Optional[Sequence[str]] = None,
        statuses: Optional[Sequence[ContinuityStatus]] = None,
        limit: int = 100,
    ) -> List[ContinuityItem]:
        persona_id = _require_persona_id(persona_id)
        sql = "SELECT * FROM continuity_items WHERE persona_id=?"
        params: List[Any] = [persona_id]
        if group_id is not None:
            sql += " AND group_id=?"
            params.append(str(group_id))
        selected_subjects = tuple(
            dict.fromkeys(str(item) for item in (subject_ids or ()) if str(item))
        )
        if subject_id is not None and not selected_subjects:
            selected_subjects = (str(subject_id),)
        if selected_subjects:
            sql += " AND subject_id IN ({})".format(
                ",".join("?" for _ in selected_subjects)
            )
            params.extend(selected_subjects)
        selected_statuses = tuple(statuses or ())
        if selected_statuses:
            sql += " AND status IN ({})".format(
                ",".join("?" for _ in selected_statuses)
            )
            params.extend(
                item.value if isinstance(item, ContinuityStatus) else str(item)
                for item in selected_statuses
            )
        sql += " ORDER BY updated_at DESC, created_at DESC LIMIT ?"
        params.append(max(1, min(500, int(limit))))
        rows = self._db.execute(sql, tuple(params)).fetchall()
        return [self._row_to_continuity(row) for row in rows]

    def resolve_continuity_item(
        self,
        persona_id: str,
        item_id: str,
        *,
        status: ContinuityStatus,
        resolution_message_id: str,
        resolution_quote: str,
        resolved_at: int,
    ) -> Optional[ContinuityItem]:
        persona_id = _require_persona_id(persona_id)
        next_status = (
            status
            if isinstance(status, ContinuityStatus)
            else ContinuityStatus(str(status))
        )
        if next_status not in {ContinuityStatus.COMPLETED, ContinuityStatus.CANCELLED}:
            raise ValueError("continuity resolution must complete or cancel")

        def operation(db):
            cursor = db.execute(
                "UPDATE continuity_items SET status=?, resolution_message_id=?, "
                "resolution_quote=?, resolved_at=?, updated_at=? "
                "WHERE persona_id=? AND item_id=? AND status='open'",
                (
                    next_status.value,
                    str(resolution_message_id or ""),
                    str(resolution_quote or "")[:180],
                    int(resolved_at),
                    int(resolved_at),
                    persona_id,
                    str(item_id),
                ),
            )
            if cursor.rowcount != 1:
                return None
            return db.execute(
                "SELECT * FROM continuity_items WHERE persona_id=? AND item_id=?",
                (persona_id, str(item_id)),
            ).fetchone()

        row = self._write(operation)
        return self._row_to_continuity(row) if row is not None else None

    @staticmethod
    def _continuity_payload(item: Optional[ContinuityItem]) -> Optional[Dict[str, Any]]:
        if item is None:
            return None
        return {
            "item_id": item.item_id,
            "group_id": item.group_id,
            "subject_id": item.subject_id,
            "kind": item.kind.value,
            "summary": item.summary,
            "source_message_id": item.source_message_id,
            "source_quote": item.source_quote,
            "created_at": item.created_at,
            "updated_at": item.updated_at,
            "due_at": item.due_at,
            "confidence": item.confidence,
            "extractor_version": item.extractor_version,
            "status": item.status.value,
            "resolution_message_id": item.resolution_message_id,
            "resolution_quote": item.resolution_quote,
            "resolved_at": item.resolved_at,
        }

    def update_continuity_with_audit(
        self,
        persona_id: str,
        item_id: str,
        *,
        status: ContinuityStatus,
        reason: str,
        actor: str,
        now: int,
    ) -> Optional[Dict[str, Any]]:
        persona_id = _require_persona_id(persona_id)
        next_status = (
            status
            if isinstance(status, ContinuityStatus)
            else ContinuityStatus(str(status))
        )
        if next_status not in {
            ContinuityStatus.OPEN,
            ContinuityStatus.COMPLETED,
            ContinuityStatus.CANCELLED,
            ContinuityStatus.DELETED,
        }:
            raise ValueError("unsupported continuity status")
        action_id = str(uuid4())

        def operation(db):
            row = db.execute(
                "SELECT * FROM continuity_items WHERE persona_id=? AND item_id=?",
                (persona_id, str(item_id)),
            ).fetchone()
            if row is None:
                return None
            current = self._row_to_continuity(row)
            if current.status is next_status:
                raise ValueError("continuity item already has this status")
            before = self._continuity_payload(current)
            resolved_at = (
                int(now)
                if next_status in {
                    ContinuityStatus.COMPLETED,
                    ContinuityStatus.CANCELLED,
                }
                else None
            )
            db.execute(
                "UPDATE continuity_items SET status=?, updated_at=?, resolved_at=?, "
                "resolution_message_id=NULL, resolution_quote='' "
                "WHERE persona_id=? AND item_id=?",
                (
                    next_status.value,
                    int(now),
                    resolved_at,
                    persona_id,
                    str(item_id),
                ),
            )
            updated = self._row_to_continuity(
                db.execute(
                    "SELECT * FROM continuity_items WHERE persona_id=? AND item_id=?",
                    (persona_id, str(item_id)),
                ).fetchone()
            )
            after = self._continuity_payload(updated)
            self._insert_governance_action(
                db,
                action_id=action_id,
                persona_id=persona_id,
                action_type="continuity_status_corrected",
                target_kind="continuity_item",
                target_id=current.item_id,
                group_id=current.group_id,
                subject_id=current.subject_id,
                before=before,
                after=after,
                reason=reason,
                actor=actor,
                created_at=now,
            )
            return db.execute(
                "SELECT * FROM governance_actions WHERE action_id=?", (action_id,)
            ).fetchone()

        row = self._write(operation)
        return self._governance_payload(row) if row is not None else None

    @staticmethod
    def _row_to_self_commitment(row) -> SelfCommitment:
        try:
            facts = tuple(json.loads(row["result_facts_json"] or "[]"))
        except (TypeError, ValueError):
            facts = ()
        return SelfCommitment(
            commitment_id=str(row["commitment_id"]),
            group_id=str(row["group_id"]),
            beneficiary_subject_id=str(row["beneficiary_subject_id"]),
            summary=str(row["summary"]),
            source_decision_id=str(row["source_decision_id"]),
            source_message_id=str(row["source_message_id"]),
            request_message_id=str(row["request_message_id"] or ""),
            source_quote=str(row["source_quote"]),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
            status=SelfCommitmentStatus(str(row["status"])),
            required_capability=str(row["required_capability"] or ""),
            fulfillment_mode=str(row["fulfillment_mode"] or "follow_up"),
            due_at=row["due_at"],
            confidence=float(row["confidence"]),
            extractor_version=str(row["extractor_version"]),
            result_decision_id=row["result_decision_id"],
            result_quote=str(row["result_quote"] or ""),
            result_facts=facts,
            failure_code=str(row["failure_code"] or ""),
            resolved_at=row["resolved_at"],
            next_attempt_at=row["next_attempt_at"],
            attempt_count=int(row["attempt_count"] or 0),
            lease_owner=str(row["lease_owner"] or ""),
            lease_until=row["lease_until"],
            last_attempt_at=row["last_attempt_at"],
            last_delivery_at=row["last_delivery_at"],
        )

    def append_self_commitment(
        self, persona_id: str, item: SelfCommitment
    ) -> Optional[SelfCommitment]:
        persona_id = _require_persona_id(persona_id)

        def operation(db):
            cursor = db.execute(
                """
                INSERT OR IGNORE INTO self_commitments(
                    commitment_id, persona_id, group_id, beneficiary_subject_id,
                    summary, source_decision_id, source_message_id, source_quote,
                    created_at, updated_at, status, required_capability, due_at,
                    confidence, extractor_version, result_decision_id, result_quote,
                    result_facts_json, failure_code, resolved_at, request_message_id,
                    fulfillment_mode, next_attempt_at,
                    attempt_count, lease_owner, lease_until, last_attempt_at,
                    last_delivery_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.commitment_id,
                    persona_id,
                    item.group_id,
                    item.beneficiary_subject_id,
                    item.summary,
                    item.source_decision_id,
                    item.source_message_id,
                    item.source_quote,
                    item.created_at,
                    item.updated_at,
                    item.status.value,
                    item.required_capability,
                    item.due_at,
                    item.confidence,
                    item.extractor_version,
                    item.result_decision_id,
                    item.result_quote,
                    json.dumps(item.result_facts, ensure_ascii=False),
                    item.failure_code,
                    item.resolved_at,
                    item.request_message_id,
                    item.fulfillment_mode,
                    item.next_attempt_at,
                    item.attempt_count,
                    item.lease_owner,
                    item.lease_until,
                    item.last_attempt_at,
                    item.last_delivery_at,
                ),
            )
            if cursor.rowcount != 1:
                return None
            return db.execute(
                "SELECT * FROM self_commitments WHERE persona_id=? AND commitment_id=?",
                (persona_id, item.commitment_id),
            ).fetchone()

        row = self._write(operation)
        return self._row_to_self_commitment(row) if row is not None else None

    def get_self_commitment(
        self, persona_id: str, commitment_id: str
    ) -> Optional[SelfCommitment]:
        persona_id = _require_persona_id(persona_id)
        row = self._db.execute(
            "SELECT * FROM self_commitments WHERE persona_id=? AND commitment_id=?",
            (persona_id, str(commitment_id)),
        ).fetchone()
        return self._row_to_self_commitment(row) if row is not None else None

    def list_self_commitments(
        self,
        persona_id: str,
        *,
        group_id: Optional[str] = None,
        beneficiary_subject_ids: Optional[Sequence[str]] = None,
        statuses: Optional[Sequence[SelfCommitmentStatus]] = None,
        limit: int = 100,
    ) -> List[SelfCommitment]:
        persona_id = _require_persona_id(persona_id)
        sql = "SELECT * FROM self_commitments WHERE persona_id=?"
        params: List[Any] = [persona_id]
        if group_id is not None:
            sql += " AND group_id=?"
            params.append(str(group_id))
        subjects = tuple(
            dict.fromkeys(
                str(item) for item in (beneficiary_subject_ids or ()) if str(item)
            )
        )
        if subjects:
            sql += " AND beneficiary_subject_id IN ({})".format(
                ",".join("?" for _ in subjects)
            )
            params.extend(subjects)
        selected_statuses = tuple(statuses or ())
        if selected_statuses:
            sql += " AND status IN ({})".format(
                ",".join("?" for _ in selected_statuses)
            )
            params.extend(
                item.value if isinstance(item, SelfCommitmentStatus) else str(item)
                for item in selected_statuses
            )
        sql += " ORDER BY updated_at DESC, created_at DESC LIMIT ?"
        params.append(max(1, min(500, int(limit))))
        rows = self._db.execute(sql, tuple(params)).fetchall()
        return [self._row_to_self_commitment(row) for row in rows]

    def next_self_commitment_attempt_at(self, persona_id: str) -> Optional[int]:
        persona_id = _require_persona_id(persona_id)
        row = self._db.execute(
            "SELECT MIN(next_attempt_at) AS next_at FROM self_commitments "
            "WHERE persona_id=? AND status='pending' AND next_attempt_at IS NOT NULL",
            (persona_id,),
        ).fetchone()
        if row is None or row["next_at"] is None:
            return None
        return int(row["next_at"])

    def claim_due_self_commitments(
        self,
        persona_id: str,
        *,
        now: int,
        lease_owner: str,
        lease_seconds: int = 120,
        commitment_id: Optional[str] = None,
        limit: int = 10,
    ) -> List[SelfCommitment]:
        persona_id = _require_persona_id(persona_id)
        owner = str(lease_owner or "").strip()
        if not owner:
            raise ValueError("lease_owner is required")
        lease_until = int(now) + max(30, int(lease_seconds))

        def operation(db):
            sql = (
                "SELECT commitment_id FROM self_commitments WHERE persona_id=? "
                "AND status IN ('pending','in_progress') "
                "AND (lease_until IS NULL OR lease_until<=?) "
            )
            params: List[Any] = [persona_id, int(now)]
            if commitment_id is not None:
                sql += "AND commitment_id=? "
                params.append(str(commitment_id))
            else:
                sql += "AND next_attempt_at IS NOT NULL AND next_attempt_at<=? "
                params.append(int(now))
            sql += "ORDER BY next_attempt_at ASC, created_at ASC LIMIT ?"
            params.append(max(1, min(50, int(limit))))
            ids = [
                str(row["commitment_id"])
                for row in db.execute(sql, tuple(params)).fetchall()
            ]
            claimed = []
            for item_id in ids:
                cursor = db.execute(
                    "UPDATE self_commitments SET lease_owner=?, lease_until=?, "
                    "last_attempt_at=?, attempt_count=attempt_count+1, "
                    "status='in_progress', updated_at=? "
                    "WHERE persona_id=? AND commitment_id=? "
                    "AND status IN ('pending','in_progress') "
                    "AND (lease_until IS NULL OR lease_until<=?)",
                    (
                        owner,
                        lease_until,
                        int(now),
                        int(now),
                        persona_id,
                        item_id,
                        int(now),
                    ),
                )
                if cursor.rowcount == 1:
                    claimed.append(item_id)
            if not claimed:
                return []
            placeholders = ",".join("?" for _ in claimed)
            return db.execute(
                "SELECT * FROM self_commitments WHERE persona_id=? "
                "AND commitment_id IN ({}) ORDER BY next_attempt_at ASC, created_at ASC".format(
                    placeholders
                ),
                tuple([persona_id] + claimed),
            ).fetchall()

        return [self._row_to_self_commitment(row) for row in self._write(operation)]

    def finish_self_commitment_attempt(
        self,
        persona_id: str,
        commitment_id: str,
        *,
        lease_owner: str,
        status: SelfCommitmentStatus,
        now: int,
        result_decision_id: Optional[str] = None,
        result_quote: str = "",
        result_facts: Sequence[str] = (),
        failure_code: str = "",
        next_attempt_at: Optional[int] = None,
        delivered: bool = False,
    ) -> Optional[SelfCommitment]:
        persona_id = _require_persona_id(persona_id)
        next_status = (
            status
            if isinstance(status, SelfCommitmentStatus)
            else SelfCommitmentStatus(str(status))
        )
        if next_status not in {
            SelfCommitmentStatus.PENDING,
            SelfCommitmentStatus.COMPLETED,
            SelfCommitmentStatus.BLOCKED,
        }:
            raise ValueError("unsupported scheduler commitment status")
        resolved_at = int(now) if next_status in {
            SelfCommitmentStatus.COMPLETED,
            SelfCommitmentStatus.BLOCKED,
        } else None

        def operation(db):
            cursor = db.execute(
                "UPDATE self_commitments SET status=?, updated_at=?, "
                "result_decision_id=?, result_quote=?, result_facts_json=?, "
                "failure_code=?, resolved_at=?, next_attempt_at=?, "
                "lease_owner='', lease_until=NULL, "
                "last_delivery_at=CASE WHEN ? THEN ? ELSE last_delivery_at END "
                "WHERE persona_id=? AND commitment_id=? AND lease_owner=?",
                (
                    next_status.value,
                    int(now),
                    result_decision_id,
                    str(result_quote or "")[:180],
                    json.dumps(tuple(result_facts or ())[:8], ensure_ascii=False),
                    str(failure_code or "")[:80],
                    resolved_at,
                    next_attempt_at,
                    1 if delivered else 0,
                    int(now),
                    persona_id,
                    str(commitment_id),
                    str(lease_owner),
                ),
            )
            if cursor.rowcount != 1:
                return None
            return db.execute(
                "SELECT * FROM self_commitments WHERE persona_id=? AND commitment_id=?",
                (persona_id, str(commitment_id)),
            ).fetchone()

        row = self._write(operation)
        return self._row_to_self_commitment(row) if row is not None else None

    def resolve_self_commitment(
        self,
        persona_id: str,
        commitment_id: str,
        *,
        status: SelfCommitmentStatus,
        result_decision_id: str,
        result_quote: str,
        result_facts: Sequence[str] = (),
        failure_code: str = "",
        resolved_at: int,
    ) -> Optional[SelfCommitment]:
        persona_id = _require_persona_id(persona_id)
        next_status = (
            status
            if isinstance(status, SelfCommitmentStatus)
            else SelfCommitmentStatus(str(status))
        )
        if next_status not in {
            SelfCommitmentStatus.IN_PROGRESS,
            SelfCommitmentStatus.COMPLETED,
            SelfCommitmentStatus.BLOCKED,
            SelfCommitmentStatus.WITHDRAWN,
        }:
            raise ValueError("unsupported automatic commitment status")
        finished = next_status in {
            SelfCommitmentStatus.COMPLETED,
            SelfCommitmentStatus.BLOCKED,
            SelfCommitmentStatus.WITHDRAWN,
        }

        def operation(db):
            cursor = db.execute(
                "UPDATE self_commitments SET status=?, updated_at=?, "
                "result_decision_id=?, result_quote=?, result_facts_json=?, "
                "failure_code=?, resolved_at=?, next_attempt_at=NULL, "
                "lease_owner='', lease_until=NULL "
                "WHERE persona_id=? AND commitment_id=? "
                "AND status IN ('pending','in_progress','blocked')",
                (
                    next_status.value,
                    int(resolved_at),
                    str(result_decision_id or ""),
                    str(result_quote or "")[:180],
                    json.dumps(tuple(result_facts or ())[:8], ensure_ascii=False),
                    str(failure_code or "")[:80],
                    int(resolved_at) if finished else None,
                    persona_id,
                    str(commitment_id),
                ),
            )
            if cursor.rowcount != 1:
                return None
            return db.execute(
                "SELECT * FROM self_commitments WHERE persona_id=? AND commitment_id=?",
                (persona_id, str(commitment_id)),
            ).fetchone()

        row = self._write(operation)
        return self._row_to_self_commitment(row) if row is not None else None

    @staticmethod
    def _self_commitment_payload(
        item: Optional[SelfCommitment],
    ) -> Optional[Dict[str, Any]]:
        if item is None:
            return None
        return {
            "commitment_id": item.commitment_id,
            "group_id": item.group_id,
            "beneficiary_subject_id": item.beneficiary_subject_id,
            "summary": item.summary,
            "source_decision_id": item.source_decision_id,
            "source_message_id": item.source_message_id,
            "request_message_id": item.request_message_id,
            "source_quote": item.source_quote,
            "created_at": item.created_at,
            "updated_at": item.updated_at,
            "status": item.status.value,
            "required_capability": item.required_capability,
            "fulfillment_mode": item.fulfillment_mode,
            "due_at": item.due_at,
            "confidence": item.confidence,
            "extractor_version": item.extractor_version,
            "result_decision_id": item.result_decision_id,
            "result_quote": item.result_quote,
            "result_facts": list(item.result_facts),
            "failure_code": item.failure_code,
            "resolved_at": item.resolved_at,
            "next_attempt_at": item.next_attempt_at,
            "attempt_count": item.attempt_count,
            "last_attempt_at": item.last_attempt_at,
            "last_delivery_at": item.last_delivery_at,
        }

    def update_self_commitment_with_audit(
        self,
        persona_id: str,
        commitment_id: str,
        *,
        status: SelfCommitmentStatus,
        reason: str,
        actor: str,
        now: int,
    ) -> Optional[Dict[str, Any]]:
        persona_id = _require_persona_id(persona_id)
        next_status = (
            status
            if isinstance(status, SelfCommitmentStatus)
            else SelfCommitmentStatus(str(status))
        )
        if next_status not in {
            SelfCommitmentStatus.PENDING,
            SelfCommitmentStatus.IN_PROGRESS,
            SelfCommitmentStatus.COMPLETED,
            SelfCommitmentStatus.BLOCKED,
            SelfCommitmentStatus.WITHDRAWN,
            SelfCommitmentStatus.DELETED,
        }:
            raise ValueError("unsupported self commitment status")
        action_id = str(uuid4())

        def operation(db):
            row = db.execute(
                "SELECT * FROM self_commitments WHERE persona_id=? AND commitment_id=?",
                (persona_id, str(commitment_id)),
            ).fetchone()
            if row is None:
                return None
            current = self._row_to_self_commitment(row)
            if current.status is next_status:
                raise ValueError("self commitment already has this status")
            before = self._self_commitment_payload(current)
            resolved_at = (
                int(now)
                if next_status in {
                    SelfCommitmentStatus.COMPLETED,
                    SelfCommitmentStatus.BLOCKED,
                    SelfCommitmentStatus.WITHDRAWN,
                }
                else None
            )
            manual_result_id = (
                "governance:" + action_id if resolved_at is not None else None
            )
            manual_result_quote = (
                "管理员修正：" + str(reason).strip()
                if resolved_at is not None
                else ""
            )
            db.execute(
                "UPDATE self_commitments SET status=?, updated_at=?, resolved_at=?, "
                "result_decision_id=?, result_quote=?, result_facts_json='[]', "
                "failure_code='', next_attempt_at=?, lease_owner='', lease_until=NULL "
                "WHERE persona_id=? AND commitment_id=?",
                (
                    next_status.value,
                    int(now),
                    resolved_at,
                    manual_result_id,
                    manual_result_quote,
                    (
                        current.due_at or int(now)
                        if next_status is SelfCommitmentStatus.PENDING
                        else None
                    ),
                    persona_id,
                    str(commitment_id),
                ),
            )
            updated = self._row_to_self_commitment(
                db.execute(
                    "SELECT * FROM self_commitments WHERE persona_id=? AND commitment_id=?",
                    (persona_id, str(commitment_id)),
                ).fetchone()
            )
            after = self._self_commitment_payload(updated)
            self._insert_governance_action(
                db,
                action_id=action_id,
                persona_id=persona_id,
                action_type="self_commitment_status_corrected",
                target_kind="self_commitment",
                target_id=current.commitment_id,
                group_id=current.group_id,
                subject_id=current.beneficiary_subject_id,
                before=before,
                after=after,
                reason=reason,
                actor=actor,
                created_at=now,
            )
            return db.execute(
                "SELECT * FROM governance_actions WHERE action_id=?", (action_id,)
            ).fetchone()

        row = self._write(operation)
        return self._governance_payload(row) if row is not None else None

    def correct_member_profile_with_audit(
        self,
        persona_id: str,
        group_id: str,
        subject_id: str,
        preferred_address: str,
        *,
        reason: str,
        actor: str,
        now: int,
    ) -> Dict[str, Any]:
        persona_id = _require_persona_id(persona_id)
        preferred_address = str(preferred_address or "").strip()[:80]
        action_id = str(uuid4())

        def operation(db):
            row = db.execute(
                "SELECT * FROM profiles WHERE persona_id=? AND group_id=? AND subject_id=?",
                (persona_id, str(group_id), str(subject_id)),
            ).fetchone()
            if row is None:
                raise KeyError("member profile not found")
            before = self._profile_payload(row)
            db.execute(
                "UPDATE profiles SET preferred_address=?, updated_at=? "
                "WHERE persona_id=? AND group_id=? AND subject_id=?",
                (preferred_address, int(now), persona_id, str(group_id), str(subject_id)),
            )
            after_row = db.execute(
                "SELECT * FROM profiles WHERE persona_id=? AND group_id=? AND subject_id=?",
                (persona_id, str(group_id), str(subject_id)),
            ).fetchone()
            after = self._profile_payload(after_row)
            self._insert_governance_action(
                db,
                action_id=action_id,
                persona_id=persona_id,
                action_type="member_address_corrected",
                target_kind="member_profile",
                target_id=str(subject_id),
                group_id=str(group_id),
                subject_id=str(subject_id),
                before=before,
                after=after,
                reason=reason,
                actor=actor,
                created_at=now,
            )
            return after, db.execute(
                "SELECT * FROM governance_actions WHERE action_id=?", (action_id,)
            ).fetchone()

        profile, action = self._write(operation)
        return {"profile": profile, "action": self._governance_payload(action)}

    def link_member_identity_with_audit(
        self,
        persona_id: str,
        group_id: str,
        source_subject_id: str,
        canonical_subject_id: str,
        *,
        reason: str,
        actor: str,
        now: int,
    ) -> Dict[str, Any]:
        persona_id = _require_persona_id(persona_id)
        source = str(source_subject_id)
        canonical = str(canonical_subject_id)
        if source == canonical:
            raise ValueError("member cannot be linked to itself")
        action_id = str(uuid4())

        def operation(db):
            profiles = db.execute(
                "SELECT subject_id FROM profiles WHERE persona_id=? AND group_id=? "
                "AND subject_id IN (?, ?)",
                (persona_id, str(group_id), source, canonical),
            ).fetchall()
            if len(profiles) != 2:
                raise KeyError("member profile not found")
            target_canonical = canonical
            target_link = db.execute(
                "SELECT canonical_subject_id FROM member_identity_links "
                "WHERE persona_id=? AND group_id=? AND source_subject_id=? AND active=1",
                (persona_id, str(group_id), canonical),
            ).fetchone()
            if target_link is not None:
                target_canonical = str(target_link["canonical_subject_id"])
            if source == target_canonical:
                raise ValueError("member identity link would create a cycle")
            existing = db.execute(
                "SELECT * FROM member_identity_links WHERE persona_id=? AND group_id=? "
                "AND source_subject_id=?",
                (persona_id, str(group_id), source),
            ).fetchone()
            before = dict(existing) if existing is not None else None
            db.execute(
                """
                INSERT INTO member_identity_links(
                    persona_id, group_id, source_subject_id, canonical_subject_id,
                    reason, actor, created_at, active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(persona_id, group_id, source_subject_id) DO UPDATE SET
                    canonical_subject_id=excluded.canonical_subject_id,
                    reason=excluded.reason, actor=excluded.actor,
                    created_at=excluded.created_at, active=1
                """,
                (persona_id, str(group_id), source, target_canonical, str(reason), str(actor), int(now)),
            )
            after = {
                "group_id": str(group_id),
                "source_subject_id": source,
                "canonical_subject_id": target_canonical,
                "active": 1,
            }
            self._insert_governance_action(
                db,
                action_id=action_id,
                persona_id=persona_id,
                action_type="member_identity_linked",
                target_kind="member_identity_link",
                target_id=source,
                group_id=str(group_id),
                subject_id=source,
                before=before,
                after=after,
                reason=reason,
                actor=actor,
                created_at=now,
            )
            return db.execute(
                "SELECT * FROM governance_actions WHERE action_id=?", (action_id,)
            ).fetchone()

        return self._governance_payload(self._write(operation))

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

    def list_recent_memories(
        self,
        persona_id: str,
        *,
        group_id: Optional[str] = None,
        now: int,
        limit: int = 100,
        statuses: Optional[Sequence[MemoryStatus]] = None,
    ) -> List[MemoryItem]:
        """Return recent memories across groups for the plugin governance page."""
        persona_id = _require_persona_id(persona_id)
        sql = (
            "SELECT * FROM memories WHERE persona_id = ? "
            "AND (expires_at IS NULL OR expires_at > ?)"
        )
        params: List[Any] = [persona_id, int(now)]
        if group_id is not None:
            sql += " AND group_id = ?"
            params.append(str(group_id))
        selected_statuses = statuses or (MemoryStatus.ACCEPTED,)
        placeholders = ",".join("?" for _ in selected_statuses)
        sql += " AND status IN ({})".format(placeholders)
        params.extend(
            item.value if isinstance(item, MemoryStatus) else str(item)
            for item in selected_statuses
        )
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(0, min(500, int(limit))))
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
        expanded_ids = None
        if subject_ids is not None:
            expanded_ids = tuple(
                dict.fromkeys(
                    linked_id
                    for item in subject_ids
                    for linked_id in self.member_subject_ids(
                        persona_id, group_id, str(item)
                    )
                )
            )
        elif subject_id is not None:
            expanded_ids = self.member_subject_ids(
                persona_id, group_id, str(subject_id)
            )
        return memory_retrieval.search_memories(
            items,
            query=query,
            now=now,
            limit=limit,
            subject_id=None if expanded_ids is not None else subject_id,
            subject_ids=expanded_ids if expanded_ids is not None else subject_ids,
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
        limit = max(1, min(200, int(limit)))
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
        addressee: Dict[str, Any] = {}
        for item in stages:
            if item["state"] != "ADDRESSEE":
                continue
            try:
                decoded = json.loads(item["reason"] or "{}")
            except (TypeError, ValueError):
                decoded = {}
            if isinstance(decoded, dict):
                addressee = decoded
            break
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
            "addressee": addressee,
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
        delivery_keys = {
            (
                str(item.text or "").strip(),
                int(item.timestamp or 0),
            )
            for item in messages
            if item.origin is MessageOrigin.BOT_DELIVERY
        }

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
                    if message.origin is MessageOrigin.BOT_DELIVERY:
                        delivery_keys.add(
                            (
                                str(message.text or "").strip(),
                                int(message.timestamp or 0),
                            )
                        )

        # Bot replies are stored once as BOT_DELIVERY and often echoed again as
        # PLATFORM_HISTORY when QQ mirrors the outbound message back in.
        deduped: List[Any] = []
        for message in messages:
            text = str(message.text or "").strip()
            stamp = int(message.timestamp or 0)
            if (
                message.origin is MessageOrigin.PLATFORM_HISTORY
                and message.is_bot
                and text
                and (text, stamp) in delivery_keys
            ):
                continue
            deduped.append(message)
        messages = deduped

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
                    confidence, occurred_at, decision_id, evidence_text,
                    reason_code, extractor_version, status, reviewed_at, review_code,
                    review_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    event.evidence_text,
                    event.reason_code,
                    event.extractor_version,
                    event.status.value,
                    event.reviewed_at,
                    event.review_code,
                    event.review_reason,
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
        return [self._row_to_social_event(row) for row in rows]

    def get_social_event(
        self, persona_id: str, event_id: str
    ) -> Optional[SocialEvent]:
        persona_id = _require_persona_id(persona_id)
        row = self._db.execute(
            "SELECT * FROM social_events WHERE persona_id=? AND event_id=?",
            (persona_id, str(event_id)),
        ).fetchone()
        return self._row_to_social_event(row) if row else None

    def list_relationship_evidence(
        self,
        persona_id: str,
        *,
        group_id: Optional[str] = None,
        user_id: Optional[str] = None,
        limit: int = 500,
    ) -> List[SocialEvent]:
        persona_id = _require_persona_id(persona_id)
        sql = "SELECT * FROM social_events WHERE persona_id=?"
        params: List[Any] = [persona_id]
        if group_id is not None:
            sql += " AND group_id=?"
            params.append(str(group_id))
        if user_id is not None:
            sql += " AND user_id=?"
            params.append(str(user_id))
        sql += " ORDER BY occurred_at DESC, rowid DESC LIMIT ?"
        params.append(max(1, min(2000, int(limit))))
        rows = self._db.execute(sql, tuple(params)).fetchall()
        return [self._row_to_social_event(row) for row in rows]

    def relationship_learning_quality(
        self, persona_id: str, group_id: Optional[str] = None
    ) -> Dict[str, Any]:
        persona_id = _require_persona_id(persona_id)
        sql = (
            "SELECT status, review_code, COUNT(*) AS count FROM social_events "
            "WHERE persona_id=?"
        )
        params: List[Any] = [persona_id]
        if group_id is not None:
            sql += " AND group_id=?"
            params.append(str(group_id))
        sql += " GROUP BY status, review_code"
        rows = self._db.execute(sql, tuple(params)).fetchall()
        counts = {
            "pending": 0,
            "accepted": 0,
            "wrong_person": 0,
            "wrong_kind": 0,
            "insufficient_context": 0,
            "other_error": 0,
        }
        reviewed = 0
        errors = 0
        for row in rows:
            status = str(row["status"])
            review_code = str(row["review_code"] or "")
            count = int(row["count"])
            if status == SocialEventStatus.PENDING.value:
                counts["pending"] += count
                continue
            if not review_code:
                continue
            reviewed += count
            if status == SocialEventStatus.ACCEPTED.value:
                counts["accepted"] += count
            else:
                key = review_code if review_code in counts else "other_error"
                counts[key] += count
                errors += count
        return {
            **counts,
            "reviewed_count": reviewed,
            "error_count": errors,
            "error_rate": round(errors / reviewed, 4) if reviewed else 0.0,
        }

    @staticmethod
    def _row_to_social_event(row) -> SocialEvent:
        keys = set(row.keys())
        try:
            kind = SocialEventKind(str(row["kind"]))
        except ValueError:
            kind = SocialEventKind.NEUTRAL
        try:
            status = SocialEventStatus(
                str(row["status"] if "status" in keys else "accepted")
            )
        except ValueError:
            status = SocialEventStatus.ACCEPTED
        return SocialEvent(
            event_id=str(row["event_id"]),
            group_id=str(row["group_id"]),
            user_id=str(row["user_id"]),
            kind=kind,
            source_message_id=str(row["source_message_id"]),
            confidence=float(row["confidence"]),
            occurred_at=int(row["occurred_at"]),
            decision_id=row["decision_id"],
            evidence_text=str(row["evidence_text"] or "")
            if "evidence_text" in keys
            else "",
            reason_code=str(row["reason_code"] or "")
            if "reason_code" in keys
            else "",
            extractor_version=str(row["extractor_version"] or "legacy-verified")
            if "extractor_version" in keys
            else "legacy-verified",
            status=status,
            reviewed_at=row["reviewed_at"] if "reviewed_at" in keys else None,
            review_reason=str(row["review_reason"] or "")
            if "review_reason" in keys
            else "",
            review_code=str(row["review_code"] or "")
            if "review_code" in keys
            else "",
        )

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

    def get_member_relationship_state(
        self,
        persona_id: str,
        group_id: str,
        user_id: str,
        *,
        configured_relationship: Optional[str] = None,
        now: int = 0,
    ) -> Optional[RelationshipState]:
        """Read one continuous state across explicitly linked member profiles."""
        persona_id = _require_persona_id(persona_id)
        canonical = self.resolve_member_subject_id(persona_id, group_id, user_id)
        member_ids = self.member_subject_ids(persona_id, group_id, canonical)
        if len(member_ids) == 1:
            return self.get_relationship_state(persona_id, group_id, canonical)
        placeholders = ",".join("?" for _ in member_ids)
        baseline = self._db.execute(
            "SELECT * FROM governance_actions WHERE persona_id=? AND group_id=? "
            "AND subject_id IN ({}) AND target_kind='relationship' AND ("
            "(action_type='relationship_corrected' AND reverted_at IS NULL) OR "
            "action_type='governance_reverted') "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1".format(placeholders),
            (persona_id, str(group_id), *member_ids),
        ).fetchone()
        from ..social.affinity import initial_affinity_for_relationship
        from ..social.projector import SocialStateProjector

        if baseline is not None:
            try:
                payload = json.loads(baseline["after_json"] or "null")
            except (TypeError, ValueError):
                payload = None
            if isinstance(payload, dict):
                state = RelationshipState(
                    group_id=str(group_id),
                    user_id=canonical,
                    familiarity=int(payload.get("familiarity") or 0),
                    affinity=int(payload.get("affinity") or 0),
                    trust=int(payload.get("trust") or 0),
                    boundary_pressure=int(payload.get("boundary_pressure") or 0),
                    interaction_count=int(payload.get("interaction_count") or 0),
                    last_interaction_at=int(payload.get("last_interaction_at") or 0),
                    configured_relationship=payload.get("configured_relationship"),
                    updated_at=int(payload.get("updated_at") or baseline["created_at"]),
                )
            else:
                state = None
            since = int(baseline["created_at"])
        else:
            seed = initial_affinity_for_relationship(configured_relationship or "")
            state = RelationshipState(
                group_id=str(group_id),
                user_id=canonical,
                affinity=seed,
                configured_relationship=configured_relationship,
                updated_at=int(now),
            )
            since = -1
        rows = self._db.execute(
            "SELECT * FROM social_events WHERE persona_id=? AND group_id=? "
            "AND user_id IN ({}) AND status='accepted' AND occurred_at>? "
            "ORDER BY occurred_at ASC, event_id ASC".format(placeholders),
            (persona_id, str(group_id), *member_ids, since),
        ).fetchall()
        for row in rows:
            event = replace(self._row_to_social_event(row), user_id=canonical)
            state = SocialStateProjector().apply_event(
                state,
                event,
                configured_relationship=(
                    state.configured_relationship if state is not None else None
                ),
                now=int(now or event.occurred_at),
            )
        if rows or baseline is not None or configured_relationship:
            return state
        existing = [
            self.get_relationship_state(persona_id, group_id, item)
            for item in member_ids
        ]
        existing = [item for item in existing if item is not None]
        if not existing:
            return None
        latest = max(existing, key=lambda item: item.updated_at)
        return replace(latest, user_id=canonical)

    def list_relationship_states(
        self,
        persona_id: str,
        *,
        group_id: Optional[str] = None,
        limit: int = 200,
    ) -> List[RelationshipState]:
        persona_id = _require_persona_id(persona_id)
        sql = "SELECT * FROM relationship_state WHERE persona_id = ?"
        params: List[Any] = [persona_id]
        if group_id is not None:
            sql += " AND group_id = ?"
            params.append(str(group_id))
        sql += " ORDER BY updated_at DESC, interaction_count DESC LIMIT ?"
        params.append(max(0, min(500, int(limit))))
        rows = self._db.execute(sql, tuple(params)).fetchall()
        return [
            RelationshipState(
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
            for row in rows
        ]

    def upsert_relationship_state(
        self, persona_id: str, state: RelationshipState
    ) -> None:
        persona_id = _require_persona_id(persona_id)
        def operation(db):
            self._upsert_relationship_row(db, persona_id, state)

        self._write(operation)

    @staticmethod
    def _upsert_relationship_row(db, persona_id: str, state: RelationshipState) -> None:
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

    @staticmethod
    def _relationship_payload(state: Optional[RelationshipState]) -> Optional[Dict[str, Any]]:
        if state is None:
            return None
        return {
            "group_id": state.group_id,
            "user_id": state.user_id,
            "familiarity": state.familiarity,
            "affinity": state.affinity,
            "trust": state.trust,
            "boundary_pressure": state.boundary_pressure,
            "interaction_count": state.interaction_count,
            "last_interaction_at": state.last_interaction_at,
            "configured_relationship": state.configured_relationship,
            "updated_at": state.updated_at,
        }

    @staticmethod
    def _social_event_payload(event: SocialEvent) -> Dict[str, Any]:
        return {
            "event_id": event.event_id,
            "group_id": event.group_id,
            "user_id": event.user_id,
            "kind": event.kind.value,
            "source_message_id": event.source_message_id,
            "confidence": event.confidence,
            "occurred_at": event.occurred_at,
            "decision_id": event.decision_id,
            "evidence_text": event.evidence_text,
            "reason_code": event.reason_code,
            "extractor_version": event.extractor_version,
            "status": event.status.value,
            "reviewed_at": event.reviewed_at,
            "review_code": event.review_code,
            "review_reason": event.review_reason,
        }

    @staticmethod
    def _relationship_from_row(row) -> Optional[RelationshipState]:
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

    @staticmethod
    def _memory_payload(item: MemoryItem) -> Dict[str, Any]:
        return {
            "memory_id": item.memory_id,
            "group_id": item.group_id,
            "subject_id": item.subject_id,
            "kind": item.kind.value,
            "text": item.text,
            "created_at": item.created_at,
            "expires_at": item.expires_at,
            "confidence": item.confidence,
            "importance": item.importance,
            "authority": item.authority,
            "status": item.status.value,
            "scope": item.scope.value,
            "sensitivity": item.sensitivity.value,
        }

    @staticmethod
    def _insert_governance_action(
        db,
        *,
        action_id: str,
        persona_id: str,
        action_type: str,
        target_kind: str,
        target_id: str,
        group_id: str,
        subject_id: str,
        before: Any,
        after: Any,
        reason: str,
        actor: str,
        created_at: int,
        reverts_action_id: Optional[str] = None,
    ) -> None:
        db.execute(
            """
            INSERT INTO governance_actions(
                action_id, persona_id, action_type, target_kind, target_id,
                group_id, subject_id, before_json, after_json, reason, actor,
                created_at, reverts_action_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                action_id,
                persona_id,
                str(action_type),
                str(target_kind),
                str(target_id),
                str(group_id),
                str(subject_id),
                json.dumps(before, ensure_ascii=False, sort_keys=True),
                json.dumps(after, ensure_ascii=False, sort_keys=True),
                str(reason or "未填写原因"),
                str(actor or "AstrBot 插件管理员"),
                int(created_at),
                reverts_action_id,
            ),
        )

    @staticmethod
    def _governance_payload(row) -> Dict[str, Any]:
        def decode(value):
            try:
                return json.loads(value or "null")
            except (TypeError, ValueError):
                return None

        return {
            "action_id": str(row["action_id"]),
            "action_type": str(row["action_type"]),
            "target_kind": str(row["target_kind"]),
            "target_id": str(row["target_id"]),
            "group_id": str(row["group_id"]),
            "subject_id": str(row["subject_id"]),
            "before": decode(row["before_json"]),
            "after": decode(row["after_json"]),
            "reason": str(row["reason"]),
            "actor": str(row["actor"]),
            "created_at": int(row["created_at"]),
            "reverts_action_id": row["reverts_action_id"],
            "reverted_at": row["reverted_at"],
            "reverted_by": row["reverted_by"],
            "revert_reason": row["revert_reason"],
            "revert_action_id": row["revert_action_id"],
            "can_revert": (
                row["reverts_action_id"] is None
                and row["reverted_at"] is None
                and str(row["action_type"])
                in {
                    "relationship_corrected",
                    "relationship_evidence_reviewed",
                    "relationship_evidence_rejected",
                    "memory_deleted",
                    "member_address_corrected",
                    "member_identity_linked",
                    "continuity_status_corrected",
                    "self_commitment_status_corrected",
                }
            ),
        }

    def list_governance_actions(
        self,
        persona_id: str,
        *,
        group_id: Optional[str] = None,
        target_kind: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        persona_id = _require_persona_id(persona_id)
        sql = "SELECT * FROM governance_actions WHERE persona_id = ?"
        params: List[Any] = [persona_id]
        if group_id is not None:
            sql += " AND group_id = ?"
            params.append(str(group_id))
        if target_kind is not None:
            sql += " AND target_kind = ?"
            params.append(str(target_kind))
        sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
        params.append(max(1, min(500, int(limit))))
        rows = self._db.execute(sql, tuple(params)).fetchall()
        items = []
        seen_targets = set()
        for row in rows:
            item = self._governance_payload(row)
            key = (item["target_kind"], item["target_id"], item["group_id"])
            if key in seen_targets:
                item["can_revert"] = False
            seen_targets.add(key)
            items.append(item)
        return items

    def correct_relationship_with_audit(
        self,
        persona_id: str,
        state: RelationshipState,
        *,
        reason: str,
        actor: str,
        now: int,
    ) -> Dict[str, Any]:
        persona_id = _require_persona_id(persona_id)
        action_id = str(uuid4())

        def operation(db):
            row = db.execute(
                "SELECT * FROM relationship_state "
                "WHERE persona_id=? AND group_id=? AND user_id=?",
                (persona_id, str(state.group_id), str(state.user_id)),
            ).fetchone()
            before = self._relationship_payload(self._relationship_from_row(row))
            self._upsert_relationship_row(db, persona_id, state)
            after = self._relationship_payload(state)
            self._insert_governance_action(
                db,
                action_id=action_id,
                persona_id=persona_id,
                action_type="relationship_corrected",
                target_kind="relationship",
                target_id=state.user_id,
                group_id=state.group_id,
                subject_id=state.user_id,
                before=before,
                after=after,
                reason=reason,
                actor=actor,
                created_at=now,
            )
            return db.execute(
                "SELECT * FROM governance_actions WHERE action_id=?",
                (action_id,),
            ).fetchone()

        return self._governance_payload(self._write(operation))

    def delete_memory_with_audit(
        self,
        persona_id: str,
        memory_id: str,
        *,
        reason: str,
        actor: str,
        now: int,
    ) -> Optional[Dict[str, Any]]:
        persona_id = _require_persona_id(persona_id)
        action_id = str(uuid4())
        tombstone_id = str(uuid4())

        def operation(db):
            row = db.execute(
                "SELECT * FROM memories WHERE persona_id=? AND memory_id=?",
                (persona_id, str(memory_id)),
            ).fetchone()
            if row is None:
                return None
            item = self._row_to_memory(row)
            if item.status is not MemoryStatus.ACCEPTED:
                return None
            before = self._memory_payload(item)
            after = dict(before)
            after["status"] = MemoryStatus.DELETED.value
            after["tombstone_id"] = tombstone_id
            source_ids = list(item.source_message_ids or ())
            db.execute(
                "UPDATE memories SET status=? WHERE persona_id=? AND memory_id=?",
                (MemoryStatus.DELETED.value, persona_id, str(memory_id)),
            )
            db.execute(
                """
                INSERT INTO memory_tombstones(
                    tombstone_id, persona_id, group_id, subject_id, claim_hash,
                    source_message_ids_json, deleted_at, reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tombstone_id,
                    persona_id,
                    item.group_id,
                    item.subject_id,
                    claim_hash(item.text),
                    json.dumps(source_ids, ensure_ascii=False),
                    int(now),
                    str(reason or "plugin_page_deletion"),
                ),
            )
            self._insert_governance_action(
                db,
                action_id=action_id,
                persona_id=persona_id,
                action_type="memory_deleted",
                target_kind="memory",
                target_id=item.memory_id,
                group_id=item.group_id,
                subject_id=item.subject_id,
                before=before,
                after=after,
                reason=reason,
                actor=actor,
                created_at=now,
            )
            return db.execute(
                "SELECT * FROM governance_actions WHERE action_id=?",
                (action_id,),
            ).fetchone()

        row = self._write(operation)
        return self._governance_payload(row) if row is not None else None

    def reject_social_event_with_audit(
        self,
        persona_id: str,
        event_id: str,
        *,
        reason: str,
        actor: str,
        now: int,
    ) -> Optional[Dict[str, Any]]:
        persona_id = _require_persona_id(persona_id)
        action_id = str(uuid4())

        def operation(db):
            row = db.execute(
                "SELECT * FROM social_events WHERE persona_id=? AND event_id=?",
                (persona_id, str(event_id)),
            ).fetchone()
            if row is None:
                return None
            event = self._row_to_social_event(row)
            if event.status is not SocialEventStatus.ACCEPTED:
                raise ValueError("relationship evidence is not accepted")
            relationship_row = db.execute(
                "SELECT * FROM relationship_state "
                "WHERE persona_id=? AND group_id=? AND user_id=?",
                (persona_id, event.group_id, event.user_id),
            ).fetchone()
            before_relationship = self._relationship_payload(
                self._relationship_from_row(relationship_row)
            )
            before_event = self._social_event_payload(event)
            db.execute(
                "UPDATE social_events SET status=?, reviewed_at=?, review_code=?, "
                "review_reason=? "
                "WHERE persona_id=? AND event_id=?",
                (
                    SocialEventStatus.REJECTED.value,
                    int(now),
                    "other_error",
                    str(reason),
                    persona_id,
                    event.event_id,
                ),
            )
            rejected = self._row_to_social_event(
                db.execute(
                    "SELECT * FROM social_events WHERE persona_id=? AND event_id=?",
                    (persona_id, event.event_id),
                ).fetchone()
            )
            rebuilt = self._rebuild_relationship_in_db(
                db,
                persona_id,
                event.group_id,
                event.user_id,
                now=now,
            )
            before = {
                "evidence": before_event,
                "relationship": before_relationship,
            }
            after = {
                "evidence": self._social_event_payload(rejected),
                "relationship": self._relationship_payload(rebuilt),
            }
            self._insert_governance_action(
                db,
                action_id=action_id,
                persona_id=persona_id,
                action_type="relationship_evidence_rejected",
                target_kind="relationship_evidence",
                target_id=event.event_id,
                group_id=event.group_id,
                subject_id=event.user_id,
                before=before,
                after=after,
                reason=reason,
                actor=actor,
                created_at=now,
            )
            return db.execute(
                "SELECT * FROM governance_actions WHERE action_id=?",
                (action_id,),
            ).fetchone()

        row = self._write(operation)
        return self._governance_payload(row) if row is not None else None

    def review_pending_social_event_with_audit(
        self,
        persona_id: str,
        event_id: str,
        *,
        outcome: str,
        reason: str,
        actor: str,
        now: int,
    ) -> Optional[Dict[str, Any]]:
        persona_id = _require_persona_id(persona_id)
        outcomes = {
            "correct": (SocialEventStatus.ACCEPTED, "correct"),
            "wrong_person": (SocialEventStatus.REJECTED, "wrong_person"),
            "wrong_kind": (SocialEventStatus.REJECTED, "wrong_kind"),
            "insufficient_context": (
                SocialEventStatus.REJECTED,
                "insufficient_context",
            ),
        }
        if outcome not in outcomes:
            raise ValueError("unsupported relationship evidence review outcome")
        next_status, review_code = outcomes[outcome]
        action_id = str(uuid4())

        def operation(db):
            row = db.execute(
                "SELECT * FROM social_events WHERE persona_id=? AND event_id=?",
                (persona_id, str(event_id)),
            ).fetchone()
            if row is None:
                return None
            event = self._row_to_social_event(row)
            if event.status is not SocialEventStatus.PENDING:
                raise ValueError("relationship evidence is no longer pending review")
            relationship_row = db.execute(
                "SELECT * FROM relationship_state "
                "WHERE persona_id=? AND group_id=? AND user_id=?",
                (persona_id, event.group_id, event.user_id),
            ).fetchone()
            before = {
                "evidence": self._social_event_payload(event),
                "relationship": self._relationship_payload(
                    self._relationship_from_row(relationship_row)
                ),
            }
            db.execute(
                "UPDATE social_events SET status=?, reviewed_at=?, review_code=?, "
                "review_reason=? WHERE persona_id=? AND event_id=?",
                (
                    next_status.value,
                    int(now),
                    review_code,
                    str(reason),
                    persona_id,
                    event.event_id,
                ),
            )
            reviewed = self._row_to_social_event(
                db.execute(
                    "SELECT * FROM social_events WHERE persona_id=? AND event_id=?",
                    (persona_id, event.event_id),
                ).fetchone()
            )
            rebuilt = self._rebuild_relationship_in_db(
                db,
                persona_id,
                event.group_id,
                event.user_id,
                now=now,
            )
            after = {
                "evidence": self._social_event_payload(reviewed),
                "relationship": self._relationship_payload(rebuilt),
            }
            self._insert_governance_action(
                db,
                action_id=action_id,
                persona_id=persona_id,
                action_type="relationship_evidence_reviewed",
                target_kind="relationship_evidence",
                target_id=event.event_id,
                group_id=event.group_id,
                subject_id=event.user_id,
                before=before,
                after=after,
                reason=reason,
                actor=actor,
                created_at=now,
            )
            return db.execute(
                "SELECT * FROM governance_actions WHERE action_id=?", (action_id,)
            ).fetchone()

        row = self._write(operation)
        return self._governance_payload(row) if row is not None else None

    def revert_governance_action(
        self,
        persona_id: str,
        action_id: str,
        *,
        reason: str,
        actor: str,
        now: int,
    ) -> Dict[str, Any]:
        persona_id = _require_persona_id(persona_id)
        rollback_id = str(uuid4())

        def operation(db):
            row = db.execute(
                "SELECT rowid AS audit_rowid, * FROM governance_actions "
                "WHERE persona_id=? AND action_id=?",
                (persona_id, str(action_id)),
            ).fetchone()
            if row is None:
                raise KeyError("governance action not found")
            if row["reverts_action_id"] is not None or row["reverted_at"] is not None:
                raise ValueError("governance action is not revertible")
            newer = db.execute(
                "SELECT 1 FROM governance_actions WHERE persona_id=? "
                "AND target_kind=? AND target_id=? AND group_id=? AND rowid>? LIMIT 1",
                (
                    persona_id,
                    row["target_kind"],
                    row["target_id"],
                    row["group_id"],
                    int(row["audit_rowid"]),
                ),
            ).fetchone()
            if newer is not None:
                raise ValueError("newer governance action exists for this target")
            original = self._governance_payload(row)
            before = original["before"]
            target_kind = original["target_kind"]
            if target_kind == "relationship":
                current_row = db.execute(
                    "SELECT * FROM relationship_state "
                    "WHERE persona_id=? AND group_id=? AND user_id=?",
                    (persona_id, original["group_id"], original["subject_id"]),
                ).fetchone()
                current = self._relationship_payload(
                    self._relationship_from_row(current_row)
                )
                if before is None:
                    db.execute(
                        "DELETE FROM relationship_state "
                        "WHERE persona_id=? AND group_id=? AND user_id=?",
                        (persona_id, original["group_id"], original["subject_id"]),
                    )
                    restored = None
                else:
                    restored_state = RelationshipState(
                        group_id=before["group_id"],
                        user_id=before["user_id"],
                        familiarity=int(before["familiarity"]),
                        affinity=int(before["affinity"]),
                        trust=int(before["trust"]),
                        boundary_pressure=int(before["boundary_pressure"]),
                        interaction_count=int(before["interaction_count"]),
                        last_interaction_at=int(before["last_interaction_at"]),
                        configured_relationship=before.get("configured_relationship"),
                        updated_at=int(now),
                    )
                    self._upsert_relationship_row(db, persona_id, restored_state)
                    restored = self._relationship_payload(restored_state)
            elif target_kind == "memory":
                memory_row = db.execute(
                    "SELECT * FROM memories WHERE persona_id=? AND memory_id=?",
                    (persona_id, original["target_id"]),
                ).fetchone()
                if memory_row is None:
                    raise ValueError("memory no longer exists")
                current_item = self._row_to_memory(memory_row)
                if current_item.status is not MemoryStatus.DELETED:
                    raise ValueError("memory is no longer deleted")
                current = self._memory_payload(current_item)
                restored_status = str(before.get("status") or MemoryStatus.ACCEPTED.value)
                db.execute(
                    "UPDATE memories SET status=? WHERE persona_id=? AND memory_id=?",
                    (restored_status, persona_id, original["target_id"]),
                )
                tombstone_id = (original.get("after") or {}).get("tombstone_id")
                if tombstone_id:
                    db.execute(
                        "DELETE FROM memory_tombstones "
                        "WHERE persona_id=? AND tombstone_id=?",
                        (persona_id, str(tombstone_id)),
                    )
                restored = dict(before)
                restored["status"] = restored_status
            elif target_kind == "relationship_evidence":
                event_row = db.execute(
                    "SELECT * FROM social_events WHERE persona_id=? AND event_id=?",
                    (persona_id, original["target_id"]),
                ).fetchone()
                if event_row is None:
                    raise ValueError("relationship evidence no longer exists")
                event = self._row_to_social_event(event_row)
                after_event = (original.get("after") or {}).get("evidence") or {}
                if event.status.value != str(after_event.get("status") or ""):
                    raise ValueError("relationship evidence review state has changed")
                current_relationship_row = db.execute(
                    "SELECT * FROM relationship_state "
                    "WHERE persona_id=? AND group_id=? AND user_id=?",
                    (persona_id, event.group_id, event.user_id),
                ).fetchone()
                current = {
                    "evidence": self._social_event_payload(event),
                    "relationship": self._relationship_payload(
                        self._relationship_from_row(current_relationship_row)
                    ),
                }
                before_event = (before or {}).get("evidence") or {}
                db.execute(
                    "UPDATE social_events SET status=?, reviewed_at=?, review_code=?, "
                    "review_reason=? "
                    "WHERE persona_id=? AND event_id=?",
                    (
                        str(before_event.get("status") or "accepted"),
                        before_event.get("reviewed_at"),
                        str(before_event.get("review_code") or ""),
                        str(before_event.get("review_reason") or ""),
                        persona_id,
                        event.event_id,
                    ),
                )
                restored_event = self._row_to_social_event(
                    db.execute(
                        "SELECT * FROM social_events WHERE persona_id=? AND event_id=?",
                        (persona_id, event.event_id),
                    ).fetchone()
                )
                rebuilt = self._rebuild_relationship_in_db(
                    db,
                    persona_id,
                    event.group_id,
                    event.user_id,
                    now=now,
                )
                restored = {
                    "evidence": self._social_event_payload(restored_event),
                    "relationship": self._relationship_payload(rebuilt),
                }
            elif target_kind == "member_profile":
                profile_row = db.execute(
                    "SELECT * FROM profiles WHERE persona_id=? AND group_id=? AND subject_id=?",
                    (persona_id, original["group_id"], original["subject_id"]),
                ).fetchone()
                if profile_row is None:
                    raise ValueError("member profile no longer exists")
                current = self._profile_payload(profile_row)
                db.execute(
                    "UPDATE profiles SET preferred_address=?, updated_at=? "
                    "WHERE persona_id=? AND group_id=? AND subject_id=?",
                    (
                        str((before or {}).get("preferred_address") or ""),
                        int(now),
                        persona_id,
                        original["group_id"],
                        original["subject_id"],
                    ),
                )
                restored = self._profile_payload(
                    db.execute(
                        "SELECT * FROM profiles WHERE persona_id=? AND group_id=? AND subject_id=?",
                        (persona_id, original["group_id"], original["subject_id"]),
                    ).fetchone()
                )
            elif target_kind == "member_identity_link":
                link_row = db.execute(
                    "SELECT * FROM member_identity_links WHERE persona_id=? AND group_id=? "
                    "AND source_subject_id=?",
                    (persona_id, original["group_id"], original["subject_id"]),
                ).fetchone()
                current = dict(link_row) if link_row is not None else None
                if before is None:
                    db.execute(
                        "DELETE FROM member_identity_links WHERE persona_id=? AND group_id=? "
                        "AND source_subject_id=?",
                        (persona_id, original["group_id"], original["subject_id"]),
                    )
                    restored = None
                else:
                    db.execute(
                        """
                        INSERT INTO member_identity_links(
                            persona_id, group_id, source_subject_id, canonical_subject_id,
                            reason, actor, created_at, active
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(persona_id, group_id, source_subject_id) DO UPDATE SET
                            canonical_subject_id=excluded.canonical_subject_id,
                            reason=excluded.reason, actor=excluded.actor,
                            created_at=excluded.created_at, active=excluded.active
                        """,
                        (
                            persona_id,
                            original["group_id"],
                            original["subject_id"],
                            before["canonical_subject_id"],
                            before["reason"],
                            before["actor"],
                            int(before["created_at"]),
                            int(before["active"]),
                        ),
                    )
                    restored = dict(before)
            elif target_kind == "continuity_item":
                continuity_row = db.execute(
                    "SELECT * FROM continuity_items WHERE persona_id=? AND item_id=?",
                    (persona_id, original["target_id"]),
                ).fetchone()
                if continuity_row is None:
                    raise ValueError("continuity item no longer exists")
                current_item = self._row_to_continuity(continuity_row)
                current = self._continuity_payload(current_item)
                before_status = str((before or {}).get("status") or "open")
                db.execute(
                    "UPDATE continuity_items SET status=?, updated_at=?, resolved_at=?, "
                    "resolution_message_id=?, resolution_quote=? "
                    "WHERE persona_id=? AND item_id=?",
                    (
                        before_status,
                        int(now),
                        (before or {}).get("resolved_at"),
                        (before or {}).get("resolution_message_id"),
                        str((before or {}).get("resolution_quote") or ""),
                        persona_id,
                        original["target_id"],
                    ),
                )
                restored = dict(before or {})
                restored["updated_at"] = int(now)
            elif target_kind == "self_commitment":
                commitment_row = db.execute(
                    "SELECT * FROM self_commitments WHERE persona_id=? AND commitment_id=?",
                    (persona_id, original["target_id"]),
                ).fetchone()
                if commitment_row is None:
                    raise ValueError("self commitment no longer exists")
                current_item = self._row_to_self_commitment(commitment_row)
                current = self._self_commitment_payload(current_item)
                db.execute(
                    "UPDATE self_commitments SET status=?, updated_at=?, resolved_at=?, "
                    "result_decision_id=?, result_quote=?, result_facts_json=?, "
                    "failure_code=?, next_attempt_at=?, attempt_count=?, "
                    "lease_owner='', lease_until=NULL, last_attempt_at=?, "
                    "last_delivery_at=? WHERE persona_id=? AND commitment_id=?",
                    (
                        str((before or {}).get("status") or "pending"),
                        int(now),
                        (before or {}).get("resolved_at"),
                        (before or {}).get("result_decision_id"),
                        str((before or {}).get("result_quote") or ""),
                        json.dumps(
                            tuple((before or {}).get("result_facts") or ()),
                            ensure_ascii=False,
                        ),
                        str((before or {}).get("failure_code") or ""),
                        (before or {}).get("next_attempt_at"),
                        int((before or {}).get("attempt_count") or 0),
                        (before or {}).get("last_attempt_at"),
                        (before or {}).get("last_delivery_at"),
                        persona_id,
                        original["target_id"],
                    ),
                )
                restored = dict(before or {})
                restored["updated_at"] = int(now)
            else:
                raise ValueError("unsupported governance target")
            self._insert_governance_action(
                db,
                action_id=rollback_id,
                persona_id=persona_id,
                action_type="governance_reverted",
                target_kind=target_kind,
                target_id=original["target_id"],
                group_id=original["group_id"],
                subject_id=original["subject_id"],
                before=current,
                after=restored,
                reason=reason,
                actor=actor,
                created_at=now,
                reverts_action_id=original["action_id"],
            )
            db.execute(
                "UPDATE governance_actions SET reverted_at=?, reverted_by=?, "
                "revert_reason=?, revert_action_id=? WHERE action_id=?",
                (int(now), str(actor), str(reason), rollback_id, original["action_id"]),
            )
            return db.execute(
                "SELECT * FROM governance_actions WHERE action_id=?",
                (rollback_id,),
            ).fetchone()

        return self._governance_payload(self._write(operation))

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
        return self._write(
            lambda db: self._rebuild_relationship_in_db(
                db,
                persona_id,
                group_id,
                user_id,
                configured_relationship=configured_relationship,
                seed_affinity=seed_affinity,
                now=now,
            )
        )

    def _rebuild_relationship_in_db(
        self,
        db,
        persona_id: str,
        group_id: str,
        user_id: str,
        *,
        configured_relationship: Optional[str] = None,
        seed_affinity: int = 0,
        now: int = 0,
    ) -> RelationshipState:
        from ..social.affinity import initial_affinity_for_relationship
        from ..social.projector import SocialStateProjector

        baseline_row = db.execute(
            "SELECT * FROM governance_actions WHERE persona_id=? AND group_id=? "
            "AND subject_id=? AND target_kind='relationship' AND ("
            "(action_type='relationship_corrected' AND reverted_at IS NULL) OR "
            "action_type='governance_reverted') "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (persona_id, str(group_id), str(user_id)),
        ).fetchone()
        projector = SocialStateProjector()
        if baseline_row is not None:
            try:
                payload = json.loads(baseline_row["after_json"] or "null")
            except (TypeError, ValueError):
                payload = None
            if isinstance(payload, dict):
                state = RelationshipState(
                    group_id=str(group_id),
                    user_id=str(user_id),
                    familiarity=int(payload.get("familiarity") or 0),
                    affinity=int(payload.get("affinity") or 0),
                    trust=int(payload.get("trust") or 0),
                    boundary_pressure=int(payload.get("boundary_pressure") or 0),
                    interaction_count=int(payload.get("interaction_count") or 0),
                    last_interaction_at=int(payload.get("last_interaction_at") or 0),
                    configured_relationship=payload.get("configured_relationship"),
                    updated_at=int(payload.get("updated_at") or baseline_row["created_at"]),
                )
                since = int(baseline_row["created_at"])
            else:
                state = None
                since = int(baseline_row["created_at"])
        else:
            current_row = db.execute(
                "SELECT configured_relationship FROM relationship_state "
                "WHERE persona_id=? AND group_id=? AND user_id=?",
                (persona_id, str(group_id), str(user_id)),
            ).fetchone()
            configured = configured_relationship
            if configured is None and current_row is not None:
                configured = current_row["configured_relationship"]
            seed = int(seed_affinity)
            if configured and seed == 0:
                seed = initial_affinity_for_relationship(str(configured))
            state = RelationshipState(
                group_id=str(group_id),
                user_id=str(user_id),
                affinity=seed,
                configured_relationship=configured,
                updated_at=int(now),
            )
            since = -1
        rows = db.execute(
            "SELECT * FROM social_events WHERE persona_id=? AND group_id=? "
            "AND user_id=? AND status='accepted' AND occurred_at>? "
            "ORDER BY occurred_at ASC, event_id ASC",
            (persona_id, str(group_id), str(user_id), since),
        ).fetchall()
        for row in rows:
            state = projector.apply_event(
                state,
                self._row_to_social_event(row),
                configured_relationship=(
                    state.configured_relationship if state is not None else None
                ),
                now=int(now),
            )
        if state is None:
            state = RelationshipState(
                group_id=str(group_id),
                user_id=str(user_id),
                updated_at=int(now),
            )
        self._upsert_relationship_row(db, persona_id, state)
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

        canonical_user_id = self.resolve_member_subject_id(
            persona_id, event.group_id, event.user_id
        )
        if canonical_user_id != event.user_id:
            event = replace(event, user_id=canonical_user_id)

        inserted = self.append_social_event(persona_id, event)
        current = self.get_relationship_state(
            persona_id, event.group_id, event.user_id
        )
        if event.status is not SocialEventStatus.ACCEPTED:
            return current
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
