"""SQLite Durable Inbox, Journal, Cursor, and Snapshot store."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from ..contracts import ActorCursor, SocialEventEnvelope
from .schema import connect_database, initialize_database


class EventClaimError(RuntimeError):
    """Raised when an actor attempts to commit an event it does not own."""


@dataclass(frozen=True)
class AppendResult:
    inserted: bool
    sequence: int


@dataclass(frozen=True)
class ClaimedEvent:
    sequence: int
    event: SocialEventEnvelope
    attempt: int


@dataclass(frozen=True)
class JournalEffect:
    effect_id: str
    source_event_id: str
    correlation_id: str
    causation_id: str | None
    actor_key: str
    effect_type: str
    payload: dict[str, object]
    committed_at: int


@dataclass(frozen=True)
class StoredSnapshot:
    actor_key: str
    version: int
    payload: dict[str, object]
    created_at: int


class SQLiteSocialEventStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        initialize_database(self.path)
        self._ensure_claim_owner_column()

    def _ensure_claim_owner_column(self) -> None:
        with connect_database(self.path) as db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(inbox)")}
            if "claimed_by" not in columns:
                db.execute("ALTER TABLE inbox ADD COLUMN claimed_by TEXT")

    def append(self, event: SocialEventEnvelope) -> AppendResult:
        encoded = json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True)
        with connect_database(self.path) as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO inbox("
                "event_id, persona_id, group_id, envelope_json, received_at, status"
                ") VALUES(?, ?, ?, ?, ?, 'pending')",
                (
                    event.event_id,
                    event.persona_id,
                    event.group_id,
                    encoded,
                    event.received_at,
                ),
            )
            row = db.execute(
                "SELECT sequence FROM inbox WHERE event_id=?", (event.event_id,)
            ).fetchone()
            return AppendResult(cursor.rowcount == 1, int(row[0]))

    def claim(
        self,
        actor_key: str,
        after_sequence: int,
        limit: int,
        *,
        persona_id: str | None = None,
        group_id: str | None = None,
    ) -> tuple[ClaimedEvent, ...]:
        if limit < 1:
            raise ValueError("limit must be positive")
        db = connect_database(self.path)
        try:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT sequence, envelope_json, attempt FROM inbox "
                "WHERE sequence>? AND (status IN ('pending','failed') "
                "OR (status='processing' AND claimed_by=?)) "
                "AND (? IS NULL OR persona_id=?) "
                "AND (? IS NULL OR group_id=?) "
                "ORDER BY sequence LIMIT ?",
                (
                    after_sequence,
                    actor_key,
                    persona_id,
                    persona_id,
                    group_id,
                    group_id,
                    limit,
                ),
            ).fetchall()
            claimed = []
            for row in rows:
                attempt = int(row[2]) + 1
                db.execute(
                    "UPDATE inbox SET status='processing', attempt=?, claimed_by=?, "
                    "failure_code=NULL WHERE sequence=?",
                    (attempt, actor_key, int(row[0])),
                )
                claimed.append(
                    ClaimedEvent(
                        sequence=int(row[0]),
                        event=SocialEventEnvelope.from_dict(json.loads(row[1])),
                        attempt=attempt,
                    )
                )
            db.commit()
            return tuple(claimed)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def read_events(
        self,
        after_sequence: int,
        through_sequence: int,
        *,
        persona_id: str,
        group_id: str,
    ) -> tuple[ClaimedEvent, ...]:
        """Read an actor's committed history for deterministic snapshot replay."""

        if through_sequence < after_sequence:
            raise ValueError("through_sequence must not precede after_sequence")
        with connect_database(self.path) as db:
            rows = db.execute(
                "SELECT sequence, envelope_json, attempt FROM inbox "
                "WHERE sequence>? AND sequence<=? AND persona_id=? AND group_id=? "
                "AND status='committed' ORDER BY sequence",
                (after_sequence, through_sequence, persona_id, group_id),
            ).fetchall()
        return tuple(
            ClaimedEvent(
                sequence=int(row[0]),
                event=SocialEventEnvelope.from_dict(json.loads(row[1])),
                attempt=int(row[2]),
            )
            for row in rows
        )

    def commit(
        self,
        actor_key: str,
        claimed: ClaimedEvent,
        effects: tuple[dict[str, object], ...],
    ) -> ActorCursor:
        db = connect_database(self.path)
        try:
            db.execute("BEGIN IMMEDIATE")
            current = self._cursor_on(db, actor_key)
            if current.last_sequence >= claimed.sequence:
                db.rollback()
                return current
            ownership = db.execute(
                "SELECT status, claimed_by FROM inbox WHERE sequence=?",
                (claimed.sequence,),
            ).fetchone()
            if ownership is None or ownership[1] != actor_key:
                raise EventClaimError("event claim is owned by another actor")
            now = int(time.time())
            for effect in effects:
                effect_id = str(effect.get("effect_id") or "").strip()
                effect_type = str(effect.get("kind") or "").strip()
                if not effect_id or not effect_type:
                    raise ValueError("effects require effect_id and kind")
                db.execute(
                    "INSERT OR IGNORE INTO journal("
                    "effect_id, source_event_id, correlation_id, causation_id, "
                    "actor_key, effect_type, effect_json, committed_at"
                    ") VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        effect_id,
                        claimed.event.event_id,
                        claimed.event.correlation_id,
                        claimed.event.causation_id,
                        actor_key,
                        effect_type,
                        json.dumps(effect, ensure_ascii=False, sort_keys=True),
                        now,
                    ),
                )
            next_cursor = ActorCursor(actor_key, claimed.sequence, current.version + 1)
            db.execute(
                "INSERT INTO actor_cursors(actor_key, last_sequence, version) "
                "VALUES(?, ?, ?) ON CONFLICT(actor_key) DO UPDATE SET "
                "last_sequence=excluded.last_sequence, version=excluded.version",
                (actor_key, next_cursor.last_sequence, next_cursor.version),
            )
            db.execute(
                "UPDATE inbox SET status='committed', claimed_by=? WHERE sequence=?",
                (actor_key, claimed.sequence),
            )
            db.commit()
            return next_cursor
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def fail(self, actor_key: str, sequence: int, code: str) -> None:
        with connect_database(self.path) as db:
            db.execute(
                "UPDATE inbox SET status='failed', claimed_by=?, failure_code=? "
                "WHERE sequence=?",
                (actor_key, str(code), sequence),
            )

    def cursor(self, actor_key: str) -> ActorCursor:
        with connect_database(self.path) as db:
            return self._cursor_on(db, actor_key)

    @staticmethod
    def _cursor_on(db: sqlite3.Connection, actor_key: str) -> ActorCursor:
        row = db.execute(
            "SELECT last_sequence, version FROM actor_cursors WHERE actor_key=?",
            (actor_key,),
        ).fetchone()
        if row is None:
            return ActorCursor(actor_key, 0, 0)
        return ActorCursor(actor_key, int(row[0]), int(row[1]))

    def journal(self, correlation_id: str) -> tuple[JournalEffect, ...]:
        with connect_database(self.path) as db:
            rows = db.execute(
                "SELECT effect_id, source_event_id, correlation_id, causation_id, "
                "actor_key, effect_type, effect_json, committed_at FROM journal "
                "WHERE correlation_id=? ORDER BY rowid",
                (correlation_id,),
            ).fetchall()
        return tuple(
            JournalEffect(
                effect_id=row[0],
                source_event_id=row[1],
                correlation_id=row[2],
                causation_id=row[3],
                actor_key=row[4],
                effect_type=row[5],
                payload=json.loads(row[6]),
                committed_at=int(row[7]),
            )
            for row in rows
        )

    def event_ids(self) -> tuple[str, ...]:
        with connect_database(self.path) as db:
            rows = db.execute("SELECT event_id FROM inbox ORDER BY sequence").fetchall()
        return tuple(str(row[0]) for row in rows)

    def pending_groups(self, persona_id: str) -> tuple[str, ...]:
        with connect_database(self.path) as db:
            rows = db.execute(
                "SELECT DISTINCT group_id FROM inbox WHERE persona_id=? "
                "AND group_id IS NOT NULL "
                "AND status IN ('pending','processing','failed') ORDER BY group_id",
                (persona_id,),
            ).fetchall()
        return tuple(str(row[0]) for row in rows)

    def save_snapshot(self, actor_key: str, version: int, payload: dict) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with connect_database(self.path) as db:
            db.execute(
                "INSERT OR REPLACE INTO snapshots(actor_key, version, payload_json, created_at) "
                "VALUES(?, ?, ?, ?)",
                (actor_key, version, encoded, int(time.time())),
            )

    def load_snapshot(self, actor_key: str) -> StoredSnapshot | None:
        with connect_database(self.path) as db:
            row = db.execute(
                "SELECT version, payload_json, created_at FROM snapshots "
                "WHERE actor_key=? ORDER BY version DESC LIMIT 1",
                (actor_key,),
            ).fetchone()
        if row is None:
            return None
        return StoredSnapshot(actor_key, int(row[0]), json.loads(row[1]), int(row[2]))
