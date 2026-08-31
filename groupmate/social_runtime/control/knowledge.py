"""Safe read models for administrator knowledge review."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from ..persistence.schema import connect_database, initialize_database


class KnowledgeControlQueries:
    """Expose only review-safe knowledge fields for one group."""

    _PAGE_SIZE = 50

    def __init__(self, path: Path, *, persona_id: str) -> None:
        self.path = Path(path)
        initialize_database(self.path)
        self.persona_id = self._required_text(persona_id, "persona_id")

    def overview(self, group_id: str, now: int) -> dict[str, object]:
        scope = self._required_text(group_id, "group_id")
        timestamp = int(now)
        with connect_database(self.path) as db:
            counts = {
                "entities": int(
                    db.execute(
                        "SELECT COUNT(*) FROM knowledge_entities"
                    ).fetchone()[0]
                ),
                "active_claims": int(
                    db.execute(
                        "SELECT COUNT(*) FROM knowledge_claims WHERE status='active'"
                    ).fetchone()[0]
                ),
                "review_conventions": int(
                    db.execute(
                        "SELECT COUNT(*) FROM group_conventions WHERE group_id=? "
                        "AND status IN ('candidate','disputed')",
                        (scope,),
                    ).fetchone()[0]
                ),
                "retry_jobs": int(
                    db.execute(
                        "SELECT COUNT(*) FROM knowledge_jobs WHERE "
                        "(group_id IS NULL OR group_id=?) AND status='retry'",
                        (scope,),
                    ).fetchone()[0]
                ),
            }
            popular_rows = db.execute(
                "SELECT e.entity_id,e.canonical_name,e.status,a.salience,"
                "a.qualified_mention_count,a.distinct_actor_count,"
                "a.distinct_scene_count,a.last_seen_at "
                "FROM group_topic_affinity AS a "
                "JOIN knowledge_entities AS e ON e.entity_id=a.entity_id "
                "WHERE a.group_id=? ORDER BY a.salience DESC,a.last_seen_at DESC "
                "LIMIT 12",
                (scope,),
            ).fetchall()
            popular = [
                {
                    "entity_id": str(row["entity_id"]),
                    "canonical_name": str(row["canonical_name"]),
                    "status": str(row["status"]),
                    "salience": float(row["salience"]),
                    "qualified_mention_count": int(row["qualified_mention_count"]),
                    "distinct_actor_count": int(row["distinct_actor_count"]),
                    "distinct_scene_count": int(row["distinct_scene_count"]),
                    "last_seen_at": int(row["last_seen_at"]),
                }
                for row in popular_rows
            ]
            release_rows = db.execute(
                "SELECT r.version_slot_id,r.game_entity_id,e.canonical_name,"
                "r.official_label,r.region,r.platform,r.release_state,"
                "r.official_state,r.rumor_state,r.release_checked_at,"
                "r.official_checked_at,r.rumor_checked_at,r.fresh_until,"
                "r.status,r.revision,(SELECT MAX(j.updated_at) FROM knowledge_jobs AS j "
                "WHERE j.entity_id=r.game_entity_id AND (j.group_id IS NULL OR j.group_id=?)) "
                "AS last_attempt_at FROM game_release_states AS r "
                "JOIN knowledge_entities AS e ON e.entity_id=r.game_entity_id "
                "WHERE r.status IN ('active','disputed') "
                "ORDER BY r.fresh_until ASC,e.canonical_name LIMIT 24",
                (scope,),
            ).fetchall()
            release_states = [
                {
                    "version_slot_id": str(row["version_slot_id"]),
                    "entity_id": str(row["game_entity_id"]),
                    "canonical_name": str(row["canonical_name"]),
                    "official_label": (
                        None
                        if row["official_label"] is None
                        else str(row["official_label"])
                    ),
                    "region": str(row["region"]),
                    "platform": str(row["platform"]),
                    "release_state": str(row["release_state"]),
                    "official_state": str(row["official_state"]),
                    "rumor_state": str(row["rumor_state"]),
                    "last_successful_check_at": max(
                        value
                        for value in (
                            self._nullable_int(row["release_checked_at"]),
                            self._nullable_int(row["official_checked_at"]),
                            self._nullable_int(row["rumor_checked_at"]),
                            0,
                        )
                        if value is not None
                    ),
                    "last_attempt_at": self._nullable_int(row["last_attempt_at"]),
                    "fresh_until": int(row["fresh_until"]),
                    "fresh": int(row["fresh_until"]) >= timestamp,
                    "status": str(row["status"]),
                    "revision": int(row["revision"]),
                }
                for row in release_rows
            ]
            usage_rows = db.execute(
                "SELECT source_domains_json,latency_ms,cache_hit,result_kind,"
                "diagnostic_code,recorded_at FROM knowledge_usage WHERE group_id=? "
                "ORDER BY recorded_at DESC,usage_id DESC LIMIT 20",
                (scope,),
            ).fetchall()
            recent_usage = [
                {
                    "source_domains": [
                        str(value)
                        for value in json.loads(str(row["source_domains_json"]))
                    ],
                    "latency_ms": int(row["latency_ms"]),
                    "cache_hit": bool(row["cache_hit"]),
                    "result_kind": str(row["result_kind"]),
                    "diagnostic": (
                        None
                        if row["diagnostic_code"] is None
                        else str(row["diagnostic_code"])
                    ),
                    "recorded_at": int(row["recorded_at"]),
                }
                for row in usage_rows
            ]
            revision = self._control_revision_on(db, scope)
            canary = self._ambient_canary_on(db, scope)
        return {
            "group_id": scope,
            "as_of": timestamp,
            "revision": revision,
            "counts": counts,
            "popular_games": popular,
            "release_states": release_states,
            "recent_usage": recent_usage,
            "ambient_canary_enabled": canary,
        }

    def entities(
        self,
        group_id: str,
        cursor: str | None,
        filters: Mapping[str, object] | None,
    ) -> dict[str, object]:
        scope = self._required_text(group_id, "group_id")
        offset = self._offset(cursor)
        values = dict(filters or {})
        clauses: list[str] = []
        parameters: list[object] = []
        status = self._optional_filter(values, "status")
        entity_type = self._optional_filter(values, "entity_type")
        if status:
            clauses.append("e.status=?")
            parameters.append(status)
        if entity_type:
            clauses.append("e.entity_type=?")
            parameters.append(entity_type)
        where = "" if not clauses else "WHERE " + " AND ".join(clauses)
        with connect_database(self.path) as db:
            rows = db.execute(
                "SELECT e.entity_id,e.entity_type,e.canonical_name,"
                "e.canonical_game_id,e.status,e.updated_at,a.salience,"
                "a.qualified_mention_count,a.last_seen_at "
                "FROM knowledge_entities AS e LEFT JOIN group_topic_affinity AS a "
                "ON a.entity_id=e.entity_id AND a.group_id=? "
                f"{where} ORDER BY e.canonical_name,e.entity_id LIMIT ? OFFSET ?",
                (scope, *parameters, self._PAGE_SIZE + 1, offset),
            ).fetchall()
            revision = self._control_revision_on(db, scope)
        return self._page(
            [
                {
                    "entity_id": str(row["entity_id"]),
                    "entity_type": str(row["entity_type"]),
                    "canonical_name": str(row["canonical_name"]),
                    "canonical_game_id": str(row["canonical_game_id"]),
                    "scope": "global",
                    "status": str(row["status"]),
                    "revision": int(row["updated_at"]),
                    "group_salience": (
                        None
                        if row["salience"] is None
                        else {
                            "value": float(row["salience"]),
                            "qualified_mention_count": int(
                                row["qualified_mention_count"]
                            ),
                            "last_seen_at": int(row["last_seen_at"]),
                        }
                    ),
                }
                for row in rows
            ],
            offset,
            revision,
        )

    def claims(
        self,
        group_id: str,
        cursor: str | None,
        filters: Mapping[str, object] | None,
    ) -> dict[str, object]:
        scope = self._required_text(group_id, "group_id")
        offset = self._offset(cursor)
        values = dict(filters or {})
        clauses: list[str] = []
        parameters: list[object] = []
        for key, column in (
            ("status", "c.status"),
            ("claim_kind", "c.claim_kind"),
            ("entity_id", "c.subject_entity_id"),
        ):
            value = self._optional_filter(values, key)
            if value:
                clauses.append(f"{column}=?")
                parameters.append(value)
        where = "" if not clauses else "WHERE " + " AND ".join(clauses)
        with connect_database(self.path) as db:
            rows = db.execute(
                "SELECT c.claim_id,c.subject_entity_id,e.canonical_name,c.predicate,"
                "c.safe_summary,c.claim_kind,c.evidence_level,c.status,c.valid_from,"
                "c.valid_until,c.checked_at,c.updated_at "
                "FROM knowledge_claims AS c JOIN knowledge_entities AS e "
                "ON e.entity_id=c.subject_entity_id "
                f"{where} ORDER BY c.updated_at DESC,c.claim_id LIMIT ? OFFSET ?",
                (*parameters, self._PAGE_SIZE + 1, offset),
            ).fetchall()
            items = []
            for row in rows:
                source_rows = db.execute(
                    "SELECT DISTINCT s.domain,s.source_class "
                    "FROM knowledge_claim_evidence AS ce "
                    "JOIN knowledge_sources AS s ON s.source_id=ce.source_id "
                    "WHERE ce.claim_id=? ORDER BY s.source_class,s.domain",
                    (row["claim_id"],),
                ).fetchall()
                items.append(
                    {
                        "claim_id": str(row["claim_id"]),
                        "entity_id": str(row["subject_entity_id"]),
                        "canonical_name": str(row["canonical_name"]),
                        "predicate": str(row["predicate"]),
                        "safe_summary": str(row["safe_summary"]),
                        "claim_kind": str(row["claim_kind"]),
                        "evidence_level": str(row["evidence_level"]),
                        "scope": "global",
                        "status": str(row["status"]),
                        "valid_from": self._nullable_int(row["valid_from"]),
                        "valid_until": self._nullable_int(row["valid_until"]),
                        "checked_at": self._nullable_int(row["checked_at"]),
                        "revision": int(row["updated_at"]),
                        "sources": [
                            {
                                "domain": str(source["domain"]),
                                "source_class": str(source["source_class"]),
                            }
                            for source in source_rows
                        ],
                    }
                )
            revision = self._control_revision_on(db, scope)
        return self._page(items, offset, revision)

    def conventions(
        self,
        group_id: str,
        cursor: str | None,
        filters: Mapping[str, object] | None,
    ) -> dict[str, object]:
        scope = self._required_text(group_id, "group_id")
        offset = self._offset(cursor)
        values = dict(filters or {})
        clauses = ["c.group_id=?"]
        parameters: list[object] = [scope]
        status = self._optional_filter(values, "status")
        if status:
            clauses.append("c.status=?")
            parameters.append(status)
        with connect_database(self.path) as db:
            rows = db.execute(
                "SELECT c.convention_id,c.normalized_expression,c.resolved_entity_id,"
                "e.canonical_name,c.meaning_summary,c.distinct_actor_count,"
                "c.distinct_scene_count,c.confidence,c.status,c.first_seen_at,"
                "c.last_seen_at,c.updated_at,a.alias_id FROM group_conventions AS c "
                "LEFT JOIN knowledge_entities AS e ON e.entity_id=c.resolved_entity_id "
                "LEFT JOIN group_knowledge_aliases AS a ON a.group_id=c.group_id "
                "AND a.normalized_alias=c.normalized_expression "
                "AND a.entity_id=c.resolved_entity_id AND a.status='active' "
                "WHERE " + " AND ".join(clauses) +
                " ORDER BY c.updated_at DESC,c.convention_id LIMIT ? OFFSET ?",
                (*parameters, self._PAGE_SIZE + 1, offset),
            ).fetchall()
            revision = self._control_revision_on(db, scope)
        return self._page(
            [
                {
                    "convention_id": str(row["convention_id"]),
                    "expression": str(row["normalized_expression"]),
                    "entity_id": (
                        None
                        if row["resolved_entity_id"] is None
                        else str(row["resolved_entity_id"])
                    ),
                    "canonical_name": (
                        None
                        if row["canonical_name"] is None
                        else str(row["canonical_name"])
                    ),
                    "meaning_summary": str(row["meaning_summary"]),
                    "alias_id": (
                        None if row["alias_id"] is None else str(row["alias_id"])
                    ),
                    "scope": "group",
                    "group_id": scope,
                    "status": str(row["status"]),
                    "confidence": float(row["confidence"]),
                    "distinct_actor_count": int(row["distinct_actor_count"]),
                    "distinct_scene_count": int(row["distinct_scene_count"]),
                    "first_seen_at": int(row["first_seen_at"]),
                    "last_seen_at": int(row["last_seen_at"]),
                    "revision": int(row["updated_at"]),
                }
                for row in rows
            ],
            offset,
            revision,
        )

    def jobs(
        self,
        group_id: str,
        cursor: str | None,
        filters: Mapping[str, object] | None,
    ) -> dict[str, object]:
        scope = self._required_text(group_id, "group_id")
        offset = self._offset(cursor)
        values = dict(filters or {})
        clauses = ["(j.group_id IS NULL OR j.group_id=?)"]
        parameters: list[object] = [scope]
        for key, column in (("status", "j.status"), ("job_kind", "j.job_kind")):
            value = self._optional_filter(values, key)
            if value:
                clauses.append(f"{column}=?")
                parameters.append(value)
        with connect_database(self.path) as db:
            rows = db.execute(
                "SELECT j.job_id,j.job_kind,j.group_id,j.entity_id,e.canonical_name,"
                "j.status,j.attempt,j.next_attempt_at,j.diagnostic_code,j.created_at,"
                "j.updated_at FROM knowledge_jobs AS j LEFT JOIN knowledge_entities AS e "
                "ON e.entity_id=j.entity_id WHERE " + " AND ".join(clauses) +
                " ORDER BY j.updated_at DESC,j.job_id LIMIT ? OFFSET ?",
                (*parameters, self._PAGE_SIZE + 1, offset),
            ).fetchall()
            revision = self._control_revision_on(db, scope)
        return self._page(
            [
                {
                    "job_id": str(row["job_id"]),
                    "job_kind": str(row["job_kind"]),
                    "scope": "global" if row["group_id"] is None else "group",
                    "group_id": (
                        None if row["group_id"] is None else str(row["group_id"])
                    ),
                    "entity_id": (
                        None if row["entity_id"] is None else str(row["entity_id"])
                    ),
                    "canonical_name": (
                        None
                        if row["canonical_name"] is None
                        else str(row["canonical_name"])
                    ),
                    "status": str(row["status"]),
                    "attempt": int(row["attempt"]),
                    "next_attempt_at": int(row["next_attempt_at"]),
                    "diagnostic": (
                        None
                        if row["diagnostic_code"] is None
                        else str(row["diagnostic_code"])
                    ),
                    "created_at": int(row["created_at"]),
                    "revision": int(row["updated_at"]),
                }
                for row in rows
            ],
            offset,
            revision,
        )

    def _control_revision_on(self, db, group_id: str) -> int:
        rows = db.execute(
            "SELECT action_json FROM governance_actions WHERE persona_id=? "
            "AND group_id=?",
            (self.persona_id, group_id),
        ).fetchall()
        return max(
            (
                int(json.loads(str(row[0])).get("control_version", 0))
                for row in rows
            ),
            default=0,
        )

    def _ambient_canary_on(self, db, group_id: str) -> bool:
        row = db.execute(
            "SELECT action_json FROM governance_actions WHERE persona_id=? "
            "AND group_id=? AND action_type='knowledge.ambient_canary_enabled' "
            "ORDER BY created_at DESC,rowid DESC LIMIT 1",
            (self.persona_id, group_id),
        ).fetchone()
        if row is None:
            return False
        stored = json.loads(str(row[0]))
        return bool(stored.get("result", {}).get("data", {}).get("enabled", False))

    @classmethod
    def _page(
        cls, items: list[dict[str, object]], offset: int, revision: int
    ) -> dict[str, object]:
        has_more = len(items) > cls._PAGE_SIZE
        visible = items[: cls._PAGE_SIZE]
        return {
            "items": visible,
            "next_cursor": str(offset + cls._PAGE_SIZE) if has_more else None,
            "revision": revision,
        }

    @staticmethod
    def _offset(value: str | None) -> int:
        if value is None or str(value).strip() == "":
            return 0
        try:
            offset = int(str(value))
        except ValueError as exc:
            raise ValueError("invalid knowledge cursor") from exc
        if offset < 0:
            raise ValueError("invalid knowledge cursor")
        return offset

    @staticmethod
    def _optional_filter(values: Mapping[str, object], key: str) -> str | None:
        value = " ".join(str(values.get(key) or "").split())
        if not value:
            return None
        if len(value) > 128:
            raise ValueError(f"{key} filter is too long")
        return value

    @staticmethod
    def _nullable_int(value: object) -> int | None:
        return None if value is None else int(value)

    @staticmethod
    def _required_text(value: object, name: str) -> str:
        normalized = " ".join(str(value or "").split())
        if not normalized:
            raise ValueError(f"{name} must not be empty")
        return normalized


__all__ = ("KnowledgeControlQueries",)
