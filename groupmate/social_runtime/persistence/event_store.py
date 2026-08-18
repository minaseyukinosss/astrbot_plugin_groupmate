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


class JournalEffectIdentityConflict(RuntimeError):
    """Raised when an effect id is reused for different causal content."""


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


@dataclass(frozen=True)
class StoredSceneWorkRequest:
    request_id: str
    actor_key: str
    status: str
    payload: dict[str, object]


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
        work_requests: tuple[dict[str, object], ...] = (),
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
                effect_json = json.dumps(effect, ensure_ascii=False, sort_keys=True)
                existing = db.execute(
                    "SELECT source_event_id, correlation_id, causation_id, "
                    "actor_key, effect_type, effect_json FROM journal "
                    "WHERE effect_id=?",
                    (effect_id,),
                ).fetchone()
                identity = (
                    claimed.event.event_id,
                    claimed.event.correlation_id,
                    claimed.event.causation_id,
                    actor_key,
                    effect_type,
                    effect_json,
                )
                if existing is not None:
                    if tuple(existing) != identity:
                        raise JournalEffectIdentityConflict(
                            f"effect id belongs to different content: {effect_id}"
                        )
                    continue
                db.execute(
                    "INSERT INTO journal("
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
                        effect_json,
                        now,
                    ),
                )
            for request in work_requests:
                request_id = str(request.get("request_id") or "").strip()
                trigger_event_id = str(
                    request.get("trigger_event_id") or ""
                ).strip()
                scene_version = int(request.get("scene_version") or 0)
                request_payload = request.get("request")
                if not request_id or not trigger_event_id or scene_version < 1:
                    raise ValueError("work request identity is incomplete")
                request_json = json.dumps(
                    request_payload, ensure_ascii=False, sort_keys=True
                )
                existing_request = db.execute(
                    "SELECT actor_key, trigger_event_id, scene_version, request_json "
                    "FROM scene_work_requests WHERE request_id=?",
                    (request_id,),
                ).fetchone()
                request_identity = (
                    actor_key,
                    trigger_event_id,
                    scene_version,
                    request_json,
                )
                if existing_request is not None:
                    if tuple(existing_request) != request_identity:
                        raise JournalEffectIdentityConflict(
                            f"work request id belongs to different content: {request_id}"
                        )
                    continue
                db.execute(
                    "UPDATE scene_work_requests SET status='stale', updated_at=? "
                    "WHERE actor_key=? AND status='pending' AND scene_version<?",
                    (now, actor_key, scene_version),
                )
                db.execute(
                    "INSERT INTO scene_work_requests("
                    "request_id, actor_key, trigger_event_id, scene_version, "
                    "request_json, status, created_at, updated_at"
                    ") VALUES(?, ?, ?, ?, ?, 'pending', ?, ?)",
                    (
                        request_id,
                        actor_key,
                        trigger_event_id,
                        scene_version,
                        request_json,
                        now,
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
                "SELECT group_id FROM ("
                "SELECT DISTINCT group_id FROM inbox WHERE persona_id=? "
                "AND group_id IS NOT NULL "
                "AND status IN ('pending','processing','failed') "
                "UNION "
                "SELECT DISTINCT inbox.group_id FROM scene_work_requests AS work "
                "JOIN inbox ON inbox.event_id=work.trigger_event_id "
                "WHERE inbox.persona_id=? AND inbox.group_id IS NOT NULL "
                "AND work.status='pending'"
                ") ORDER BY group_id",
                (persona_id, persona_id),
            ).fetchall()
        return tuple(str(row[0]) for row in rows)

    def outbox_count(self) -> int:
        with connect_database(self.path) as db:
            return int(db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0])

    def pending_scene_work(
        self, actor_key: str, scene_version: int
    ) -> tuple[dict[str, object], ...]:
        with connect_database(self.path) as db:
            rows = db.execute(
                "SELECT request_json FROM scene_work_requests "
                "WHERE actor_key=? AND scene_version=? AND status='pending' "
                "ORDER BY created_at, request_id",
                (actor_key, scene_version),
            ).fetchall()
        return tuple(json.loads(row[0]) for row in rows)

    def scene_work_request(
        self, actor_key: str, request_id: str
    ) -> StoredSceneWorkRequest | None:
        with connect_database(self.path) as db:
            row = db.execute(
                "SELECT status, request_json FROM scene_work_requests "
                "WHERE actor_key=? AND request_id=?",
                (actor_key, request_id),
            ).fetchone()
        if row is None:
            return None
        return StoredSceneWorkRequest(
            request_id=request_id,
            actor_key=actor_key,
            status=str(row[0]),
            payload=json.loads(row[1]),
        )

    def refresh_pending_scene_work(
        self,
        actor_key: str,
        request_id: str,
        request: dict[str, object],
    ) -> bool:
        encoded = json.dumps(request, ensure_ascii=False, sort_keys=True)
        with connect_database(self.path) as db:
            cursor = db.execute(
                "UPDATE scene_work_requests SET request_json=?, updated_at=? "
                "WHERE actor_key=? AND request_id=? AND status='pending'",
                (encoded, int(time.time()), actor_key, request_id),
            )
            return cursor.rowcount == 1

    def resolve_scene_evaluation(
        self,
        actor_key: str,
        request_id: str,
        status: str,
        *,
        evaluation: dict[str, object] | None,
        keep_pending_request: dict[str, object] | None = None,
    ) -> bool:
        if status not in {"accepted", "stale"}:
            raise ValueError("scene work status must be accepted or stale")
        if status == "stale" and evaluation is not None:
            raise ValueError("stale scene work cannot persist an evaluation")
        if keep_pending_request is not None and (
            status != "accepted" or evaluation is None
        ):
            raise ValueError("pending continuation requires an accepted evaluation")
        db = connect_database(self.path)
        try:
            db.execute("BEGIN IMMEDIATE")
            if keep_pending_request is None:
                cursor = db.execute(
                    "UPDATE scene_work_requests SET status=?, updated_at=? "
                    "WHERE actor_key=? AND request_id=? AND status='pending'",
                    (status, int(time.time()), actor_key, request_id),
                )
            else:
                encoded_request = json.dumps(
                    keep_pending_request,
                    ensure_ascii=False,
                    sort_keys=True,
                )
                cursor = db.execute(
                    "UPDATE scene_work_requests SET request_json=?, updated_at=? "
                    "WHERE actor_key=? AND request_id=? AND status='pending'",
                    (
                        encoded_request,
                        int(time.time()),
                        actor_key,
                        request_id,
                    ),
                )
            if cursor.rowcount != 1:
                if status == "accepted" and evaluation is not None:
                    accepted = self._resolved_evaluation_matches(
                        db,
                        actor_key,
                        request_id,
                        evaluation,
                    )
                    db.rollback()
                    return accepted
                db.rollback()
                return False
            if evaluation is not None:
                if not self._evaluation_identity_matches(db, evaluation):
                    self._insert_shadow_evaluation(db, actor_key, evaluation)
            db.commit()
            return True
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _resolved_evaluation_matches(
        db: sqlite3.Connection,
        actor_key: str,
        request_id: str,
        evaluation: dict[str, object],
    ) -> bool:
        work = db.execute(
            "SELECT status FROM scene_work_requests "
            "WHERE actor_key=? AND request_id=?",
            (actor_key, request_id),
        ).fetchone()
        if work is None or str(work[0]) not in {"pending", "accepted"}:
            return False
        return SQLiteSocialEventStore._evaluation_identity_matches(db, evaluation)

    @staticmethod
    def _evaluation_identity_matches(
        db: sqlite3.Connection,
        evaluation: dict[str, object],
    ) -> bool:
        result = db.execute(
            "SELECT result_json FROM governor_results WHERE result_id=?",
            (str(evaluation.get("result_id") or ""),),
        ).fetchone()
        journal = db.execute(
            "SELECT effect_json FROM journal WHERE effect_id=?",
            (str(evaluation.get("effect_id") or ""),),
        ).fetchone()
        encoded = json.dumps(evaluation, ensure_ascii=False, sort_keys=True)
        if result is None and journal is None:
            return False
        if result is None or journal is None:
            raise JournalEffectIdentityConflict(
                "scene work is missing part of its evaluation identity"
            )
        if str(result[0]) != encoded or str(journal[0]) != encoded:
            raise JournalEffectIdentityConflict(
                "scene work belongs to a different evaluation"
            )
        return True

    @staticmethod
    def _insert_shadow_evaluation(
        db: sqlite3.Connection,
        actor_key: str,
        evaluation: dict[str, object],
    ) -> None:
        required = (
            "effect_id",
            "kind",
            "result_id",
            "frame_id",
            "source_event_id",
            "correlation_id",
            "persona_id",
            "group_id",
            "scene_version",
            "governor_result",
        )
        if any(evaluation.get(key) in (None, "") for key in required):
            raise ValueError("shadow evaluation identity is incomplete")
        now = int(time.time())
        encoded = json.dumps(evaluation, ensure_ascii=False, sort_keys=True)
        db.execute(
            "INSERT INTO journal("
            "effect_id, source_event_id, correlation_id, causation_id, "
            "actor_key, effect_type, effect_json, committed_at"
            ") VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(evaluation["effect_id"]),
                str(evaluation["source_event_id"]),
                str(evaluation["correlation_id"]),
                evaluation.get("causation_id"),
                actor_key,
                str(evaluation["kind"]),
                encoded,
                now,
            ),
        )
        db.execute(
            "INSERT INTO governor_results("
            "result_id, frame_id, persona_id, group_id, scene_version, "
            "result_json, created_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
            (
                str(evaluation["result_id"]),
                str(evaluation["frame_id"]),
                str(evaluation["persona_id"]),
                str(evaluation["group_id"]),
                int(evaluation["scene_version"]),
                encoded,
                now,
            ),
        )

    def event_envelopes(
        self,
        persona_id: str,
        group_id: str,
        event_ids: tuple[str, ...],
    ) -> tuple[SocialEventEnvelope, ...]:
        if not persona_id.strip() or not group_id.strip():
            raise ValueError("event context requires persona_id and group_id")
        if not event_ids:
            return ()
        placeholders = ",".join("?" for _ in event_ids)
        with connect_database(self.path) as db:
            rows = db.execute(
                f"SELECT event_id, envelope_json FROM inbox "
                f"WHERE persona_id=? AND group_id=? "
                f"AND event_id IN ({placeholders})",
                (persona_id, group_id, *event_ids),
            ).fetchall()
        by_id = {
            str(row["event_id"]): SocialEventEnvelope.from_dict(
                json.loads(row["envelope_json"])
            )
            for row in rows
        }
        return tuple(by_id[event_id] for event_id in event_ids if event_id in by_id)

    def shadow_evaluations(
        self, persona_id: str, group_id: str
    ) -> tuple[dict[str, object], ...]:
        if not persona_id.strip() or not group_id.strip():
            raise ValueError("shadow scope requires persona_id and group_id")
        with connect_database(self.path) as db:
            rows = db.execute(
                "SELECT result_json FROM governor_results "
                "WHERE persona_id=? AND group_id=? "
                "ORDER BY created_at, result_id",
                (persona_id, group_id),
            ).fetchall()
        return tuple(json.loads(row[0]) for row in rows)

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
