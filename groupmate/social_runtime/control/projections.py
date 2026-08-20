"""Independent, privacy-trimmed Journal consumers for the control plane."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from ..persistence.schema import connect_database, initialize_database


@dataclass(frozen=True)
class ProjectionProgress:
    projection_name: str
    cursor: int
    projection_version: int
    applied: int


@dataclass(frozen=True)
class _ProjectedItem:
    entity_key: str
    entity_ref: str
    persona_id: str
    group_id: str | None
    kind: str
    summary: dict[str, object]
    evidence_refs: tuple[str, ...]
    as_of: int


_SAFE_SCALAR_FIELDS = (
    "status",
    "outcome",
    "scene_version",
    "config_version",
    "control_version",
    "persona_state_version",
    "delivery_relevant",
    "direct_request",
    "disposition",
    "reconsider_at",
    "occurred_at",
    "expires_at",
    "progress",
    "error_code",
    "result_status",
    "task_status",
    "culture_status",
    "runtime_mode",
    "paused",
    "decision",
    "labels_frozen",
    "split",
    "manifest_version",
)
_SAFE_SEQUENCE_FIELDS = ("reason_codes", "constraints", "categories")
_ENTITY_FIELDS = (
    "task_id",
    "plan_id",
    "bundle_id",
    "part_id",
    "subject_id",
    "artifact_id",
    "result_id",
    "frame_id",
    "config_id",
    "entity_ref",
)


class ProjectionConsumer:
    """Consumes one named read model without sharing another model's cursor."""

    PROJECTION_NAMES = (
        "runtime",
        "activity",
        "scenes",
        "people",
        "culture",
        "tasks",
        "persona",
        "governance",
        "evaluation",
        "health",
    )

    def __init__(self, path: Path, projection_name: str) -> None:
        self.path = Path(path)
        self.projection_name = self._validate_name(projection_name)
        initialize_database(self.path)
        self._ensure_tables()

    def consume(self, limit: int) -> ProjectionProgress:
        if int(limit) < 1:
            raise ValueError("projection consume limit must be positive")
        db = connect_database(self.path)
        try:
            db.execute("BEGIN IMMEDIATE")
            cursor_row = db.execute(
                "SELECT last_journal_rowid, version FROM projection_cursors "
                "WHERE projection_name=?",
                (self.projection_name,),
            ).fetchone()
            cursor = int(cursor_row[0]) if cursor_row is not None else 0
            version = int(cursor_row[1]) if cursor_row is not None else 0
            rows = db.execute(
                "SELECT journal.rowid AS journal_rowid, journal.effect_id, "
                "journal.effect_type, journal.effect_json, journal.committed_at, "
                "inbox.persona_id, inbox.group_id, inbox.envelope_json "
                "FROM journal LEFT JOIN inbox "
                "ON inbox.event_id=journal.source_event_id "
                "WHERE journal.rowid>? ORDER BY journal.rowid LIMIT ?",
                (cursor, int(limit)),
            ).fetchall()
            applied = 0
            latest_as_of = None
            for row in rows:
                latest_as_of = int(row["committed_at"])
                seen = db.execute(
                    "SELECT 1 FROM control_projection_applied "
                    "WHERE projection_name=? AND effect_id=?",
                    (self.projection_name, str(row["effect_id"])),
                ).fetchone()
                if seen is not None:
                    continue
                payload = json.loads(str(row["effect_json"]))
                envelope = (
                    json.loads(str(row["envelope_json"]))
                    if row["envelope_json"] is not None
                    else {}
                )
                item = self._project_effect(
                    effect_id=str(row["effect_id"]),
                    effect_type=str(row["effect_type"]),
                    payload=payload,
                    persona_id=(
                        str(row["persona_id"])
                        if row["persona_id"] is not None
                        else str(payload.get("persona_id") or "")
                    ),
                    group_id=(
                        str(row["group_id"])
                        if row["group_id"] is not None
                        else self._optional_text(payload.get("group_id"))
                    ),
                    source_event_type=str(envelope.get("event_type") or ""),
                    source_correlation_id=str(
                        envelope.get("correlation_id") or ""
                    ),
                    source_payload=(
                        envelope.get("payload")
                        if isinstance(envelope.get("payload"), Mapping)
                        else {}
                    ),
                    committed_at=int(row["committed_at"]),
                )
                if item is not None:
                    version += 1
                    self._upsert_item(
                        db,
                        item,
                        version,
                        effect_id=str(row["effect_id"]),
                        source_journal_rowid=int(row["journal_rowid"]),
                    )
                    applied += 1
                db.execute(
                    "INSERT INTO control_projection_applied("
                    "projection_name, effect_id) VALUES(?, ?)",
                    (self.projection_name, str(row["effect_id"])),
                )
            if rows:
                cursor = int(rows[-1]["journal_rowid"])
                db.execute(
                    "INSERT INTO projection_cursors("
                    "projection_name, last_journal_rowid, version, updated_at"
                    ") VALUES(?, ?, ?, ?) "
                    "ON CONFLICT(projection_name) DO UPDATE SET "
                    "last_journal_rowid=excluded.last_journal_rowid, "
                    "version=excluded.version, updated_at=excluded.updated_at",
                    (
                        self.projection_name,
                        cursor,
                        version,
                        int(latest_as_of or 0),
                    ),
                )
            db.commit()
            return ProjectionProgress(
                self.projection_name,
                cursor,
                version,
                applied,
            )
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def rebuild(self, name: str) -> int:
        projection_name = self._validate_name(name)
        with connect_database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "DELETE FROM control_projection_items WHERE projection_name=?",
                (projection_name,),
            )
            db.execute(
                "DELETE FROM control_projection_applied WHERE projection_name=?",
                (projection_name,),
            )
            db.execute(
                "DELETE FROM control_projection_events WHERE projection_name=?",
                (projection_name,),
            )
            db.execute(
                "DELETE FROM projection_cursors WHERE projection_name=?",
                (projection_name,),
            )
            db.commit()

        target = self if projection_name == self.projection_name else type(self)(
            self.path, projection_name
        )
        total = 0
        while True:
            progress = target.consume(256)
            total += progress.applied
            if progress.cursor == self._journal_head():
                return total

    def _project_effect(
        self,
        *,
        effect_id: str,
        effect_type: str,
        payload: Mapping[str, object],
        persona_id: str,
        group_id: str | None,
        source_event_type: str,
        source_correlation_id: str,
        source_payload: Mapping[str, object],
        committed_at: int,
    ) -> _ProjectedItem | None:
        effect_matches = self._matches(effect_type)
        source_matches = bool(source_event_type) and self._matches(source_event_type)
        if not effect_matches and not source_matches:
            return None
        projected_kind = source_event_type if source_matches and not effect_matches else effect_type
        projected_payload = dict(payload)
        if source_matches:
            projected_payload.update(source_payload)
        identity = next(
            (
                str(projected_payload[field]).strip()
                for field in _ENTITY_FIELDS
                if str(projected_payload.get(field) or "").strip()
            ),
            effect_id,
        )
        scope = f"{persona_id}\0{group_id or ''}\0{identity}"
        entity_key = hashlib.sha256(scope.encode()).hexdigest()
        supplied_ref = str(projected_payload.get("entity_ref") or "").strip()
        entity_ref = (
            supplied_ref
            if (
                (
                    projected_kind == "evaluation.shadow_decision_captured"
                    and supplied_ref.startswith("evaluation:")
                )
                or (
                    projected_kind.startswith("calibration.shadow_")
                    and supplied_ref.startswith("calibration:")
                )
            )
            and len(supplied_ref) <= 64
            else (
                f"{self.projection_name}:"
                f"{hashlib.sha256((self.projection_name + chr(0) + scope).encode()).hexdigest()[:20]}"
            )
        )
        summary = self._safe_summary(projected_kind, projected_payload)
        if source_event_type.startswith("control.") and source_correlation_id:
            summary["command_id"] = source_correlation_id
        evidence_refs = tuple(
            f"evidence:{hashlib.sha256(str(value).encode()).hexdigest()[:20]}"
            for value in self._sequence(projected_payload.get("evidence_event_ids"))
            if str(value).strip()
        )
        return _ProjectedItem(
            entity_key=entity_key,
            entity_ref=entity_ref,
            persona_id=persona_id,
            group_id=group_id,
            kind=projected_kind,
            summary=summary,
            evidence_refs=evidence_refs,
            as_of=int(committed_at),
        )

    def _matches(self, effect_type: str) -> bool:
        if self.projection_name in {"activity", "health"}:
            return self.projection_name == "activity"
        prefixes = {
            "runtime": ("group_world.", "runtime.", "mode.", "persona."),
            "scenes": (
                "group_world.",
                "attention.",
                "cognition.",
                "governor.",
                "shadow.governor_",
            ),
            "people": ("relationship.", "impression.", "memory."),
            "culture": ("culture.",),
            "tasks": ("task.", "capability.", "delivery.", "outbox.", "plan."),
            "persona": ("persona.", "constitution.", "mode.", "style.", "media."),
            "governance": (
                "governance.",
                "config.",
                "calibration.",
                "control.",
            ),
            "evaluation": (
                "evaluation.",
                "shadow.",
                "governor.",
                "control.shadow_",
            ),
        }
        return effect_type.startswith(prefixes[self.projection_name])

    @staticmethod
    def _safe_summary(
        effect_type: str, payload: Mapping[str, object]
    ) -> dict[str, object]:
        summary: dict[str, object] = {"kind": effect_type}
        for field in _SAFE_SCALAR_FIELDS:
            value = payload.get(field)
            if isinstance(value, (str, int, float, bool)) or value is None:
                if value is not None:
                    summary[field] = value
        for field in _SAFE_SEQUENCE_FIELDS:
            values = ProjectionConsumer._sequence(payload.get(field))
            if values and all(
                isinstance(value, (str, int, float, bool)) for value in values
            ):
                summary[field] = list(values)
        governor = payload.get("governor_result")
        if isinstance(governor, Mapping):
            safe_governor = {
                field: governor[field]
                for field in ("outcome", "reason_codes", "constraints", "reconsider_at")
                if field in governor
                and (
                    isinstance(governor[field], (str, int, float, bool))
                    or (
                        isinstance(governor[field], (list, tuple))
                        and all(
                            isinstance(value, (str, int, float, bool))
                            for value in governor[field]
                        )
                    )
                )
            }
            if safe_governor:
                summary["governor"] = safe_governor
        if (
            payload.get("admin_visible") is True
            and str(payload.get("sensitivity") or "normal") != "restricted"
            and isinstance(payload.get("fact_summary"), str)
            and str(payload["fact_summary"]).strip()
        ):
            summary["fact_summary"] = str(payload["fact_summary"]).strip()
        if effect_type == "evaluation.shadow_decision_captured":
            summary.update(ProjectionConsumer._safe_shadow_review(payload))
        if effect_type == "calibration.shadow_candidate_evaluated":
            summary["comparison"] = ProjectionConsumer._safe_calibration_comparison(
                payload.get("comparison")
            )
        return summary

    @staticmethod
    def _safe_calibration_comparison(value: object) -> dict[str, object]:
        if not isinstance(value, Mapping):
            return {}
        result: dict[str, object] = {}
        fields = (
            "worker_mode",
            "report_kind",
            "safety_issue_count",
            "false_positive_rate",
            "attention_precision",
            "action_precision",
            "target_precision",
        )
        for split in ("calibration", "holdout"):
            split_value = value.get(split)
            if not isinstance(split_value, Mapping):
                continue
            sides = {}
            for side in ("baseline", "candidate"):
                side_value = split_value.get(side)
                if not isinstance(side_value, Mapping):
                    continue
                safe_side = {
                    field: side_value[field]
                    for field in fields
                    if isinstance(side_value.get(field), (str, int, float))
                    and not isinstance(side_value.get(field), bool)
                }
                lanes_value = side_value.get("lanes")
                if isinstance(lanes_value, Mapping):
                    safe_side["lanes"] = {
                        lane_name: ProjectionConsumer._safe_calibration_lane(
                            lanes_value.get(lane_name)
                        )
                        for lane_name in (
                            "SOCIAL_CONVERSATION",
                            "GROUPMATE_CAPABILITY",
                            "EXTERNAL_PLUGIN_COMPATIBILITY",
                        )
                    }
                sides[side] = safe_side
            result[split] = sides
        return result

    @staticmethod
    def _safe_calibration_lane(value: object) -> dict[str, object]:
        if not isinstance(value, Mapping):
            return {"effect_count": 0, "applicable": False}
        count = value.get("effect_count")
        result: dict[str, object] = {
            "effect_count": (
                int(count)
                if isinstance(count, (int, float)) and not isinstance(count, bool)
                else 0
            ),
            "applicable": value.get("applicable") is True,
        }
        for group, names in (
            ("quality", ("task", "delivery", "recovery")),
            (
                "compatibility",
                ("no_steal", "no_duplicate", "no_self_attribution"),
            ),
        ):
            values = value.get(group)
            if isinstance(values, Mapping):
                result[group] = {
                    name: values.get(name)
                    for name in names
                    if values.get(name) is None
                    or (
                        isinstance(values.get(name), (int, float))
                        and not isinstance(values.get(name), bool)
                    )
                }
        return result

    @staticmethod
    def _safe_shadow_review(payload: Mapping[str, object]) -> dict[str, object]:
        def safe_text(value: object, limit: int) -> str:
            text = str(value or "").strip()
            text = re.sub(r"https?://\S+", "[link]", text, flags=re.IGNORECASE)
            text = re.sub(
                r"(?<![A-Za-z0-9])\d{5,}(?![A-Za-z0-9])",
                "[number]",
                text,
            )
            text = re.sub(
                r"chain[_ -]?of[_ -]?thought|system[_ -]?prompt|\bprompt\b|api[_ -]?key|auth[_ -]?code",
                "[protected]",
                text,
                flags=re.IGNORECASE,
            )
            return text[:limit]

        def event_summary(value: object) -> dict[str, object] | None:
            if not isinstance(value, Mapping):
                return None
            actor_ref = str(value.get("actor_ref") or "").strip()
            if actor_ref and not actor_ref.startswith("member:"):
                actor_ref = "member:unknown"
            result: dict[str, object] = {
                "occurred_at": int(value.get("occurred_at") or 0),
                "actor_ref": actor_ref or None,
                "summary": safe_text(value.get("summary"), 240),
            }
            media = ProjectionConsumer._sequence(value.get("media"))
            if media and all(isinstance(item, str) for item in media):
                result["media"] = list(media[:8])
            return result

        history = tuple(
            item
            for item in (
                event_summary(value)
                for value in ProjectionConsumer._sequence(payload.get("history"))[-20:]
            )
            if item is not None
        )
        focus = tuple(
            item
            for item in (
                event_summary(value)
                for value in ProjectionConsumer._sequence(payload.get("focus"))[:1]
            )
            if item is not None
        )
        attention_value = payload.get("attention")
        attention = {}
        if isinstance(attention_value, Mapping):
            for name in ("trigger_kind", "urgency", "deadline"):
                value = attention_value.get(name)
                if isinstance(value, (str, int)) and not isinstance(value, bool):
                    attention[name] = value
        candidate_actions = []
        for value in ProjectionConsumer._sequence(payload.get("candidate_actions")):
            if not isinstance(value, Mapping):
                continue
            candidate_actions.append(
                {
                    name: str(value.get(name) or "").strip()[:80]
                    for name in ("kind", "proposed_act")
                }
            )
        categories = [
            safe_text(value, 80)
            for value in ProjectionConsumer._sequence(
                payload.get("suggested_categories")
            )
            if isinstance(value, str) and str(value).strip()
        ]
        return {
            "history": list(history),
            "focus": list(focus),
            "attention": attention,
            "target": (
                safe_text(payload.get("target"), 64)
                if str(payload.get("target") or "").startswith("member:")
                else None
            ),
            "candidate_response": (
                safe_text(payload.get("candidate_response"), 500)
                if payload.get("candidate_response") is not None
                else None
            ),
            "candidate_actions": candidate_actions,
            "suggested_categories": categories,
            "expires_at": int(payload.get("expires_at") or 0),
        }

    def _upsert_item(
        self,
        db: sqlite3.Connection,
        item: _ProjectedItem,
        version: int,
        *,
        effect_id: str,
        source_journal_rowid: int,
    ) -> None:
        db.execute(
            "INSERT INTO control_projection_items("
            "projection_name, entity_key, entity_ref, persona_id, group_id, kind, "
            "projection_version, summary_json, evidence_refs_json, as_of"
            ") VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(projection_name, entity_key) DO UPDATE SET "
            "entity_ref=excluded.entity_ref, kind=excluded.kind, "
            "projection_version=excluded.projection_version, "
            "summary_json=excluded.summary_json, "
            "evidence_refs_json=excluded.evidence_refs_json, as_of=excluded.as_of",
            (
                self.projection_name,
                item.entity_key,
                item.entity_ref,
                item.persona_id,
                item.group_id,
                item.kind,
                version,
                json.dumps(item.summary, ensure_ascii=False, sort_keys=True),
                json.dumps(item.evidence_refs, ensure_ascii=False),
                item.as_of,
            ),
        )
        db.execute(
            "INSERT OR IGNORE INTO control_projection_events("
            "projection_name, source_effect_id, source_journal_rowid, "
            "persona_id, group_id, kind, entity_ref, projection_version, "
            "summary_json, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                self.projection_name,
                effect_id,
                int(source_journal_rowid),
                item.persona_id,
                item.group_id,
                item.kind,
                item.entity_ref,
                version,
                json.dumps(item.summary, ensure_ascii=False, sort_keys=True),
                item.as_of,
            ),
        )

    def _ensure_tables(self) -> None:
        with connect_database(self.path) as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS control_projection_items (
                    projection_name TEXT NOT NULL,
                    entity_key TEXT NOT NULL,
                    entity_ref TEXT NOT NULL,
                    persona_id TEXT NOT NULL,
                    group_id TEXT,
                    kind TEXT NOT NULL,
                    projection_version INTEGER NOT NULL,
                    summary_json TEXT NOT NULL,
                    evidence_refs_json TEXT NOT NULL,
                    as_of INTEGER NOT NULL,
                    PRIMARY KEY(projection_name, entity_key)
                );
                CREATE INDEX IF NOT EXISTS idx_control_projection_scope
                    ON control_projection_items(
                        projection_name, persona_id, group_id, projection_version
                    );
                CREATE TABLE IF NOT EXISTS control_projection_applied (
                    projection_name TEXT NOT NULL,
                    effect_id TEXT NOT NULL,
                    PRIMARY KEY(projection_name, effect_id)
                );
                CREATE TABLE IF NOT EXISTS control_projection_source (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    last_journal_rowid INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS control_projection_events (
                    stream_cursor INTEGER PRIMARY KEY AUTOINCREMENT,
                    projection_name TEXT NOT NULL,
                    source_effect_id TEXT NOT NULL,
                    source_journal_rowid INTEGER NOT NULL,
                    persona_id TEXT NOT NULL,
                    group_id TEXT,
                    kind TEXT NOT NULL,
                    entity_ref TEXT NOT NULL,
                    projection_version INTEGER NOT NULL,
                    summary_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    UNIQUE(projection_name, source_effect_id)
                );
                CREATE INDEX IF NOT EXISTS idx_control_projection_events_scope
                    ON control_projection_events(
                        persona_id, group_id, stream_cursor
                    );
                INSERT INTO control_projection_source(singleton, last_journal_rowid)
                    VALUES(1, (SELECT COALESCE(MAX(rowid), 0) FROM journal))
                    ON CONFLICT(singleton) DO UPDATE SET
                    last_journal_rowid=MAX(
                        control_projection_source.last_journal_rowid,
                        excluded.last_journal_rowid
                    );
                CREATE TRIGGER IF NOT EXISTS control_projection_track_journal_head
                    AFTER INSERT ON journal
                    BEGIN
                        UPDATE control_projection_source
                        SET last_journal_rowid=NEW.rowid
                        WHERE singleton=1;
                    END;
                """
            )

    def _journal_head(self) -> int:
        with connect_database(self.path) as db:
            row = db.execute(
                "SELECT last_journal_rowid FROM control_projection_source "
                "WHERE singleton=1"
            ).fetchone()
        return int(row[0])

    @classmethod
    def _validate_name(cls, name: str) -> str:
        normalized = str(name).strip()
        if normalized not in cls.PROJECTION_NAMES:
            raise ValueError(f"unknown projection: {normalized}")
        return normalized

    @staticmethod
    def _optional_text(value: object) -> str | None:
        normalized = str(value or "").strip()
        return normalized or None

    @staticmethod
    def _sequence(value: object) -> tuple[object, ...]:
        if isinstance(value, (list, tuple)):
            return tuple(value)
        return ()


__all__ = ("ProjectionConsumer", "ProjectionProgress")
