"""SQLite persistence for persona-independent knowledge projections."""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from ..persistence.schema import connect_database, initialize_database
from .contracts import (
    KnowledgeClaimCandidate,
    KnowledgeObservation,
    KnowledgeScope,
    NegativeSearchSnapshot,
    OriginClass,
    SourceEvidence,
    VersionSlot,
)


AFFINITY_HALF_LIFE_SECONDS = 7 * 24 * 60 * 60
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_ENRICHMENT_JOB_DUE_AT = 2**62


def _required_text(value: object, name: str, *, maximum: int = 128) -> str:
    normalized = " ".join(
        unicodedata.normalize("NFKC", str(value or "")).split()
    )
    if not normalized:
        raise ValueError(f"{name} must not be empty")
    if len(normalized) > maximum:
        raise ValueError(f"{name} is too long")
    return normalized


def _expression(value: object) -> str:
    return _required_text(value, "normalized_alias", maximum=48).casefold()


def _json(values: Iterable[object]) -> str:
    return json.dumps(
        list(values),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


@dataclass(frozen=True)
class KnowledgeAliasRecord:
    alias_id: str
    scope_kind: str
    group_id: str | None
    entity_id: str
    normalized_alias: str
    alias_kind: str
    ambiguity_level: str
    confidence: float
    status: str


@dataclass(frozen=True)
class TopicAffinity:
    group_id: str
    entity_id: str
    qualified_mention_count: int
    distinct_actor_count: int
    distinct_scene_count: int
    salience: float
    first_seen_at: int
    last_seen_at: int
    updated_at: int


@dataclass(frozen=True)
class SeedVersionRecord:
    seed_id: str
    seed_version: int
    content_hash: str
    status: str
    imported_at: int


class SeedVersionConflict(ValueError):
    """Raised when an immutable seed version is reused with new content."""


class SeedVersionOrderConflict(ValueError):
    """Raised when an absent older seed follows a newer active version."""


@dataclass(frozen=True)
class StoredKnowledgeObservation:
    observation_id: str
    group_id: str
    author_ref: str
    source_event_id: str
    scene_ref: str
    entity_hint: str | None
    safe_summary: str
    occurred_at: int
    recorded_at: int
    status: str


@dataclass(frozen=True)
class GroupConventionRecord:
    convention_id: str
    group_id: str
    normalized_expression: str
    resolved_entity_id: str
    meaning_summary: str
    evidence_observation_ids: tuple[str, ...]
    distinct_actor_count: int
    distinct_scene_count: int
    confidence: float
    status: str
    first_seen_at: int
    last_seen_at: int
    updated_at: int

    @property
    def evidence_count(self) -> int:
        return len(self.evidence_observation_ids)


@dataclass(frozen=True)
class KnowledgeEntityRecord:
    entity_id: str
    entity_type: str
    canonical_name: str
    canonical_game_id: str
    status: str


@dataclass(frozen=True)
class KnowledgeClaimRecord:
    claim_id: str
    subject_entity_id: str
    predicate: str
    safe_summary: str
    claim_kind: str
    evidence_level: str
    status: str
    checked_at: int | None


@dataclass(frozen=True)
class ClaimWriteResult:
    outcome: str
    claim_id: str
    superseded_claim_ids: tuple[str, ...]
    source_ids: tuple[str, ...]


@dataclass(frozen=True)
class NegativeSnapshotKey:
    game_entity_id: str
    query_intent: str
    region: str | None
    platform: str | None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "game_entity_id",
            _required_text(self.game_entity_id, "game_entity_id"),
        )
        object.__setattr__(
            self,
            "query_intent",
            _required_text(self.query_intent, "query_intent", maximum=64),
        )
        object.__setattr__(
            self,
            "region",
            None
            if self.region is None
            else _required_text(self.region, "region", maximum=48),
        )
        object.__setattr__(
            self,
            "platform",
            None
            if self.platform is None
            else _required_text(self.platform, "platform", maximum=48),
        )


@dataclass(frozen=True)
class NegativeSearchSnapshotRecord:
    snapshot_id: str
    game_entity_id: str
    query_intent: str
    covered_source_ids: tuple[str, ...]
    required_source_ids: tuple[str, ...]
    region: str | None
    platform: str | None
    checked_at: int
    expires_at: int
    version_state_revision: int
    status: str
    diagnostic_code: str


class ReleaseStateConflict(RuntimeError):
    """Raised when a release aggregate is saved from a stale revision."""


@dataclass(frozen=True)
class KnowledgeJobRecord:
    job_id: str
    idempotency_key: str
    job_kind: str
    group_id: str | None
    entity_id: str | None
    request: Mapping[str, Any]
    status: str
    attempt: int
    next_attempt_at: int
    diagnostic_code: str | None
    created_at: int
    updated_at: int


@dataclass(frozen=True)
class EnrichmentCacheRecord:
    intent_hash: str
    source_domains: tuple[str, ...]
    recorded_at: int


class KnowledgeRepository:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        initialize_database(self.path)

    @classmethod
    def confirm_convention_on(
        cls,
        db,
        *,
        group_id: str,
        convention_id: str,
        actor_id: str,
        reason: str,
        command_id: str,
        now: int,
    ) -> dict[str, object]:
        row = cls._scoped_convention_on(db, group_id, convention_id)
        entity_id = str(row["resolved_entity_id"] or "")
        entity = db.execute(
            "SELECT status FROM knowledge_entities WHERE entity_id=?",
            (entity_id,),
        ).fetchone()
        if not entity_id or entity is None or str(entity["status"]) != "active":
            raise ValueError("convention target entity is not active")
        cls._append_admin_observation_on(
            db,
            group_id=group_id,
            actor_id=actor_id,
            action="confirm_convention",
            target_id=convention_id,
            reason=reason,
            command_id=command_id,
            now=now,
        )
        revision = max(int(now), int(row["updated_at"]) + 1)
        db.execute(
            "UPDATE group_conventions SET status='active',confidence=1.0,"
            "updated_at=? WHERE convention_id=?",
            (revision, convention_id),
        )
        alias_id = "alias:group:" + hashlib.sha256(
            f"{group_id}\0{row['normalized_expression']}\0{entity_id}".encode()
        ).hexdigest()[:24]
        db.execute(
            "INSERT INTO group_knowledge_aliases(alias_id,group_id,entity_id,"
            "normalized_alias,evidence_observation_ids_json,confidence,last_used_at,"
            "status) VALUES(?,?,?,?,?,1.0,?,'active') "
            "ON CONFLICT(group_id,normalized_alias,entity_id) DO UPDATE SET "
            "evidence_observation_ids_json=excluded.evidence_observation_ids_json,"
            "confidence=1.0,last_used_at=excluded.last_used_at,status='active'",
            (
                alias_id,
                group_id,
                entity_id,
                str(row["normalized_expression"]),
                str(row["evidence_observation_ids_json"]),
                int(now),
            ),
        )
        return {
            "convention_id": convention_id,
            "entity_id": entity_id,
            "status": "active",
            "revision": revision,
        }

    @classmethod
    def reject_convention_on(
        cls,
        db,
        *,
        group_id: str,
        convention_id: str,
        actor_id: str,
        reason: str,
        command_id: str,
        now: int,
    ) -> dict[str, object]:
        row = cls._scoped_convention_on(db, group_id, convention_id)
        cls._append_admin_observation_on(
            db,
            group_id=group_id,
            actor_id=actor_id,
            action="reject_convention",
            target_id=convention_id,
            reason=reason,
            command_id=command_id,
            now=now,
        )
        revision = max(int(now), int(row["updated_at"]) + 1)
        db.execute(
            "UPDATE group_conventions SET status='rejected',confidence=0.0,"
            "updated_at=? WHERE convention_id=?",
            (revision, convention_id),
        )
        db.execute(
            "UPDATE group_knowledge_aliases SET status='rejected' "
            "WHERE group_id=? AND normalized_alias=? AND entity_id=?",
            (
                group_id,
                str(row["normalized_expression"]),
                str(row["resolved_entity_id"]),
            ),
        )
        return {
            "convention_id": convention_id,
            "status": "rejected",
            "revision": revision,
        }

    @classmethod
    def supersede_group_alias_on(
        cls,
        db,
        *,
        group_id: str,
        alias_id: str,
        replacement_entity_id: str,
        actor_id: str,
        reason: str,
        command_id: str,
        now: int,
    ) -> dict[str, object]:
        row = db.execute(
            "SELECT * FROM group_knowledge_aliases WHERE alias_id=? AND group_id=?",
            (alias_id, group_id),
        ).fetchone()
        if row is None:
            raise LookupError("group alias is not available")
        entity = db.execute(
            "SELECT status FROM knowledge_entities WHERE entity_id=?",
            (replacement_entity_id,),
        ).fetchone()
        if entity is None or str(entity["status"]) != "active":
            raise ValueError("replacement entity is not active")
        if str(row["entity_id"]) == replacement_entity_id:
            raise ValueError("replacement entity must differ from current target")
        cls._append_admin_observation_on(
            db,
            group_id=group_id,
            actor_id=actor_id,
            action="supersede_alias",
            target_id=alias_id,
            reason=reason,
            command_id=command_id,
            now=now,
        )
        db.execute(
            "UPDATE group_knowledge_aliases SET status='stale' WHERE alias_id=?",
            (alias_id,),
        )
        replacement_alias_id = "alias:group:admin:" + hashlib.sha256(
            f"{group_id}\0{row['normalized_alias']}\0{replacement_entity_id}".encode()
        ).hexdigest()[:20]
        db.execute(
            "INSERT INTO group_knowledge_aliases(alias_id,group_id,entity_id,"
            "normalized_alias,evidence_observation_ids_json,confidence,last_used_at,"
            "status) VALUES(?,?,?,?,?,1.0,?,'active') "
            "ON CONFLICT(group_id,normalized_alias,entity_id) DO UPDATE SET "
            "confidence=1.0,last_used_at=excluded.last_used_at,status='active'",
            (
                replacement_alias_id,
                group_id,
                replacement_entity_id,
                str(row["normalized_alias"]),
                str(row["evidence_observation_ids_json"]),
                int(now),
            ),
        )
        return {
            "alias_id": replacement_alias_id,
            "supersedes_alias_id": alias_id,
            "entity_id": replacement_entity_id,
            "status": "active",
        }

    @classmethod
    def dispute_claim_on(
        cls,
        db,
        *,
        group_id: str,
        claim_id: str,
        actor_id: str,
        reason: str,
        command_id: str,
        now: int,
    ) -> dict[str, object]:
        row = db.execute(
            "SELECT c.*,e.canonical_game_id FROM knowledge_claims AS c "
            "JOIN knowledge_entities AS e ON e.entity_id=c.subject_entity_id "
            "WHERE c.claim_id=?",
            (claim_id,),
        ).fetchone()
        if row is None:
            raise LookupError("knowledge claim is not available")
        cls._append_admin_observation_on(
            db,
            group_id=group_id,
            actor_id=actor_id,
            action="dispute_claim",
            target_id=claim_id,
            reason=reason,
            command_id=command_id,
            now=now,
        )
        revision = max(int(now), int(row["updated_at"]) + 1)
        db.execute(
            "UPDATE knowledge_claims SET status='disputed',updated_at=? "
            "WHERE claim_id=?",
            (revision, claim_id),
        )
        cls._invalidate_negative_snapshots(
            db, str(row["canonical_game_id"]), "admin_claim_disputed"
        )
        return {
            "claim_id": claim_id,
            "status": "disputed",
            "revision": revision,
        }

    @classmethod
    def retry_job_on(
        cls,
        db,
        *,
        group_id: str,
        job_id: str,
        actor_id: str,
        reason: str,
        command_id: str,
        now: int,
    ) -> dict[str, object]:
        row = db.execute(
            "SELECT * FROM knowledge_jobs WHERE job_id=? "
            "AND (group_id IS NULL OR group_id=?)",
            (job_id, group_id),
        ).fetchone()
        if row is None:
            raise LookupError("knowledge job is not available")
        if str(row["status"]) != "retry":
            raise ValueError("knowledge job is not eligible for manual retry")
        cls._append_admin_observation_on(
            db,
            group_id=group_id,
            actor_id=actor_id,
            action="retry_job",
            target_id=job_id,
            reason=reason,
            command_id=command_id,
            now=now,
        )
        revision = max(int(now), int(row["updated_at"]) + 1)
        db.execute(
            "UPDATE knowledge_jobs SET next_attempt_at=?,updated_at=? WHERE job_id=?",
            (int(now), revision, job_id),
        )
        return {
            "job_id": job_id,
            "status": "retry",
            "next_attempt_at": int(now),
            "revision": revision,
        }

    @classmethod
    def invalidate_cache_on(
        cls,
        db,
        *,
        group_id: str,
        entity_id: str,
        actor_id: str,
        reason: str,
        command_id: str,
        now: int,
    ) -> dict[str, object]:
        row = db.execute(
            "SELECT canonical_game_id FROM knowledge_entities WHERE entity_id=?",
            (entity_id,),
        ).fetchone()
        if row is None:
            raise LookupError("knowledge entity is not available")
        cls._append_admin_observation_on(
            db,
            group_id=group_id,
            actor_id=actor_id,
            action="invalidate_cache",
            target_id=entity_id,
            reason=reason,
            command_id=command_id,
            now=now,
        )
        invalidated = cls._invalidate_negative_snapshots(
            db, str(row["canonical_game_id"]), "admin_cache_invalidated"
        )
        return {
            "entity_id": entity_id,
            "status": "invalidated",
            "invalidated_snapshot_count": invalidated,
        }

    @classmethod
    def append_canary_audit_on(
        cls,
        db,
        *,
        group_id: str,
        actor_id: str,
        reason: str,
        command_id: str,
        enabled: bool,
        now: int,
    ) -> dict[str, object]:
        cls._append_admin_observation_on(
            db,
            group_id=group_id,
            actor_id=actor_id,
            action="ambient_canary_enabled",
            target_id=group_id,
            reason=reason,
            command_id=command_id,
            now=now,
        )
        return {"enabled": bool(enabled), "status": "enabled" if enabled else "disabled"}

    @staticmethod
    def _scoped_convention_on(db, group_id: str, convention_id: str):
        row = db.execute(
            "SELECT * FROM group_conventions WHERE convention_id=? AND group_id=?",
            (convention_id, group_id),
        ).fetchone()
        if row is None:
            raise LookupError("group convention is not available")
        return row

    @staticmethod
    def _append_admin_observation_on(
        db,
        *,
        group_id: str,
        actor_id: str,
        action: str,
        target_id: str,
        reason: str,
        command_id: str,
        now: int,
    ) -> None:
        observation_id = "observation:admin:" + hashlib.sha256(
            f"{group_id}\0{command_id}".encode()
        ).hexdigest()[:24]
        summary = json.dumps(
            {
                "action": action,
                "actor_id": actor_id,
                "reason": reason,
                "target_id": target_id,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        content_hash = hashlib.sha256(
            f"{command_id}\0{summary}".encode()
        ).hexdigest()
        db.execute(
            "INSERT INTO knowledge_observations(observation_id,origin_class,"
            "scope_kind,group_id,author_ref,source_event_id,source_id,entity_hint,"
            "safe_summary,content_hash,occurred_at,recorded_at,status) "
            "VALUES(?,'admin','group',?,NULL,?,?,?,?,?,?,?,'admitted')",
            (
                observation_id,
                group_id,
                command_id,
                f"control:knowledge:{action}",
                target_id[:48],
                summary,
                content_hash,
                int(now),
                int(now),
            ),
        )

    def append_observation(self, value: KnowledgeObservation) -> bool:
        with connect_database(self.path) as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO knowledge_observations("
                "observation_id,origin_class,scope_kind,group_id,author_ref,"
                "source_event_id,source_id,entity_hint,safe_summary,content_hash,"
                "occurred_at,recorded_at,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    value.observation_id,
                    value.origin_class.value,
                    value.scope_kind.value,
                    value.group_id,
                    value.author_ref,
                    value.source_event_id,
                    value.source_id,
                    value.entity_hint,
                    value.safe_summary,
                    value.content_hash,
                    int(value.occurred_at),
                    int(value.recorded_at),
                    value.status.value,
                ),
            )
            return cursor.rowcount == 1

    def observation_count(self, *, group_id: str) -> int:
        scope = _required_text(group_id, "group_id")
        with connect_database(self.path) as db:
            row = db.execute(
                "SELECT COUNT(*) FROM knowledge_observations WHERE group_id=?",
                (scope,),
            ).fetchone()
        return 0 if row is None else int(row[0])

    def pending_observations(self) -> tuple[StoredKnowledgeObservation, ...]:
        with connect_database(self.path) as db:
            rows = db.execute(
                "SELECT * FROM knowledge_observations "
                "WHERE status='pending' AND origin_class='human_chat' "
                "ORDER BY recorded_at,observation_id"
            ).fetchall()
        return tuple(self._stored_observation(row) for row in rows)

    def observation_status_for_event(self, event_id: str) -> str | None:
        source_event_id = _required_text(event_id, "event_id")
        with connect_database(self.path) as db:
            row = db.execute(
                "SELECT status FROM knowledge_observations "
                "WHERE source_event_id=? ORDER BY recorded_at DESC LIMIT 1",
                (source_event_id,),
            ).fetchone()
        return None if row is None else str(row["status"])

    def set_observation_status(
        self, observation_id: str, status: str
    ) -> None:
        identity = _required_text(observation_id, "observation_id")
        normalized_status = _required_text(status, "status", maximum=24)
        if normalized_status not in {
            "pending", "admitted", "rejected", "expired"
        }:
            raise ValueError("unsupported observation status")
        with connect_database(self.path) as db:
            cursor = db.execute(
                "UPDATE knowledge_observations SET status=? "
                "WHERE observation_id=?",
                (normalized_status, identity),
            )
            if cursor.rowcount != 1:
                raise ValueError("knowledge observation does not exist")

    def convention(
        self, group_id: str, expression: str
    ) -> GroupConventionRecord | None:
        values = self.conventions_for_expression(group_id, expression)
        if not values:
            return None
        return sorted(
            values,
            key=lambda item: (
                {
                    "active": 0,
                    "candidate": 1,
                    "disputed": 2,
                    "stale": 3,
                }.get(item.status, 4),
                -item.updated_at,
                item.convention_id,
            ),
        )[0]

    def conventions_for_expression(
        self, group_id: str, expression: str
    ) -> tuple[GroupConventionRecord, ...]:
        scope = _required_text(group_id, "group_id")
        normalized = _expression(expression)
        with connect_database(self.path) as db:
            rows = db.execute(
                "SELECT * FROM group_conventions "
                "WHERE group_id=? AND normalized_expression=? "
                "ORDER BY convention_id",
                (scope, normalized),
            ).fetchall()
        return tuple(self._convention_record(row) for row in rows)

    def convention_expressions(self, group_id: str) -> tuple[str, ...]:
        scope = _required_text(group_id, "group_id")
        with connect_database(self.path) as db:
            rows = db.execute(
                "SELECT DISTINCT normalized_expression FROM group_conventions "
                "WHERE group_id=? AND status IN ('candidate','active') "
                "ORDER BY LENGTH(normalized_expression) DESC,normalized_expression",
                (scope,),
            ).fetchall()
        return tuple(str(row[0]) for row in rows)

    def game_alias_matches(self, text: str) -> tuple[tuple[str, str], ...]:
        normalized_text = unicodedata.normalize(
            "NFKC", str(text or "")
        ).casefold()
        if not normalized_text:
            return ()
        with connect_database(self.path) as db:
            rows = db.execute(
                "SELECT a.entity_id,a.normalized_alias "
                "FROM knowledge_aliases AS a "
                "JOIN knowledge_entities AS e ON e.entity_id=a.entity_id "
                "WHERE a.status='active' AND a.ambiguity_level='none' "
                "AND e.entity_type='game' AND e.status='active' "
                "ORDER BY LENGTH(a.normalized_alias) DESC,a.alias_id"
            ).fetchall()
        result = []
        for row in rows:
            alias = str(row["normalized_alias"])
            value = (str(row["entity_id"]), alias)
            if alias in normalized_text and value not in result:
                result.append(value)
        return tuple(result)

    def active_global_game_aliases(self) -> tuple[KnowledgeAliasRecord, ...]:
        with connect_database(self.path) as db:
            rows = db.execute(
                "SELECT a.* FROM knowledge_aliases AS a "
                "JOIN knowledge_entities AS e ON e.entity_id=a.entity_id "
                "WHERE a.status='active' AND e.entity_type='game' "
                "AND e.status='active' AND a.alias_id LIKE 'alias:learned:%' "
                "ORDER BY LENGTH(a.normalized_alias) DESC,"
                "a.alias_id"
            ).fetchall()
        return tuple(
            KnowledgeAliasRecord(
                alias_id=str(row["alias_id"]),
                scope_kind="global",
                group_id=None,
                entity_id=str(row["entity_id"]),
                normalized_alias=str(row["normalized_alias"]),
                alias_kind=str(row["alias_kind"]),
                ambiguity_level=str(row["ambiguity_level"]),
                confidence=1.0,
                status=str(row["status"]),
            )
            for row in rows
        )

    def active_seed_manifests(self) -> tuple[Mapping[str, Any], ...]:
        with connect_database(self.path) as db:
            rows = db.execute(
                "SELECT manifest_json FROM knowledge_seeds "
                "WHERE status='active' ORDER BY seed_id,seed_version"
            ).fetchall()
        return tuple(json.loads(str(row[0])) for row in rows)

    def current_official_source_registry(
        self, declarations: Iterable[Mapping[str, Any]]
    ) -> tuple[Mapping[str, Any], ...]:
        """Resolve seed-declared source identities to their current registry rows."""

        declared = tuple(dict(item) for item in declarations)
        source_ids = tuple(
            _required_text(item.get("source_id"), "source_id")
            for item in declared
        )
        if not source_ids:
            return ()
        placeholders = ",".join("?" for _ in source_ids)
        with connect_database(self.path) as db:
            rows = db.execute(
                "SELECT source_id,canonical_url,domain,publisher FROM knowledge_sources "
                "WHERE source_id IN ({}) AND source_class='official'".format(
                    placeholders
                ),
                source_ids,
            ).fetchall()
        current = {str(row["source_id"]): row for row in rows}
        return tuple(
            {
                "source_id": source_id,
                "publisher": (
                    str(current[source_id]["publisher"])
                    if source_id in current
                    else str(item["publisher"])
                ),
                "domain": (
                    str(current[source_id]["domain"])
                    if source_id in current
                    else str(item["domain"])
                ),
                "url": (
                    str(current[source_id]["canonical_url"])
                    if source_id in current
                    else str(item["url"])
                ),
                "required": bool(item["required"]),
            }
            for source_id, item in zip(source_ids, declared)
        )

    def enqueue_knowledge_job(
        self,
        *,
        idempotency_key: str,
        job_kind: str,
        group_id: str | None = None,
        entity_id: str | None,
        request: Mapping[str, Any],
        next_attempt_at: int,
        now: int,
    ) -> KnowledgeJobRecord:
        key = _required_text(idempotency_key, "idempotency_key", maximum=512)
        kind = _required_text(job_kind, "job_kind", maximum=64)
        if kind not in {
            "seed_import", "official_daily_probe", "instant_enrichment",
            "unknown_entity_learning", "group_topic_warmup",
            "time_boundary_revalidation", "correction_rebuild",
            "long_tail_official_refresh",
        }:
            raise ValueError("unsupported knowledge job kind")
        scope = (
            None
            if group_id is None
            else _required_text(group_id, "group_id")
        )
        if (
            kind in {"unknown_entity_learning", "group_topic_warmup"}
            and scope is None
        ):
            raise ValueError("group-scoped learning job requires group_id")
        identity = None if entity_id is None else _required_text(entity_id, "entity_id")
        timestamp = int(now)
        due_at = int(next_attempt_at)
        if timestamp < 0 or due_at < 0:
            raise ValueError("knowledge job timestamps must not be negative")
        payload = json.dumps(
            dict(request), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        job_id = "job:" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        with connect_database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT OR IGNORE INTO knowledge_jobs(job_id,idempotency_key,"
                "job_kind,group_id,entity_id,request_json,status,attempt,"
                "next_attempt_at,diagnostic_code,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (job_id, key, kind, scope, identity, payload, "pending", 0,
                 due_at, None, timestamp, timestamp),
            )
            row = db.execute(
                "SELECT * FROM knowledge_jobs WHERE idempotency_key=?", (key,)
            ).fetchone()
        assert row is not None
        return self._knowledge_job(row)

    def acquire_enrichment_job(
        self,
        *,
        request_hash: str,
        entity_id: str,
        query_intents: Iterable[str],
        now: int,
    ) -> KnowledgeJobRecord:
        """Claim one stable knowledge-only job for an instant request.

        Scene, target, lease, intention and reply data are deliberately absent
        from the durable payload.  A recovered job can therefore only recover
        evidence; the current caller must supply and revalidate its own guard.
        """

        digest = _required_text(request_hash, "request_hash", maximum=64)
        if len(digest) != 64 or any(item not in "0123456789abcdef" for item in digest):
            raise ValueError("request_hash must be a lowercase SHA-256 digest")
        identity = _required_text(entity_id, "entity_id")
        intents = tuple(
            dict.fromkeys(
                _required_text(value, "query_intent", maximum=64)
                for value in query_intents
            )
        )
        if not intents or len(intents) > 2:
            raise ValueError("query_intents must contain 1-2 values")
        timestamp = int(now)
        if timestamp < 0:
            raise ValueError("now must not be negative")
        with connect_database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT * FROM knowledge_jobs WHERE job_kind='instant_enrichment' "
                "AND status IN ('pending','running','retry') "
                "ORDER BY created_at DESC,job_id DESC"
            ).fetchall()
            for row in rows:
                try:
                    payload = json.loads(str(row["request_json"]))
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(payload, dict) or payload.get("request_hash") != digest:
                    continue
                if str(row["status"]) in {"pending", "retry"}:
                    db.execute(
                        "UPDATE knowledge_jobs SET status='running',"
                        "attempt=attempt+1,diagnostic_code=NULL,updated_at=? "
                        "WHERE job_id=? AND status IN ('pending','retry')",
                        (timestamp, row["job_id"]),
                    )
                claimed = db.execute(
                    "SELECT * FROM knowledge_jobs WHERE job_id=?", (row["job_id"],)
                ).fetchone()
                assert claimed is not None
                return self._knowledge_job(claimed)

            payload = json.dumps(
                {
                    "request_hash": digest,
                    "query_intents": list(intents),
                    "official_complete": False,
                    "discovery_complete": False,
                    "staged_evidence": [],
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            prefix = f"instant_enrichment:{digest}:"
            generation = int(
                db.execute(
                    "SELECT COUNT(*) FROM knowledge_jobs WHERE "
                    "idempotency_key LIKE ?",
                    (prefix + "%",),
                ).fetchone()[0]
            )
            key = f"{prefix}{timestamp}:{generation}"
            job_id = "job:" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
            db.execute(
                "INSERT INTO knowledge_jobs(job_id,idempotency_key,job_kind,"
                "group_id,entity_id,request_json,status,attempt,next_attempt_at,"
                "diagnostic_code,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    key,
                    "instant_enrichment",
                    None,
                    identity,
                    payload,
                    "running",
                    1,
                    _ENRICHMENT_JOB_DUE_AT,
                    None,
                    timestamp,
                    timestamp,
                ),
            )
            row = db.execute(
                "SELECT * FROM knowledge_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
        assert row is not None
        return self._knowledge_job(row)

    def stage_enrichment_job(
        self,
        job_id: str,
        *,
        evidence: Iterable[SourceEvidence],
        official_complete: bool,
        discovery_complete: bool,
        diagnostic_code: str | None,
        now: int,
    ) -> KnowledgeJobRecord:
        identity = _required_text(job_id, "job_id")
        if type(official_complete) is not bool or type(discovery_complete) is not bool:
            raise ValueError("completion flags must be booleans")
        items = tuple(evidence)
        if len(items) > 40 or any(not isinstance(item, SourceEvidence) for item in items):
            raise ValueError("staged evidence is invalid")
        diagnostic = (
            None
            if diagnostic_code is None
            else _required_text(diagnostic_code, "diagnostic_code", maximum=64)
        )
        timestamp = int(now)
        with connect_database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM knowledge_jobs WHERE job_id=? "
                "AND job_kind='instant_enrichment' AND status='running'",
                (identity,),
            ).fetchone()
            if row is None:
                raise ValueError("enrichment job is not running")
            payload = dict(self._knowledge_job(row).request)
            payload.update(
                {
                    "official_complete": official_complete,
                    "discovery_complete": discovery_complete,
                    "staged_evidence": [self._source_payload(item) for item in items],
                }
            )
            db.execute(
                "UPDATE knowledge_jobs SET request_json=?,diagnostic_code=?,"
                "updated_at=? WHERE job_id=? AND status='running'",
                (
                    json.dumps(
                        payload,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    diagnostic,
                    timestamp,
                    identity,
                ),
            )
            staged = db.execute(
                "SELECT * FROM knowledge_jobs WHERE job_id=?", (identity,)
            ).fetchone()
        assert staged is not None
        return self._knowledge_job(staged)

    def recover_enrichment_jobs(self, now: int, *, stale_before: int) -> int:
        timestamp = int(now)
        boundary = int(stale_before)
        with connect_database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                "UPDATE knowledge_jobs SET status='retry',"
                "diagnostic_code='knowledge_job_recovered',updated_at=? "
                "WHERE job_kind='instant_enrichment' AND status='running' "
                "AND updated_at<?",
                (timestamp, boundary),
            )
            return int(cursor.rowcount)

    def complete_enrichment_job(self, job_id: str, *, now: int) -> None:
        self.complete_knowledge_job(job_id, now)

    def knowledge_jobs(self) -> tuple[KnowledgeJobRecord, ...]:
        with connect_database(self.path) as db:
            rows = db.execute(
                "SELECT * FROM knowledge_jobs ORDER BY created_at,job_id"
            ).fetchall()
        return tuple(self._knowledge_job(row) for row in rows)

    def claim_due_knowledge_job(self, now: int) -> KnowledgeJobRecord | None:
        timestamp = int(now)
        with connect_database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM knowledge_jobs WHERE status IN ('pending','retry') "
                "AND next_attempt_at<=? ORDER BY next_attempt_at,created_at,job_id "
                "LIMIT 1",
                (timestamp,),
            ).fetchone()
            if row is None:
                return None
            db.execute(
                "UPDATE knowledge_jobs SET status='running',attempt=attempt+1,"
                "updated_at=? WHERE job_id=?",
                (timestamp, row["job_id"]),
            )
            claimed = db.execute(
                "SELECT * FROM knowledge_jobs WHERE job_id=?", (row["job_id"],)
            ).fetchone()
        assert claimed is not None
        return self._knowledge_job(claimed)

    def recover_running_knowledge_jobs(self, now: int) -> int:
        timestamp = int(now)
        with connect_database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                "UPDATE knowledge_jobs SET status='retry',"
                "diagnostic_code='knowledge_job_recovered',updated_at=? "
                "WHERE status='running'",
                (timestamp,),
            )
            return int(cursor.rowcount)

    def complete_knowledge_job(self, job_id: str, now: int) -> None:
        identity = _required_text(job_id, "job_id")
        timestamp = int(now)
        with connect_database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                "UPDATE knowledge_jobs SET status='completed',diagnostic_code=NULL,"
                "updated_at=? WHERE job_id=? AND status='running'",
                (timestamp, identity),
            )
            if cursor.rowcount != 1:
                raise ValueError("knowledge job is not running")

    def retry_knowledge_job(
        self, job_id: str, *, next_attempt_at: int, diagnostic_code: str, now: int
    ) -> None:
        identity = _required_text(job_id, "job_id")
        diagnostic = _required_text(diagnostic_code, "diagnostic_code", maximum=64)
        timestamp = int(now)
        due_at = int(next_attempt_at)
        with connect_database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                "UPDATE knowledge_jobs SET status='retry',next_attempt_at=?,"
                "diagnostic_code=?,updated_at=? WHERE job_id=? AND status='running'",
                (due_at, diagnostic, timestamp, identity),
            )
            if cursor.rowcount != 1:
                raise ValueError("knowledge job is not running")

    def latest_completed_knowledge_job_at(
        self, *, job_kind: str, entity_id: str, region: str, platform: str
    ) -> int | None:
        kind = _required_text(job_kind, "job_kind", maximum=64)
        identity = _required_text(entity_id, "entity_id")
        normalized_region = _required_text(region, "region", maximum=48)
        normalized_platform = _required_text(platform, "platform", maximum=48)
        with connect_database(self.path) as db:
            rows = db.execute(
                "SELECT request_json,updated_at FROM knowledge_jobs "
                "WHERE job_kind=? AND entity_id=? AND status='completed' "
                "ORDER BY updated_at DESC,job_id DESC",
                (kind, identity),
            ).fetchall()
        for row in rows:
            try:
                request = json.loads(str(row["request_json"]))
            except json.JSONDecodeError:
                continue
            if (
                request.get("region") == normalized_region
                and request.get("platform") == normalized_platform
            ):
                return int(row["updated_at"])
        return None

    def has_outstanding_daily_refresh(
        self, *, entity_id: str, region: str, platform: str
    ) -> bool:
        identity = _required_text(entity_id, "entity_id")
        normalized_region = _required_text(region, "region", maximum=48)
        normalized_platform = _required_text(platform, "platform", maximum=48)
        with connect_database(self.path) as db:
            rows = db.execute(
                "SELECT request_json FROM knowledge_jobs WHERE "
                "job_kind='official_daily_probe' AND entity_id=? AND "
                "status IN ('pending','running','retry')",
                (identity,),
            ).fetchall()
        for row in rows:
            try:
                request = json.loads(str(row["request_json"]))
            except json.JSONDecodeError:
                continue
            if (
                request.get("region") == normalized_region
                and request.get("platform") == normalized_platform
            ):
                return True
        return False

    def complete_official_probe_job(
        self,
        job_id: str,
        *,
        evidence: Iterable[SourceEvidence],
        observed_at: int,
    ) -> None:
        """Commit a successful official probe as one SQLite transaction.

        The release service derives every slot transition from one aggregate
        snapshot.  This method only persists those service-authorized results.
        """

        from .release_state import GameReleaseStateService, ReleaseEvidence

        identity = _required_text(job_id, "job_id")
        checked_at = int(observed_at)
        bounded_evidence = tuple(evidence)
        if checked_at < 0 or not all(
            isinstance(item, SourceEvidence) for item in bounded_evidence
        ):
            raise ValueError("official probe completion is invalid")
        with connect_database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM knowledge_jobs WHERE job_id=? AND status='running'",
                (identity,),
            ).fetchone()
            if row is None:
                raise ValueError("knowledge job is not running")
            job = self._knowledge_job(row)
            request = job.request
            game_id = _required_text(request.get("game_entity_id"), "game_entity_id")
            region = _required_text(request.get("region"), "region", maximum=48)
            platform = _required_text(
                request.get("platform"), "platform", maximum=48
            )
            for item in bounded_evidence:
                self._upsert_source_in_transaction(db, item)
            slots = self._load_release_slots_in_transaction(
                db, game_id, region, platform
            )
            service = GameReleaseStateService(slots)
            transitions = []
            for slot in slots:
                if slot.official_state == "none":
                    continue
                transition = service.apply_evidence(
                    slot,
                    ReleaseEvidence.create(
                        evidence_id="job:{}".format(job.job_id),
                        track="official",
                        target_state=slot.official_state,
                        observed_at=checked_at,
                        official_label=slot.official_label,
                    ),
                )
                if transition.accepted and transition.state != slot:
                    transitions.append(transition)
            if transitions:
                aggregate_revision = self._release_revision(
                    db, game_id, region, platform
                )
                if any(
                    transition.old_revision != aggregate_revision
                    for transition in transitions
                ):
                    raise ReleaseStateConflict("release aggregate is inconsistent")
                db.execute(
                    "UPDATE game_release_states SET revision=? WHERE "
                    "game_entity_id=? AND region=? AND platform=?",
                    (aggregate_revision + 1, game_id, region, platform),
                )
                for transition in transitions:
                    self._write_release_state_in_transaction(db, transition.state)
                self._invalidate_negative_snapshots(
                    db, game_id, "release_state_changed"
                )
            db.execute(
                "UPDATE knowledge_jobs SET status='completed',diagnostic_code=NULL,"
                "updated_at=? WHERE job_id=? AND status='running'",
                (checked_at, identity),
            )

    def _load_release_slots_in_transaction(
        self, db, game_id: str, region: str, platform: str
    ) -> tuple[VersionSlot, ...]:
        rows = db.execute(
            "SELECT * FROM game_release_states WHERE game_entity_id=? "
            "AND region=? AND platform=? ORDER BY CASE release_state "
            "WHEN 'current' THEN 0 WHEN 'future' THEN 1 ELSE 2 END,"
            "version_slot_id",
            (game_id, region, platform),
        ).fetchall()
        return tuple(self._version_slot(row) for row in rows)

    @staticmethod
    def _write_release_state_in_transaction(db, state: VersionSlot) -> None:
        db.execute(
            "INSERT INTO game_release_states(version_slot_id,game_entity_id,"
            "official_label,region,platform,release_state,official_state,"
            "rumor_state,announced_at,release_at,effective_until,"
            "release_checked_at,official_checked_at,rumor_checked_at,"
            "fresh_until,status,revision) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(version_slot_id) DO UPDATE SET "
            "game_entity_id=excluded.game_entity_id,"
            "official_label=excluded.official_label,region=excluded.region,"
            "platform=excluded.platform,release_state=excluded.release_state,"
            "official_state=excluded.official_state,rumor_state=excluded.rumor_state,"
            "announced_at=excluded.announced_at,release_at=excluded.release_at,"
            "effective_until=excluded.effective_until,"
            "release_checked_at=excluded.release_checked_at,"
            "official_checked_at=excluded.official_checked_at,"
            "rumor_checked_at=excluded.rumor_checked_at,"
            "fresh_until=excluded.fresh_until,status=excluded.status,"
            "revision=excluded.revision",
            (
                state.version_slot_id,
                state.game_entity_id,
                state.official_label,
                state.region,
                state.platform,
                state.release_state,
                state.official_state,
                state.rumor_state,
                state.announced_at,
                state.release_at,
                state.effective_until,
                state.release_checked_at,
                state.official_checked_at,
                state.rumor_checked_at,
                state.fresh_until,
                state.status,
                state.revision,
            ),
        )

    def active_group_aliases(
        self, group_id: str
    ) -> tuple[KnowledgeAliasRecord, ...]:
        scope = _required_text(group_id, "group_id")
        with connect_database(self.path) as db:
            rows = db.execute(
                "SELECT * FROM group_knowledge_aliases "
                "WHERE group_id=? AND status='active' "
                "ORDER BY LENGTH(normalized_alias) DESC,confidence DESC,alias_id",
                (scope,),
            ).fetchall()
        return tuple(
            KnowledgeAliasRecord(
                alias_id=str(row["alias_id"]),
                scope_kind="group",
                group_id=scope,
                entity_id=str(row["entity_id"]),
                normalized_alias=str(row["normalized_alias"]),
                alias_kind="community",
                ambiguity_level="contextual",
                confidence=float(row["confidence"]),
                status=str(row["status"]),
            )
            for row in rows
        )

    def entities(
        self, entity_ids: Iterable[str]
    ) -> tuple[KnowledgeEntityRecord, ...]:
        identities = tuple(
            dict.fromkeys(_required_text(value, "entity_id") for value in entity_ids)
        )
        if not identities:
            return ()
        placeholders = ",".join("?" for _ in identities)
        with connect_database(self.path) as db:
            rows = db.execute(
                "SELECT * FROM knowledge_entities WHERE entity_id IN ({}) "
                "ORDER BY entity_id".format(placeholders),
                identities,
            ).fetchall()
        return tuple(
            KnowledgeEntityRecord(
                entity_id=str(row["entity_id"]),
                entity_type=str(row["entity_type"]),
                canonical_name=str(row["canonical_name"]),
                canonical_game_id=str(row["canonical_game_id"]),
                status=str(row["status"]),
            )
            for row in rows
        )

    def active_claims(
        self, entity_ids: Iterable[str], *, limit: int
    ) -> tuple[KnowledgeClaimRecord, ...]:
        identities = tuple(
            dict.fromkeys(_required_text(value, "entity_id") for value in entity_ids)
        )
        maximum = max(1, min(32, int(limit)))
        if not identities:
            return ()
        placeholders = ",".join("?" for _ in identities)
        with connect_database(self.path) as db:
            rows = db.execute(
                "SELECT * FROM knowledge_claims "
                "WHERE subject_entity_id IN ({}) AND status='active' "
                "ORDER BY CASE evidence_level "
                "WHEN 'official' THEN 0 WHEN 'bundled' THEN 1 "
                "WHEN 'corroborated' THEN 2 WHEN 'secondary' THEN 3 ELSE 4 END,"
                "claim_id LIMIT ?".format(placeholders),
                (*identities, maximum),
            ).fetchall()
        return tuple(
            KnowledgeClaimRecord(
                claim_id=str(row["claim_id"]),
                subject_entity_id=str(row["subject_entity_id"]),
                predicate=str(row["predicate"]),
                safe_summary=str(row["safe_summary"]),
                claim_kind=str(row["claim_kind"]),
                evidence_level=str(row["evidence_level"]),
                status=str(row["status"]),
                checked_at=(
                    None
                    if row["checked_at"] is None
                    else int(row["checked_at"])
                ),
            )
            for row in rows
        )

    def upsert_source(self, evidence: SourceEvidence) -> str:
        if not isinstance(evidence, SourceEvidence):
            raise TypeError("evidence must be SourceEvidence")
        with connect_database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            return self._upsert_source_in_transaction(db, evidence)

    def commit_enrichment_evidence(
        self,
        *,
        game_entity_id: str,
        evidence: Iterable[SourceEvidence],
    ) -> tuple[tuple[str, ...], tuple[str, ...], bool]:
        """Idempotently admit bounded source evidence in one short transaction."""

        game_id = _required_text(game_entity_id, "game_entity_id")
        items = tuple(evidence)
        if len(items) > 40 or any(not isinstance(item, SourceEvidence) for item in items):
            raise ValueError("enrichment evidence is invalid")
        source_ids: list[str] = []
        source_domains: list[str] = []
        official_changed = False
        with connect_database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            for item in items:
                prior = db.execute(
                    "SELECT content_hash,source_class FROM knowledge_sources "
                    "WHERE canonical_url=?",
                    (item.canonical_url,),
                ).fetchone()
                if item.source_class.value == "official" and (
                    prior is None
                    or str(prior["content_hash"]) != item.content_hash
                    or str(prior["source_class"]) != "official"
                ):
                    duplicate = db.execute(
                        "SELECT 1 FROM knowledge_sources WHERE domain=? AND publisher=? "
                        "AND source_class='official' AND content_hash=? LIMIT 1",
                        (item.domain, item.publisher, item.content_hash),
                    ).fetchone()
                    official_changed = official_changed or duplicate is None
                source_id = self._upsert_source_in_transaction(db, item)
                if source_id not in source_ids:
                    source_ids.append(source_id)
                if item.domain not in source_domains:
                    source_domains.append(item.domain)
            if official_changed:
                self._invalidate_negative_snapshots(
                    db, game_id, "new_official_evidence"
                )
        return tuple(source_ids), tuple(source_domains), official_changed

    @staticmethod
    def _upsert_source_in_transaction(db, evidence: SourceEvidence) -> str:
        row = db.execute(
            "SELECT * FROM knowledge_sources WHERE canonical_url=?",
            (evidence.canonical_url,),
        ).fetchone()
        if row is None:
            row = db.execute(
                "SELECT * FROM knowledge_sources WHERE domain=? "
                "AND publisher=? AND content_hash=? "
                "ORDER BY fetched_at DESC,source_id LIMIT 1",
                (
                    evidence.domain,
                    evidence.publisher,
                    evidence.content_hash,
                ),
            ).fetchone()
        if row is None:
            identity_row = db.execute(
                "SELECT canonical_url FROM knowledge_sources WHERE source_id=?",
                (evidence.source_id,),
            ).fetchone()
            if identity_row is not None:
                raise ValueError(
                    "source_id already belongs to another canonical URL"
                )
            db.execute(
                "INSERT INTO knowledge_sources(source_id,canonical_url,"
                "domain,publisher,source_class,published_at,fetched_at,"
                "content_hash,evidence_excerpt) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    evidence.source_id,
                    evidence.canonical_url,
                    evidence.domain,
                    evidence.publisher,
                    evidence.source_class.value,
                    evidence.published_at,
                    evidence.fetched_at,
                    evidence.content_hash,
                    evidence.evidence_excerpt,
                ),
            )
            return evidence.source_id
        source_id = str(row["source_id"])
        if evidence.fetched_at >= int(row["fetched_at"]):
            db.execute(
                "UPDATE knowledge_sources SET "
                "published_at=COALESCE(?,published_at),fetched_at=?,"
                "content_hash=?,evidence_excerpt=? WHERE source_id=?",
                (
                    evidence.published_at,
                    evidence.fetched_at,
                    evidence.content_hash,
                    evidence.evidence_excerpt,
                    source_id,
                ),
            )
        return source_id

    @staticmethod
    def _source_payload(evidence: SourceEvidence) -> dict[str, object]:
        return {
            "evidence_id": evidence.evidence_id,
            "source_id": evidence.source_id,
            "canonical_url": evidence.canonical_url,
            "domain": evidence.domain,
            "publisher": evidence.publisher,
            "source_class": evidence.source_class.value,
            "title": evidence.title,
            "published_at": evidence.published_at,
            "fetched_at": evidence.fetched_at,
            "evidence_excerpt": evidence.evidence_excerpt,
            "content_hash": evidence.content_hash,
        }

    def reserve_provider_quota(
        self,
        *,
        intent_hash: str,
        now: int,
        hourly_limit: int = 20,
        daily_limit: int = 100,
    ) -> str | None:
        digest = _required_text(intent_hash, "intent_hash", maximum=64)
        if len(digest) != 64 or any(item not in "0123456789abcdef" for item in digest):
            raise ValueError("intent_hash must be a lowercase SHA-256 digest")
        timestamp = int(now)
        hour_limit = int(hourly_limit)
        day_limit = int(daily_limit)
        if timestamp < 0 or hour_limit < 1 or day_limit < 1:
            raise ValueError("provider quota settings are invalid")
        local = datetime.fromtimestamp(timestamp, tz=_SHANGHAI)
        hour_start = int(
            local.replace(minute=0, second=0, microsecond=0).timestamp()
        )
        day_start = int(
            local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        )
        reservation_id = f"usage:quota:{uuid.uuid4().hex}"
        with connect_database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            hour_count = int(
                db.execute(
                    "SELECT COUNT(*) FROM knowledge_usage WHERE recorded_at>=? "
                    "AND recorded_at<? AND result_kind LIKE 'provider_%'",
                    (hour_start, hour_start + 3600),
                ).fetchone()[0]
            )
            day_count = int(
                db.execute(
                    "SELECT COUNT(*) FROM knowledge_usage WHERE recorded_at>=? "
                    "AND recorded_at<? AND result_kind LIKE 'provider_%'",
                    (day_start, day_start + 86400),
                ).fetchone()[0]
            )
            if hour_count >= hour_limit or day_count >= day_limit:
                return None
            db.execute(
                "INSERT INTO knowledge_usage(usage_id,group_id,source_event_id,"
                "knowledge_ids_json,source_domains_json,query_intent_hash,"
                "latency_ms,cache_hit,result_kind,diagnostic_code,recorded_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    reservation_id,
                    None,
                    None,
                    "[]",
                    "[]",
                    digest,
                    0,
                    0,
                    "provider_reserved",
                    "provider_reserved",
                    timestamp,
                ),
            )
        return reservation_id

    def release_provider_quota(self, reservation_id: str) -> bool:
        identity = _required_text(reservation_id, "reservation_id")
        with connect_database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                "DELETE FROM knowledge_usage WHERE usage_id=? "
                "AND result_kind='provider_reserved'",
                (identity,),
            )
            return cursor.rowcount == 1

    def finish_provider_quota(
        self,
        reservation_id: str,
        *,
        source_domains: Iterable[str],
        latency_ms: int,
        result_kind: str,
        diagnostic_code: str | None,
    ) -> None:
        identity = _required_text(reservation_id, "reservation_id")
        kind = _required_text(result_kind, "result_kind", maximum=64)
        if not kind.startswith("provider_") or kind == "provider_reserved":
            raise ValueError("provider result kind is invalid")
        domains = tuple(
            dict.fromkeys(
                _required_text(value, "source_domain", maximum=253).casefold()
                for value in source_domains
            )
        )
        latency = max(0, int(latency_ms))
        diagnostic = (
            None
            if diagnostic_code is None
            else _required_text(diagnostic_code, "diagnostic_code", maximum=64)
        )
        with connect_database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                "UPDATE knowledge_usage SET source_domains_json=?,latency_ms=?,"
                "result_kind=?,diagnostic_code=? WHERE usage_id=? "
                "AND result_kind='provider_reserved'",
                (_json(domains), latency, kind, diagnostic, identity),
            )
            if cursor.rowcount != 1:
                raise ValueError("provider quota reservation is not active")

    def record_enrichment_usage(
        self,
        *,
        intent_hash: str,
        source_domains: Iterable[str],
        latency_ms: int,
        cache_hit: bool,
        result_kind: str,
        diagnostic_code: str | None,
        now: int,
    ) -> str:
        digest = _required_text(intent_hash, "intent_hash", maximum=64)
        domains = tuple(
            dict.fromkeys(
                _required_text(value, "source_domain", maximum=253).casefold()
                for value in source_domains
            )
        )
        if type(cache_hit) is not bool:
            raise ValueError("cache_hit must be a boolean")
        kind = _required_text(result_kind, "result_kind", maximum=64)
        diagnostic = (
            None
            if diagnostic_code is None
            else _required_text(diagnostic_code, "diagnostic_code", maximum=64)
        )
        timestamp = int(now)
        usage_id = f"usage:enrichment:{uuid.uuid4().hex}"
        with connect_database(self.path) as db:
            db.execute(
                "INSERT INTO knowledge_usage(usage_id,group_id,source_event_id,"
                "knowledge_ids_json,source_domains_json,query_intent_hash,"
                "latency_ms,cache_hit,result_kind,diagnostic_code,recorded_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    usage_id,
                    None,
                    None,
                    "[]",
                    _json(domains),
                    digest,
                    max(0, int(latency_ms)),
                    int(cache_hit),
                    kind,
                    diagnostic,
                    timestamp,
                ),
            )
        return usage_id

    def successful_enrichment_cache(
        self, intent_hash: str, *, now: int, ttl_seconds: int = 600
    ) -> EnrichmentCacheRecord | None:
        digest = _required_text(intent_hash, "intent_hash", maximum=64)
        timestamp = int(now)
        ttl = int(ttl_seconds)
        if ttl < 1:
            raise ValueError("cache TTL must be positive")
        with connect_database(self.path) as db:
            row = db.execute(
                "SELECT source_domains_json,recorded_at FROM knowledge_usage "
                "WHERE query_intent_hash=? AND result_kind='enrichment_success' "
                "AND recorded_at>? ORDER BY recorded_at DESC,usage_id DESC LIMIT 1",
                (digest, timestamp - ttl),
            ).fetchone()
        if row is None:
            return None
        values = json.loads(str(row["source_domains_json"]))
        return EnrichmentCacheRecord(
            intent_hash=digest,
            source_domains=tuple(str(value) for value in values),
            recorded_at=int(row["recorded_at"]),
        )

    def admit_claim(
        self,
        candidate: KnowledgeClaimCandidate,
        evidence_ids: Iterable[str],
    ) -> ClaimWriteResult:
        if not isinstance(candidate, KnowledgeClaimCandidate):
            raise TypeError("candidate must be KnowledgeClaimCandidate")
        source_ids = tuple(
            dict.fromkeys(
                _required_text(value, "evidence_id")
                for value in evidence_ids
            )
        )
        if not source_ids:
            raise ValueError("evidence_ids must not be empty")

        with connect_database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            existing_identity = db.execute(
                "SELECT * FROM knowledge_claims WHERE claim_id=?",
                (candidate.candidate_id,),
            ).fetchone()
            if existing_identity is not None:
                linked = db.execute(
                    "SELECT source_id FROM knowledge_claim_evidence "
                    "WHERE claim_id=? AND relation_kind='supports' "
                    "ORDER BY source_id",
                    (candidate.candidate_id,),
                ).fetchall()
                linked_ids = tuple(str(row[0]) for row in linked)
                expected_values = (
                    candidate.subject_entity_id,
                    candidate.predicate,
                    candidate.safe_summary,
                    candidate.claim_kind.value,
                    candidate.evidence_level.value,
                    candidate.applies_to_version_slot_id,
                    candidate.region,
                    candidate.platform,
                    candidate.valid_from,
                    candidate.valid_until,
                    candidate.checked_at,
                )
                stored_values = tuple(
                    existing_identity[name]
                    for name in (
                        "subject_entity_id",
                        "predicate",
                        "safe_summary",
                        "claim_kind",
                        "evidence_level",
                        "applies_to_version_slot_id",
                        "region",
                        "platform",
                        "valid_from",
                        "valid_until",
                        "checked_at",
                    )
                )
                if stored_values != expected_values or set(linked_ids) != set(
                    source_ids
                ):
                    raise ValueError(
                        "claim identity already has different content"
                    )
                return ClaimWriteResult(
                    outcome="unchanged",
                    claim_id=candidate.candidate_id,
                    superseded_claim_ids=(),
                    source_ids=linked_ids,
                )

            placeholders = ",".join("?" for _ in source_ids)
            source_rows = db.execute(
                "SELECT source_id,source_class FROM knowledge_sources "
                "WHERE source_id IN ({})".format(placeholders),
                source_ids,
            ).fetchall()
            found = {str(row["source_id"]) for row in source_rows}
            if found != set(source_ids):
                raise ValueError("source evidence does not exist")
            source_classes = [
                str(row["source_class"]) for row in source_rows
            ]
            evidence_level = candidate.evidence_level.value
            level_supported = {
                "unofficial": True,
                "secondary": any(
                    value in {"secondary", "official"}
                    for value in source_classes
                ),
                "corroborated": sum(
                    value in {"secondary", "official"}
                    for value in source_classes
                )
                >= 2,
                "official": "official" in source_classes,
                # Bundled claims are admitted only by the immutable seed
                # importer, not by fetched source evidence.
                "bundled": False,
            }[evidence_level]
            if not level_supported:
                raise ValueError(
                    "source evidence does not support evidence level"
                )

            active_rows = db.execute(
                "SELECT * FROM knowledge_claims WHERE subject_entity_id=? "
                "AND predicate=? AND applies_to_version_slot_id IS ? "
                "AND region IS ? AND platform IS ? AND status='active' "
                "ORDER BY COALESCE(checked_at,-1) DESC,claim_id",
                (
                    candidate.subject_entity_id,
                    candidate.predicate,
                    candidate.applies_to_version_slot_id,
                    candidate.region,
                    candidate.platform,
                ),
            ).fetchall()
            comparable = [
                row for row in active_rows if str(row["claim_kind"]) != "rumor"
            ]
            superseded: tuple[str, ...] = ()
            if candidate.claim_kind.value != "rumor" and comparable:
                rank = {
                    "unofficial": 1,
                    "secondary": 2,
                    "corroborated": 3,
                    "bundled": 4,
                    "official": 5,
                }
                incoming_rank = rank[candidate.evidence_level.value]
                dominates = all(
                    incoming_rank > rank[str(row["evidence_level"])]
                    or (
                        incoming_rank == rank[str(row["evidence_level"])]
                        and candidate.checked_at
                        > (
                            -1
                            if row["checked_at"] is None
                            else int(row["checked_at"])
                        )
                    )
                    for row in comparable
                )
                if not dominates:
                    active = comparable[0]
                    return ClaimWriteResult(
                        outcome="unchanged",
                        claim_id=str(active["claim_id"]),
                        superseded_claim_ids=(),
                        source_ids=(),
                    )
                superseded = tuple(str(row["claim_id"]) for row in comparable)
                db.execute(
                    "UPDATE knowledge_claims SET status='superseded',"
                    "updated_at=? WHERE claim_id IN ({})".format(
                        ",".join("?" for _ in superseded)
                    ),
                    (candidate.checked_at, *superseded),
                )

            supersedes_claim_id = superseded[0] if superseded else None
            db.execute(
                "INSERT INTO knowledge_claims(claim_id,subject_entity_id,"
                "predicate,safe_summary,claim_kind,evidence_level,status,"
                "applies_to_version_slot_id,region,platform,valid_from,"
                "valid_until,checked_at,supersedes_claim_id,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    candidate.candidate_id,
                    candidate.subject_entity_id,
                    candidate.predicate,
                    candidate.safe_summary,
                    candidate.claim_kind.value,
                    candidate.evidence_level.value,
                    "active",
                    candidate.applies_to_version_slot_id,
                    candidate.region,
                    candidate.platform,
                    candidate.valid_from,
                    candidate.valid_until,
                    candidate.checked_at,
                    supersedes_claim_id,
                    candidate.checked_at,
                    candidate.checked_at,
                ),
            )
            for source_id in source_ids:
                link_id = "claim-evidence:" + hashlib.sha256(
                    "{}\0{}\0supports".format(
                        candidate.candidate_id, source_id
                    ).encode("utf-8")
                ).hexdigest()[:32]
                db.execute(
                    "INSERT INTO knowledge_claim_evidence(evidence_id,claim_id,"
                    "source_id,observation_id,relation_kind,created_at) "
                    "VALUES(?,?,?,?,?,?)",
                    (
                        link_id,
                        candidate.candidate_id,
                        source_id,
                        None,
                        "supports",
                        candidate.checked_at,
                    ),
                )

            if (
                candidate.evidence_level.value == "official"
                and "official" in source_classes
            ):
                self._invalidate_negative_snapshots(
                    db,
                    candidate.subject_entity_id,
                    "new_official_evidence",
                )
            return ClaimWriteResult(
                outcome="superseded" if superseded else "inserted",
                claim_id=candidate.candidate_id,
                superseded_claim_ids=superseded,
                source_ids=source_ids,
            )

    def load_release_state(
        self, game_id: str, region: str, platform: str
    ) -> tuple[VersionSlot, ...]:
        game = _required_text(game_id, "game_id")
        normalized_region = _required_text(region, "region", maximum=48)
        normalized_platform = _required_text(
            platform, "platform", maximum=48
        )
        with connect_database(self.path) as db:
            rows = db.execute(
                "SELECT * FROM game_release_states WHERE game_entity_id=? "
                "AND region=? AND platform=? "
                "ORDER BY CASE release_state WHEN 'current' THEN 0 "
                "WHEN 'future' THEN 1 ELSE 2 END,version_slot_id",
                (game, normalized_region, normalized_platform),
            ).fetchall()
        return tuple(self._version_slot(row) for row in rows)

    def save_release_state(
        self, state: VersionSlot, expected_revision: int
    ) -> VersionSlot:
        if not isinstance(state, VersionSlot):
            raise TypeError("state must be VersionSlot")
        expected = int(expected_revision)
        if expected < 0:
            raise ValueError("expected_revision must not be negative")
        with connect_database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            existing_slot = db.execute(
                "SELECT * FROM game_release_states WHERE version_slot_id=?",
                (state.version_slot_id,),
            ).fetchone()
            if existing_slot is not None and (
                str(existing_slot["game_entity_id"]) != state.game_entity_id
                or str(existing_slot["region"]) != state.region
                or str(existing_slot["platform"]) != state.platform
            ):
                raise ValueError(
                    "version_slot_id already belongs to another release aggregate"
                )
            current = self._release_revision(
                db, state.game_entity_id, state.region, state.platform
            )
            if current != expected:
                raise ReleaseStateConflict(
                    "release state revision changed from {} to {}".format(
                        expected, current
                    )
                )
            if existing_slot is not None:
                field_names = (
                    "game_entity_id",
                    "official_label",
                    "region",
                    "platform",
                    "release_state",
                    "official_state",
                    "rumor_state",
                    "announced_at",
                    "release_at",
                    "effective_until",
                    "release_checked_at",
                    "official_checked_at",
                    "rumor_checked_at",
                    "fresh_until",
                    "status",
                )
                stored = tuple(existing_slot[name] for name in field_names)
                proposed = tuple(getattr(state, name) for name in field_names)
                if stored == proposed:
                    if state.revision not in {current, current + 1}:
                        raise ValueError(
                            "unchanged state has an invalid revision"
                        )
                    return self._version_slot(existing_slot)
            if state.revision != current + 1:
                raise ValueError(
                    "state revision must be exactly expected_revision + 1"
                )
            db.execute(
                "UPDATE game_release_states SET revision=? "
                "WHERE game_entity_id=? AND region=? AND platform=?",
                (
                    state.revision,
                    state.game_entity_id,
                    state.region,
                    state.platform,
                ),
            )
            db.execute(
                "INSERT INTO game_release_states(version_slot_id,game_entity_id,"
                "official_label,region,platform,release_state,official_state,"
                "rumor_state,announced_at,release_at,effective_until,"
                "release_checked_at,official_checked_at,rumor_checked_at,"
                "fresh_until,status,revision) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(version_slot_id) DO UPDATE SET "
                "game_entity_id=excluded.game_entity_id,"
                "official_label=excluded.official_label,region=excluded.region,"
                "platform=excluded.platform,release_state=excluded.release_state,"
                "official_state=excluded.official_state,"
                "rumor_state=excluded.rumor_state,announced_at=excluded.announced_at,"
                "release_at=excluded.release_at,"
                "effective_until=excluded.effective_until,"
                "release_checked_at=excluded.release_checked_at,"
                "official_checked_at=excluded.official_checked_at,"
                "rumor_checked_at=excluded.rumor_checked_at,"
                "fresh_until=excluded.fresh_until,status=excluded.status,"
                "revision=excluded.revision",
                (
                    state.version_slot_id,
                    state.game_entity_id,
                    state.official_label,
                    state.region,
                    state.platform,
                    state.release_state,
                    state.official_state,
                    state.rumor_state,
                    state.announced_at,
                    state.release_at,
                    state.effective_until,
                    state.release_checked_at,
                    state.official_checked_at,
                    state.rumor_checked_at,
                    state.fresh_until,
                    state.status,
                    state.revision,
                ),
            )
            self._invalidate_negative_snapshots(
                db, state.game_entity_id, "release_state_changed"
            )
            row = db.execute(
                "SELECT * FROM game_release_states WHERE version_slot_id=?",
                (state.version_slot_id,),
            ).fetchone()
        return self._version_slot(row)

    def save_negative_snapshot(
        self, snapshot: NegativeSearchSnapshot
    ) -> str:
        if not isinstance(snapshot, NegativeSearchSnapshot):
            raise TypeError("snapshot must be NegativeSearchSnapshot")
        with connect_database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            revision = self._release_revision(
                db,
                snapshot.game_entity_id,
                snapshot.region,
                snapshot.platform,
            )
            if revision != snapshot.version_state_revision:
                raise ReleaseStateConflict(
                    "negative snapshot was checked against a stale revision"
                )
            placeholders = ",".join("?" for _ in snapshot.covered_source_ids)
            covered_count = db.execute(
                "SELECT COUNT(*) FROM knowledge_sources WHERE source_id IN ({})".format(
                    placeholders
                ),
                snapshot.covered_source_ids,
            ).fetchone()[0]
            if int(covered_count) != len(snapshot.covered_source_ids):
                raise ValueError("covered source does not exist")
            required_placeholders = ",".join(
                "?" for _ in snapshot.required_source_ids
            )
            required_rows = db.execute(
                "SELECT source_id,source_class FROM knowledge_sources "
                "WHERE source_id IN ({})".format(required_placeholders),
                snapshot.required_source_ids,
            ).fetchall()
            if (
                {str(row["source_id"]) for row in required_rows}
                != set(snapshot.required_source_ids)
                or any(
                    str(row["source_class"]) != "official"
                    for row in required_rows
                )
            ):
                raise ValueError("negative required sources must be official")
            existing = db.execute(
                "SELECT * FROM negative_search_snapshots "
                "WHERE snapshot_id=?",
                (snapshot.snapshot_id,),
            ).fetchone()
            if existing is not None:
                stored_covered, stored_required = self._negative_source_ids(
                    existing
                )
                stored_values = (
                    str(existing["game_entity_id"]),
                    str(existing["query_intent"]),
                    (
                        None
                        if existing["region"] is None
                        else str(existing["region"])
                    ),
                    (
                        None
                        if existing["platform"] is None
                        else str(existing["platform"])
                    ),
                    stored_covered,
                    stored_required,
                    int(existing["checked_at"]),
                    int(existing["expires_at"]),
                    int(existing["version_state_revision"]),
                )
                proposed_values = (
                    snapshot.game_entity_id,
                    snapshot.query_intent,
                    snapshot.region,
                    snapshot.platform,
                    snapshot.covered_source_ids,
                    snapshot.required_source_ids,
                    snapshot.checked_at,
                    snapshot.expires_at,
                    snapshot.version_state_revision,
                )
                if stored_values != proposed_values:
                    raise ValueError(
                        "snapshot identity already has different content"
                    )
                return snapshot.snapshot_id
            db.execute(
                "UPDATE negative_search_snapshots SET status='invalidated',"
                "diagnostic_code='newer_negative_snapshot' "
                "WHERE game_entity_id=? AND query_intent=? AND region IS ? "
                "AND platform IS ? AND status='active'",
                (
                    snapshot.game_entity_id,
                    snapshot.query_intent,
                    snapshot.region,
                    snapshot.platform,
                ),
            )
            db.execute(
                "INSERT INTO negative_search_snapshots(snapshot_id,"
                "game_entity_id,query_intent,region,platform,"
                "covered_source_ids_json,checked_at,expires_at,"
                "version_state_revision,status,diagnostic_code) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    snapshot.snapshot_id,
                    snapshot.game_entity_id,
                    snapshot.query_intent,
                    snapshot.region,
                    snapshot.platform,
                    json.dumps(
                        {
                            "covered": list(snapshot.covered_source_ids),
                            "required": list(snapshot.required_source_ids),
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    snapshot.checked_at,
                    snapshot.expires_at,
                    snapshot.version_state_revision,
                    snapshot.status,
                    snapshot.diagnostic_code,
                ),
            )
        return snapshot.snapshot_id

    def valid_negative_snapshot(
        self, key: NegativeSnapshotKey, now: int
    ) -> NegativeSearchSnapshotRecord | None:
        if not isinstance(key, NegativeSnapshotKey):
            raise TypeError("key must be NegativeSnapshotKey")
        timestamp = int(now)
        if timestamp < 0:
            raise ValueError("now must not be negative")
        with connect_database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT * FROM negative_search_snapshots "
                "WHERE game_entity_id=? AND query_intent=? AND region IS ? "
                "AND platform IS ? AND status='active' "
                "ORDER BY checked_at DESC,snapshot_id",
                (
                    key.game_entity_id,
                    key.query_intent,
                    key.region,
                    key.platform,
                ),
            ).fetchall()
            revision = self._release_revision(
                db, key.game_entity_id, key.region, key.platform
            )
            for row in rows:
                if int(row["expires_at"]) <= timestamp:
                    db.execute(
                        "UPDATE negative_search_snapshots SET status='expired',"
                        "diagnostic_code='negative_ttl_expired' "
                        "WHERE snapshot_id=?",
                        (row["snapshot_id"],),
                    )
                    continue
                if int(row["version_state_revision"]) != revision:
                    db.execute(
                        "UPDATE negative_search_snapshots "
                        "SET status='invalidated',"
                        "diagnostic_code='release_revision_changed' "
                        "WHERE snapshot_id=?",
                        (row["snapshot_id"],),
                    )
                    continue
                return self._negative_snapshot_record(row)
        return None

    def invalidate_negative_snapshots(
        self, game_id: str, reason: str
    ) -> int:
        game = _required_text(game_id, "game_id")
        diagnostic = _required_text(reason, "reason", maximum=64)
        with connect_database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            return self._invalidate_negative_snapshots(
                db, game, diagnostic
            )

    @staticmethod
    def _invalidate_negative_snapshots(db, game_id: str, reason: str) -> int:
        cursor = db.execute(
            "UPDATE negative_search_snapshots SET status='invalidated',"
            "diagnostic_code=? WHERE game_entity_id=? AND status='active'",
            (reason, game_id),
        )
        return int(cursor.rowcount)

    @staticmethod
    def _release_revision(
        db, game_id: str, region: str | None, platform: str | None
    ) -> int:
        clauses = ["game_entity_id=?"]
        parameters: list[object] = [game_id]
        if region is not None:
            clauses.append("region=?")
            parameters.append(region)
        if platform is not None:
            clauses.append("platform=?")
            parameters.append(platform)
        row = db.execute(
            "SELECT MAX(revision) FROM game_release_states "
            "WHERE {}".format(" AND ".join(clauses)),
            tuple(parameters),
        ).fetchone()
        return 0 if row is None or row[0] is None else int(row[0])

    @staticmethod
    def _version_slot(row) -> VersionSlot:
        return VersionSlot.create(
            version_slot_id=row["version_slot_id"],
            game_entity_id=row["game_entity_id"],
            official_label=row["official_label"],
            region=row["region"],
            platform=row["platform"],
            release_state=row["release_state"],
            official_state=row["official_state"],
            rumor_state=row["rumor_state"],
            announced_at=row["announced_at"],
            release_at=row["release_at"],
            effective_until=row["effective_until"],
            release_checked_at=row["release_checked_at"],
            official_checked_at=row["official_checked_at"],
            rumor_checked_at=row["rumor_checked_at"],
            fresh_until=row["fresh_until"],
            status=row["status"],
            revision=row["revision"],
        )

    @staticmethod
    def _knowledge_job(row) -> KnowledgeJobRecord:
        payload = json.loads(str(row["request_json"]))
        if not isinstance(payload, dict):
            raise ValueError("knowledge job request is invalid")
        return KnowledgeJobRecord(
            job_id=str(row["job_id"]),
            idempotency_key=str(row["idempotency_key"]),
            job_kind=str(row["job_kind"]),
            group_id=(
                None if row["group_id"] is None else str(row["group_id"])
            ),
            entity_id=(
                None if row["entity_id"] is None else str(row["entity_id"])
            ),
            request=payload,
            status=str(row["status"]),
            attempt=int(row["attempt"]),
            next_attempt_at=int(row["next_attempt_at"]),
            diagnostic_code=(
                None if row["diagnostic_code"] is None
                else str(row["diagnostic_code"])
            ),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
        )

    @staticmethod
    def _negative_snapshot_record(row) -> NegativeSearchSnapshotRecord:
        covered, required = KnowledgeRepository._negative_source_ids(row)
        return NegativeSearchSnapshotRecord(
            snapshot_id=str(row["snapshot_id"]),
            game_entity_id=str(row["game_entity_id"]),
            query_intent=str(row["query_intent"]),
            covered_source_ids=covered,
            required_source_ids=required,
            region=None if row["region"] is None else str(row["region"]),
            platform=(
                None if row["platform"] is None else str(row["platform"])
            ),
            checked_at=int(row["checked_at"]),
            expires_at=int(row["expires_at"]),
            version_state_revision=int(row["version_state_revision"]),
            status=str(row["status"]),
            diagnostic_code=str(row["diagnostic_code"] or ""),
        )

    @staticmethod
    def _negative_source_ids(row) -> tuple[tuple[str, ...], tuple[str, ...]]:
        payload = json.loads(str(row["covered_source_ids_json"]))
        if isinstance(payload, list):
            covered = tuple(str(value) for value in payload)
            return covered, covered
        if not isinstance(payload, dict):
            raise ValueError("negative source coverage is malformed")
        covered = tuple(str(value) for value in payload.get("covered", ()))
        required = tuple(str(value) for value in payload.get("required", ()))
        return covered, required

    def record_convention_evidence(
        self,
        *,
        group_id: str,
        expression: str,
        entity_id: str,
        meaning_summary: str,
        observation_id: str,
        evidence_kind: str,
        admin_confirmed: bool,
        now: int,
    ) -> GroupConventionRecord:
        scope = _required_text(group_id, "group_id")
        normalized = _expression(expression)
        target = _required_text(entity_id, "entity_id")
        summary = _required_text(
            meaning_summary, "meaning_summary", maximum=240
        )
        observation_key = _required_text(observation_id, "observation_id")
        kind = _required_text(evidence_kind, "evidence_kind", maximum=24)
        if kind not in {"definition", "usage"}:
            raise ValueError("unsupported convention evidence kind")
        timestamp = int(now)
        convention_id = "convention:" + hashlib.sha256(
            "{}\0{}\0{}".format(scope, normalized, target).encode("utf-8")
        ).hexdigest()[:24]

        with connect_database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            observation = db.execute(
                "SELECT * FROM knowledge_observations WHERE observation_id=?",
                (observation_key,),
            ).fetchone()
            if (
                observation is None
                or str(observation["origin_class"]) != "human_chat"
                or str(observation["group_id"] or "") != scope
                or observation["author_ref"] is None
            ):
                raise ValueError(
                    "convention evidence is not qualified human chat"
                )
            entity = db.execute(
                "SELECT canonical_name,status FROM knowledge_entities "
                "WHERE entity_id=?",
                (target,),
            ).fetchone()
            if entity is None or str(entity["status"]) != "active":
                raise ValueError("convention target entity is not active")
            existing = db.execute(
                "SELECT evidence_observation_ids_json FROM group_conventions "
                "WHERE convention_id=?",
                (convention_id,),
            ).fetchone()
            evidence_ids = []
            if existing is not None:
                evidence_ids.extend(
                    str(value)
                    for value in json.loads(
                        str(existing["evidence_observation_ids_json"])
                    )
                )
            if observation_key not in evidence_ids:
                evidence_ids.append(observation_key)
            placeholders = ",".join("?" for _ in evidence_ids)
            evidence_rows = db.execute(
                "SELECT observation_id,author_ref,source_id,safe_summary,"
                "occurred_at FROM knowledge_observations "
                "WHERE observation_id IN ({})".format(placeholders),
                tuple(evidence_ids),
            ).fetchall()
            actor_count = len(
                {str(row["author_ref"]) for row in evidence_rows}
            )
            scene_count = len(
                {str(row["source_id"]) for row in evidence_rows}
            )
            definition_count = 0
            for evidence_row in evidence_rows:
                try:
                    safe_value = json.loads(
                        str(evidence_row["safe_summary"])
                    )
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                if safe_value.get("kind") == "definition":
                    definition_count += 1
            first_seen = min(
                int(row["occurred_at"]) for row in evidence_rows
            )
            last_seen = max(int(row["occurred_at"]) for row in evidence_rows)
            status = (
                "active"
                if admin_confirmed
                or (definition_count >= 1 and scene_count >= 2)
                else "candidate"
            )
            confidence = (
                1.0
                if admin_confirmed
                else (0.85 if status == "active" else 0.5)
            )
            db.execute(
                "INSERT INTO group_conventions(convention_id,group_id,"
                "normalized_expression,resolved_entity_id,meaning_summary,"
                "evidence_observation_ids_json,distinct_actor_count,"
                "distinct_scene_count,confidence,status,first_seen_at,"
                "last_seen_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(convention_id) DO UPDATE SET "
                "meaning_summary=excluded.meaning_summary,"
                "evidence_observation_ids_json=excluded.evidence_observation_ids_json,"
                "distinct_actor_count=excluded.distinct_actor_count,"
                "distinct_scene_count=excluded.distinct_scene_count,"
                "confidence=excluded.confidence,status=excluded.status,"
                "first_seen_at=MIN(first_seen_at,excluded.first_seen_at),"
                "last_seen_at=MAX(last_seen_at,excluded.last_seen_at),"
                "updated_at=excluded.updated_at",
                (
                    convention_id,
                    scope,
                    normalized,
                    target,
                    summary,
                    _json(evidence_ids),
                    actor_count,
                    scene_count,
                    confidence,
                    status,
                    first_seen,
                    last_seen,
                    timestamp,
                ),
            )
            conflicts = db.execute(
                "SELECT convention_id FROM group_conventions "
                "WHERE group_id=? AND normalized_expression=? "
                "AND resolved_entity_id<>? "
                "AND status IN ('candidate','active','disputed')",
                (scope, normalized, target),
            ).fetchall()
            if conflicts:
                conflict_ids = [str(row["convention_id"]) for row in conflicts]
                conflict_ids.append(convention_id)
                conflict_placeholders = ",".join("?" for _ in conflict_ids)
                db.execute(
                    "UPDATE group_conventions SET status='disputed',"
                    "confidence=0.0,updated_at=? WHERE convention_id IN ({})".format(
                        conflict_placeholders
                    ),
                    (timestamp, *conflict_ids),
                )
                db.execute(
                    "UPDATE group_knowledge_aliases SET status='disputed' "
                    "WHERE group_id=? AND normalized_alias=?",
                    (scope, normalized),
                )
            elif status == "active":
                alias_id = "alias:group:" + hashlib.sha256(
                    "{}\0{}\0{}".format(scope, normalized, target).encode(
                        "utf-8"
                    )
                ).hexdigest()[:24]
                db.execute(
                    "INSERT INTO group_knowledge_aliases(alias_id,group_id,"
                    "entity_id,normalized_alias,evidence_observation_ids_json,"
                    "confidence,last_used_at,status) VALUES(?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(group_id,normalized_alias,entity_id) DO UPDATE SET "
                    "evidence_observation_ids_json=excluded.evidence_observation_ids_json,"
                    "confidence=excluded.confidence,last_used_at=excluded.last_used_at,"
                    "status='active'",
                    (
                        alias_id,
                        scope,
                        target,
                        normalized,
                        _json(evidence_ids),
                        confidence,
                        last_seen,
                        "active",
                    ),
                )
            row = db.execute(
                "SELECT * FROM group_conventions WHERE convention_id=?",
                (convention_id,),
            ).fetchone()
        return self._convention_record(row)

    def expire_stale_conventions(
        self, *, now: int, max_age_seconds: int
    ) -> int:
        timestamp = int(now)
        maximum_age = int(max_age_seconds)
        if timestamp < 0 or maximum_age <= 0:
            raise ValueError("stale convention timestamps are invalid")
        threshold = timestamp - maximum_age
        with connect_database(self.path) as db:
            rows = db.execute(
                "SELECT group_id,normalized_expression,resolved_entity_id "
                "FROM group_conventions WHERE status IN ('candidate','active') "
                "AND last_seen_at<=?",
                (threshold,),
            ).fetchall()
            cursor = db.execute(
                "UPDATE group_conventions SET status='stale',confidence=0.0,"
                "updated_at=? WHERE status IN ('candidate','active') "
                "AND last_seen_at<=?",
                (timestamp, threshold),
            )
            for row in rows:
                db.execute(
                    "UPDATE group_knowledge_aliases SET status='stale' "
                    "WHERE group_id=? AND normalized_alias=? AND entity_id=?",
                    (
                        row["group_id"],
                        row["normalized_expression"],
                        row["resolved_entity_id"],
                    ),
                )
        return int(cursor.rowcount)

    def apply_knowledge_retention(
        self, *, now: int, batch_size: int = 500
    ) -> dict[str, int]:
        """Trim old low-trust text while preserving hashes and audit links."""

        timestamp = int(now)
        maximum = int(batch_size)
        if timestamp < 0 or not 1 <= maximum <= 500:
            raise ValueError("knowledge retention batch is invalid")
        observation_threshold = timestamp - 180 * 24 * 60 * 60
        source_threshold = timestamp - 30 * 24 * 60 * 60
        retained = "[retained metadata only]"
        with connect_database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            observation_rows = db.execute(
                "SELECT observation_id FROM knowledge_observations AS o "
                "WHERE o.recorded_at<=? AND o.status IN "
                "('admitted','rejected','expired') AND o.safe_summary<>? "
                "AND NOT EXISTS(SELECT 1 FROM knowledge_claim_evidence AS e "
                "WHERE e.observation_id=o.observation_id) "
                "ORDER BY o.recorded_at,o.observation_id LIMIT ?",
                (observation_threshold, retained, maximum),
            ).fetchall()
            observation_ids = tuple(
                str(row["observation_id"]) for row in observation_rows
            )
            if observation_ids:
                db.execute(
                    "UPDATE knowledge_observations SET safe_summary=? "
                    "WHERE observation_id IN ({})".format(
                        ",".join("?" for _ in observation_ids)
                    ),
                    (retained, *observation_ids),
                )
            remaining = maximum - len(observation_ids)
            source_ids: tuple[str, ...] = ()
            if remaining:
                source_rows = db.execute(
                    "SELECT source_id FROM knowledge_sources AS s "
                    "WHERE s.fetched_at<=? AND s.source_class IN "
                    "('secondary','unofficial') AND s.evidence_excerpt<>? "
                    "AND NOT EXISTS(SELECT 1 FROM knowledge_claim_evidence AS e "
                    "WHERE e.source_id=s.source_id) "
                    "ORDER BY s.fetched_at,s.source_id LIMIT ?",
                    (source_threshold, retained, remaining),
                ).fetchall()
                source_ids = tuple(str(row["source_id"]) for row in source_rows)
                if source_ids:
                    db.execute(
                        "UPDATE knowledge_sources SET evidence_excerpt=? "
                        "WHERE source_id IN ({})".format(
                            ",".join("?" for _ in source_ids)
                        ),
                        (retained, *source_ids),
                    )
        return {
            "observations_trimmed": len(observation_ids),
            "sources_trimmed": len(source_ids),
        }

    def knowledge_storage_text(self) -> str:
        """Return knowledge table text for privacy regression auditing."""

        tables = (
            "knowledge_observations",
            "group_conventions",
            "group_knowledge_aliases",
            "group_topic_mentions",
        )
        values = []
        with connect_database(self.path) as db:
            for table in tables:
                rows = db.execute("SELECT * FROM {}".format(table)).fetchall()
                for row in rows:
                    values.extend(
                        str(value)
                        for value in tuple(row)
                        if value is not None
                    )
        return "\n".join(values)

    @staticmethod
    def _stored_observation(row) -> StoredKnowledgeObservation:
        return StoredKnowledgeObservation(
            observation_id=str(row["observation_id"]),
            group_id=str(row["group_id"]),
            author_ref=str(row["author_ref"]),
            source_event_id=str(row["source_event_id"]),
            scene_ref=str(row["source_id"]),
            entity_hint=(
                None
                if row["entity_hint"] is None
                else str(row["entity_hint"])
            ),
            safe_summary=str(row["safe_summary"]),
            occurred_at=int(row["occurred_at"]),
            recorded_at=int(row["recorded_at"]),
            status=str(row["status"]),
        )

    @staticmethod
    def _convention_record(row) -> GroupConventionRecord:
        return GroupConventionRecord(
            convention_id=str(row["convention_id"]),
            group_id=str(row["group_id"]),
            normalized_expression=str(row["normalized_expression"]),
            resolved_entity_id=str(row["resolved_entity_id"]),
            meaning_summary=str(row["meaning_summary"]),
            evidence_observation_ids=tuple(
                str(value)
                for value in json.loads(
                    str(row["evidence_observation_ids_json"])
                )
            ),
            distinct_actor_count=int(row["distinct_actor_count"]),
            distinct_scene_count=int(row["distinct_scene_count"]),
            confidence=float(row["confidence"]),
            status=str(row["status"]),
            first_seen_at=int(row["first_seen_at"]),
            last_seen_at=int(row["last_seen_at"]),
            updated_at=int(row["updated_at"]),
        )

    def seed_versions(self, seed_id: str) -> tuple[SeedVersionRecord, ...]:
        identity = _required_text(seed_id, "seed_id")
        with connect_database(self.path) as db:
            rows = db.execute(
                "SELECT seed_id,seed_version,content_hash,status,imported_at "
                "FROM knowledge_seeds WHERE seed_id=? ORDER BY seed_version",
                (identity,),
            ).fetchall()
        return tuple(
            SeedVersionRecord(
                seed_id=str(row["seed_id"]),
                seed_version=int(row["seed_version"]),
                content_hash=str(row["content_hash"]),
                status=str(row["status"]),
                imported_at=int(row["imported_at"]),
            )
            for row in rows
        )

    def retire_bundled_seed(self, seed_id: str, *, retired_at: int) -> bool:
        """Retire only bundled projections while preserving learned evidence."""

        identity = _required_text(seed_id, "seed_id")
        timestamp = int(retired_at)
        if timestamp < 0:
            raise ValueError("retired_at must not be negative")
        claim_prefix = "claim:seed:{}:%".format(identity)
        with connect_database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT manifest_json FROM knowledge_seeds WHERE seed_id=? "
                "AND status='active' ORDER BY seed_version DESC LIMIT 1",
                (identity,),
            ).fetchone()
            if row is None:
                return False
            manifest = json.loads(str(row["manifest_json"]))
            game_id = _required_text(
                manifest["game"]["entity_id"], "game.entity_id"
            )
            db.execute(
                "UPDATE knowledge_seeds SET status='superseded' "
                "WHERE seed_id=? AND status='active'",
                (identity,),
            )
            db.execute(
                "UPDATE knowledge_claims SET status='superseded',updated_at=? "
                "WHERE evidence_level='bundled' AND status='active' "
                "AND claim_id LIKE ?",
                (timestamp, claim_prefix),
            )
            db.execute(
                "UPDATE knowledge_aliases SET status='stale' WHERE source_id IS NULL "
                "AND alias_id LIKE 'alias:seed:%' AND entity_id IN ("
                "SELECT entity_id FROM knowledge_entities "
                "WHERE canonical_game_id=?)",
                (game_id,),
            )
            db.execute(
                "UPDATE game_release_states SET status='superseded' "
                "WHERE game_entity_id=? AND status!='superseded'",
                (game_id,),
            )
            db.execute(
                "UPDATE negative_search_snapshots SET status='invalidated',"
                "diagnostic_code='bundled_seed_retired' "
                "WHERE game_entity_id=? AND status='active'",
                (game_id,),
            )
            db.execute(
                "UPDATE knowledge_jobs SET status='discarded',"
                "diagnostic_code='bundled_seed_retired',updated_at=? "
                "WHERE entity_id=? AND job_kind IN ("
                "'official_daily_probe','time_boundary_revalidation') "
                "AND status IN ('pending','running','retry')",
                (timestamp, game_id),
            )
        return True

    def import_seed_manifest(
        self, manifest: Mapping[str, Any], *, imported_at: int
    ) -> str:
        """Atomically import one validated semantic seed.

        Validation belongs to the seed loader. This projection method enforces
        the immutable-version rule again at the persistence boundary.
        """

        seed_id = _required_text(manifest.get("seed_id"), "seed_id")
        seed_version = int(manifest.get("seed_version", 0))
        if seed_version <= 0:
            raise ValueError("seed_version must be positive")
        content_hash = _required_text(
            manifest.get("content_hash"), "content_hash", maximum=64
        )
        timestamp = int(imported_at)
        if timestamp < 0:
            raise ValueError("imported_at must not be negative")
        game = manifest["game"]
        game_id = _required_text(game["entity_id"], "game.entity_id")
        claim_prefix = "claim:seed:{}:".format(seed_id)

        with connect_database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT content_hash FROM knowledge_seeds "
                "WHERE seed_id=? AND seed_version=?",
                (seed_id, seed_version),
            ).fetchone()
            if existing is not None:
                if str(existing["content_hash"]) == content_hash:
                    self._upsert_seed_alias(
                        db,
                        entity_id=game_id,
                        text=game["canonical_name"],
                        alias_kind="official",
                        ambiguity_level="none",
                    )
                    return "unchanged"
                raise SeedVersionConflict(
                    "seed {!r} version {} already has a different hash".format(
                        seed_id, seed_version
                    )
                )
            newest = db.execute(
                "SELECT MAX(seed_version) AS newest_version "
                "FROM knowledge_seeds WHERE seed_id=? AND status='active'",
                (seed_id,),
            ).fetchone()
            if (
                newest is not None
                and newest["newest_version"] is not None
                and int(newest["newest_version"]) > seed_version
            ):
                raise SeedVersionOrderConflict(
                    "seed {!r} already has newer active version {}".format(
                        seed_id, int(newest["newest_version"])
                    )
                )

            db.execute(
                "UPDATE knowledge_seeds SET status='superseded' "
                "WHERE seed_id=? AND status='active' AND seed_version<?",
                (seed_id, seed_version),
            )
            db.execute(
                "UPDATE knowledge_claims SET status='superseded',updated_at=? "
                "WHERE evidence_level='bundled' AND status='active' "
                "AND claim_id LIKE ?",
                (timestamp, claim_prefix + "%"),
            )
            db.execute(
                "INSERT INTO knowledge_seeds(seed_id,seed_version,content_hash,"
                "status,manifest_json,imported_at) VALUES(?,?,?,?,?,?)",
                (
                    seed_id,
                    seed_version,
                    content_hash,
                    "active",
                    json.dumps(
                        manifest,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    timestamp,
                ),
            )
            self._upsert_seed_entity(
                db,
                entity_id=game_id,
                entity_type="game",
                canonical_name=game["canonical_name"],
                game_id=game_id,
                now=timestamp,
            )
            aliases = list(game["aliases"]) + [
                {
                    "text": game["canonical_name"],
                    "kind": "official",
                    "ambiguity_level": "none",
                },
                {
                    "text": game["english_name"],
                    "kind": "translation",
                    "ambiguity_level": "contextual",
                }
            ]
            for alias in aliases:
                self._upsert_seed_alias(
                    db,
                    entity_id=game_id,
                    text=alias["text"],
                    alias_kind=alias["kind"],
                    ambiguity_level=alias["ambiguity_level"],
                )

            for entity in manifest["entities"]:
                entity_id = _required_text(entity["entity_id"], "entity_id")
                self._upsert_seed_entity(
                    db,
                    entity_id=entity_id,
                    entity_type=entity["entity_type"],
                    canonical_name=entity["canonical_name"],
                    game_id=game_id,
                    now=timestamp,
                )
                self._upsert_seed_alias(
                    db,
                    entity_id=entity_id,
                    text=entity["canonical_name"],
                    alias_kind="official",
                    ambiguity_level="none",
                )

            for term in manifest["terms"]:
                term_id = _required_text(term["term_id"], "term_id")
                self._upsert_seed_entity(
                    db,
                    entity_id=term_id,
                    entity_type="term",
                    canonical_name=term["text"],
                    game_id=game_id,
                    now=timestamp,
                )
                self._upsert_seed_alias(
                    db,
                    entity_id=term_id,
                    text=term["text"],
                    alias_kind=term["term_kind"],
                    ambiguity_level=term["ambiguity_level"],
                )
                self._insert_seed_claim(
                    db,
                    claim_id="{}v{}:term:{}".format(
                        claim_prefix, seed_version, term_id
                    ),
                    subject_entity_id=term_id,
                    predicate="meaning",
                    safe_summary=term["meaning_summary"],
                    now=timestamp,
                )

            for index, pattern in enumerate(manifest["discussion_patterns"]):
                self._insert_seed_claim(
                    db,
                    claim_id="{}v{}:pattern:{}".format(
                        claim_prefix, seed_version, index
                    ),
                    subject_entity_id=game_id,
                    predicate="discussion_pattern",
                    safe_summary=pattern,
                    now=timestamp,
                )

            for relation in manifest["stable_relations"]:
                self._insert_seed_claim(
                    db,
                    claim_id="{}v{}:relation:{}".format(
                        claim_prefix, seed_version, relation["relation_id"]
                    ),
                    subject_entity_id=relation["subject_entity_id"],
                    predicate=relation["predicate"],
                    safe_summary=relation["safe_summary"],
                    now=timestamp,
                )

            for source in manifest["official_sources"]:
                source_hash = hashlib.sha256(
                    json.dumps(
                        source,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
                db.execute(
                    "INSERT INTO knowledge_sources(source_id,canonical_url,"
                    "domain,publisher,source_class,published_at,fetched_at,"
                    "content_hash,evidence_excerpt) VALUES(?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(source_id) DO NOTHING",
                    (
                        source["source_id"],
                        source["url"],
                        source["domain"],
                        source["publisher"],
                        "official",
                        None,
                        timestamp,
                        source_hash,
                        "Seed-declared official entry point; not temporal evidence.",
                    ),
                )
        return "imported"

    @staticmethod
    def _upsert_seed_entity(
        db,
        *,
        entity_id: str,
        entity_type: str,
        canonical_name: str,
        game_id: str,
        now: int,
    ) -> None:
        db.execute(
            "INSERT INTO knowledge_entities(entity_id,entity_type,canonical_name,"
            "canonical_game_id,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(entity_id) DO UPDATE SET "
            "entity_type=excluded.entity_type,canonical_name=excluded.canonical_name,"
            "canonical_game_id=excluded.canonical_game_id,status='active',"
            "updated_at=excluded.updated_at",
            (
                _required_text(entity_id, "entity_id"),
                _required_text(entity_type, "entity_type", maximum=48),
                _required_text(canonical_name, "canonical_name", maximum=80),
                _required_text(game_id, "canonical_game_id"),
                "active",
                now,
                now,
            ),
        )

    @staticmethod
    def _upsert_seed_alias(
        db,
        *,
        entity_id: str,
        text: str,
        alias_kind: str,
        ambiguity_level: str,
    ) -> None:
        normalized = _expression(text)
        alias_id = "alias:seed:" + hashlib.sha256(
            "{}\0{}\0{}".format(entity_id, normalized, alias_kind).encode(
                "utf-8"
            )
        ).hexdigest()[:24]
        db.execute(
            "INSERT INTO knowledge_aliases(alias_id,entity_id,normalized_alias,"
            "alias_kind,ambiguity_level,source_id,status) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(entity_id,normalized_alias,alias_kind) DO UPDATE SET "
            "ambiguity_level=excluded.ambiguity_level,status='active'",
            (
                alias_id,
                entity_id,
                normalized,
                alias_kind,
                ambiguity_level,
                None,
                "active",
            ),
        )

    @staticmethod
    def _insert_seed_claim(
        db,
        *,
        claim_id: str,
        subject_entity_id: str,
        predicate: str,
        safe_summary: str,
        now: int,
    ) -> None:
        db.execute(
            "INSERT INTO knowledge_claims(claim_id,subject_entity_id,predicate,"
            "safe_summary,claim_kind,evidence_level,status,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (
                claim_id,
                subject_entity_id,
                predicate,
                _required_text(safe_summary, "safe_summary", maximum=500),
                "stable_semantic",
                "bundled",
                "active",
                now,
                now,
            ),
        )

    def upsert_entity(
        self,
        *,
        entity_id: str,
        entity_type: str,
        canonical_name: str,
        canonical_game_id: str,
        status: str,
        now: int,
    ) -> None:
        identity = _required_text(entity_id, "entity_id")
        entity_kind = _required_text(entity_type, "entity_type", maximum=48)
        name = _required_text(canonical_name, "canonical_name", maximum=80)
        game_id = _required_text(canonical_game_id, "canonical_game_id")
        timestamp = int(now)
        if timestamp < 0:
            raise ValueError("now must not be negative")
        with connect_database(self.path) as db:
            db.execute(
                "INSERT INTO knowledge_entities("
                "entity_id,entity_type,canonical_name,canonical_game_id,status,"
                "created_at,updated_at) VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(entity_id) DO UPDATE SET "
                "entity_type=CASE WHEN excluded.updated_at>=updated_at "
                "THEN excluded.entity_type ELSE entity_type END,"
                "canonical_name=CASE WHEN excluded.updated_at>=updated_at "
                "THEN excluded.canonical_name ELSE canonical_name END,"
                "canonical_game_id=CASE WHEN excluded.updated_at>=updated_at "
                "THEN excluded.canonical_game_id ELSE canonical_game_id END,"
                "status=CASE WHEN excluded.updated_at>=updated_at "
                "THEN excluded.status ELSE status END,"
                "updated_at=MAX(updated_at,excluded.updated_at)",
                (
                    identity,
                    entity_kind,
                    name,
                    game_id,
                    _required_text(status, "status", maximum=24),
                    timestamp,
                    timestamp,
                ),
            )

    def ensure_candidate_game(self, canonical_name: str, *, now: int) -> str:
        """Create a non-resolvable placeholder without downgrading learned data."""

        name = _required_text(canonical_name, "canonical_name", maximum=48)
        folded = " ".join(
            unicodedata.normalize("NFKC", name).casefold().split()
        )
        entity_id = "game:learned:" + hashlib.sha256(
            folded.encode("utf-8")
        ).hexdigest()[:24]
        timestamp = int(now)
        if timestamp < 0:
            raise ValueError("now must not be negative")
        with connect_database(self.path) as db:
            db.execute(
                "INSERT OR IGNORE INTO knowledge_entities("
                "entity_id,entity_type,canonical_name,canonical_game_id,status,"
                "created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (
                    entity_id,
                    "game",
                    name,
                    entity_id,
                    "candidate",
                    timestamp,
                    timestamp,
                ),
            )
        return entity_id

    def put_alias(
        self,
        *,
        alias_id: str,
        entity_id: str,
        normalized_alias: str,
        alias_kind: str,
        ambiguity_level: str,
        source_id: str | None,
        status: str,
    ) -> None:
        with connect_database(self.path) as db:
            db.execute(
                "INSERT INTO knowledge_aliases("
                "alias_id,entity_id,normalized_alias,alias_kind,ambiguity_level,"
                "source_id,status) VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(entity_id,normalized_alias,alias_kind) DO UPDATE SET "
                "ambiguity_level=excluded.ambiguity_level,"
                "source_id=excluded.source_id,status=excluded.status",
                (
                    _required_text(alias_id, "alias_id"),
                    _required_text(entity_id, "entity_id"),
                    _expression(normalized_alias),
                    _required_text(alias_kind, "alias_kind", maximum=24),
                    _required_text(
                        ambiguity_level, "ambiguity_level", maximum=24
                    ),
                    (
                        None
                        if source_id is None
                        else _required_text(source_id, "source_id")
                    ),
                    _required_text(status, "status", maximum=24),
                ),
            )

    def put_group_alias(
        self,
        *,
        alias_id: str,
        group_id: str,
        entity_id: str,
        normalized_alias: str,
        evidence_observation_ids: Iterable[str],
        confidence: float,
        last_used_at: int,
        status: str,
    ) -> None:
        evidence = tuple(
            dict.fromkeys(
                _required_text(value, "evidence_observation_id")
                for value in evidence_observation_ids
            )
        )
        if not evidence:
            raise ValueError("evidence_observation_ids must not be empty")
        score = float(confidence)
        if not math.isfinite(score) or not 0.0 <= score <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        with connect_database(self.path) as db:
            db.execute(
                "INSERT INTO group_knowledge_aliases("
                "alias_id,group_id,entity_id,normalized_alias,"
                "evidence_observation_ids_json,confidence,last_used_at,status) "
                "VALUES(?,?,?,?,?,?,?,?) "
                "ON CONFLICT(group_id,normalized_alias,entity_id) DO UPDATE SET "
                "evidence_observation_ids_json=excluded.evidence_observation_ids_json,"
                "confidence=excluded.confidence,last_used_at=excluded.last_used_at,"
                "status=excluded.status",
                (
                    _required_text(alias_id, "alias_id"),
                    _required_text(group_id, "group_id"),
                    _required_text(entity_id, "entity_id"),
                    _expression(normalized_alias),
                    _json(evidence),
                    score,
                    int(last_used_at),
                    _required_text(status, "status", maximum=24),
                ),
            )

    def aliases_for_text(
        self, text: str, *, group_id: str
    ) -> tuple[KnowledgeAliasRecord, ...]:
        expression = _expression(text)
        scope = _required_text(group_id, "group_id")
        with connect_database(self.path) as db:
            group_rows = db.execute(
                "SELECT * FROM group_knowledge_aliases "
                "WHERE group_id=? AND normalized_alias=? AND status='active' "
                "ORDER BY confidence DESC,alias_id",
                (scope, expression),
            ).fetchall()
            global_rows = db.execute(
                "SELECT * FROM knowledge_aliases "
                "WHERE normalized_alias=? AND status='active' "
                "ORDER BY CASE ambiguity_level "
                "WHEN 'none' THEN 0 WHEN 'contextual' THEN 1 ELSE 2 END,alias_id",
                (expression,),
            ).fetchall()
        return tuple(
            KnowledgeAliasRecord(
                alias_id=str(row["alias_id"]),
                scope_kind="group",
                group_id=str(row["group_id"]),
                entity_id=str(row["entity_id"]),
                normalized_alias=str(row["normalized_alias"]),
                alias_kind="community",
                ambiguity_level="contextual",
                confidence=float(row["confidence"]),
                status=str(row["status"]),
            )
            for row in group_rows
        ) + tuple(
            KnowledgeAliasRecord(
                alias_id=str(row["alias_id"]),
                scope_kind="global",
                group_id=None,
                entity_id=str(row["entity_id"]),
                normalized_alias=str(row["normalized_alias"]),
                alias_kind=str(row["alias_kind"]),
                ambiguity_level=str(row["ambiguity_level"]),
                confidence=1.0,
                status=str(row["status"]),
            )
            for row in global_rows
        )

    def record_qualified_mention(
        self, observation_id: str, entity_id: str, scene_ref: str
    ) -> bool:
        observation_key = _required_text(observation_id, "observation_id")
        entity_key = _required_text(entity_id, "entity_id")
        scene_key = _required_text(scene_ref, "scene_ref")
        with connect_database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            observation = db.execute(
                "SELECT * FROM knowledge_observations WHERE observation_id=?",
                (observation_key,),
            ).fetchone()
            if observation is None:
                raise ValueError("knowledge observation does not exist")
            if (
                str(observation["origin_class"]) != OriginClass.HUMAN_CHAT.value
                or str(observation["scope_kind"]) != KnowledgeScope.GROUP.value
                or observation["group_id"] is None
                or observation["author_ref"] is None
                or observation["source_event_id"] is None
                or str(observation["status"]) not in {"pending", "admitted"}
            ):
                raise ValueError("observation is not a qualified human mention")
            entity = db.execute(
                "SELECT entity_type,status FROM knowledge_entities "
                "WHERE entity_id=?",
                (entity_key,),
            ).fetchone()
            if (
                entity is None
                or str(entity["entity_type"]) != "game"
                or str(entity["status"]) not in {"candidate", "active"}
            ):
                raise ValueError("mentioned game entity is not eligible")
            group_id = str(observation["group_id"])
            cursor = db.execute(
                "INSERT OR IGNORE INTO group_topic_mentions("
                "group_id,entity_id,observation_id,source_event_id,author_ref,"
                "scene_ref,occurred_at) VALUES(?,?,?,?,?,?,?)",
                (
                    group_id,
                    entity_key,
                    observation_key,
                    str(observation["source_event_id"]),
                    str(observation["author_ref"]),
                    scene_key,
                    int(observation["occurred_at"]),
                ),
            )
            if cursor.rowcount != 1:
                return False
            self._refresh_affinity(db, group_id, entity_key)
            return True

    def topic_affinity(
        self, group_id: str, entity_id: str, *, now: int
    ) -> TopicAffinity | None:
        scope = _required_text(group_id, "group_id")
        entity_key = _required_text(entity_id, "entity_id")
        timestamp = int(now)
        if timestamp < 0:
            raise ValueError("now must not be negative")
        with connect_database(self.path) as db:
            row = db.execute(
                "SELECT * FROM group_topic_affinity "
                "WHERE group_id=? AND entity_id=?",
                (scope, entity_key),
            ).fetchone()
            if row is None:
                return None
            mentions = db.execute(
                "SELECT occurred_at FROM group_topic_mentions "
                "WHERE group_id=? AND entity_id=?",
                (scope, entity_key),
            ).fetchall()
        salience = sum(
            self._decayed_weight(timestamp, int(item["occurred_at"]))
            for item in mentions
        )
        return TopicAffinity(
            group_id=scope,
            entity_id=entity_key,
            qualified_mention_count=int(row["qualified_mention_count"]),
            distinct_actor_count=int(row["distinct_actor_count"]),
            distinct_scene_count=int(row["distinct_scene_count"]),
            salience=salience,
            first_seen_at=int(row["first_seen_at"]),
            last_seen_at=int(row["last_seen_at"]),
            updated_at=int(row["updated_at"]),
        )

    def topic_affinity_window(
        self,
        group_id: str,
        entity_id: str,
        *,
        now: int,
        window_seconds: int,
    ) -> TopicAffinity | None:
        scope = _required_text(group_id, "group_id")
        entity_key = _required_text(entity_id, "entity_id")
        timestamp = int(now)
        window = int(window_seconds)
        if timestamp < 0 or window < 1:
            raise ValueError("affinity window is invalid")
        with connect_database(self.path) as db:
            rows = db.execute(
                "SELECT author_ref,scene_ref,occurred_at FROM group_topic_mentions "
                "WHERE group_id=? AND entity_id=? AND occurred_at>? "
                "ORDER BY occurred_at,source_event_id",
                (scope, entity_key, timestamp - window),
            ).fetchall()
        if not rows:
            return None
        occurred = [int(row["occurred_at"]) for row in rows]
        return TopicAffinity(
            group_id=scope,
            entity_id=entity_key,
            qualified_mention_count=len(rows),
            distinct_actor_count=len({str(row["author_ref"]) for row in rows}),
            distinct_scene_count=len({str(row["scene_ref"]) for row in rows}),
            salience=sum(
                self._decayed_weight(timestamp, value) for value in occurred
            ),
            first_seen_at=min(occurred),
            last_seen_at=max(occurred),
            updated_at=max(occurred),
        )

    @staticmethod
    def _refresh_affinity(db, group_id: str, entity_id: str) -> None:
        aggregate = db.execute(
            "SELECT COUNT(*) AS mention_count,"
            "COUNT(DISTINCT author_ref) AS actor_count,"
            "COUNT(DISTINCT scene_ref) AS scene_count,"
            "MIN(occurred_at) AS first_seen_at,MAX(occurred_at) AS last_seen_at "
            "FROM group_topic_mentions WHERE group_id=? AND entity_id=?",
            (group_id, entity_id),
        ).fetchone()
        last_seen_at = int(aggregate["last_seen_at"])
        mentions = db.execute(
            "SELECT occurred_at FROM group_topic_mentions "
            "WHERE group_id=? AND entity_id=?",
            (group_id, entity_id),
        ).fetchall()
        salience = sum(
            KnowledgeRepository._decayed_weight(
                last_seen_at, int(item["occurred_at"])
            )
            for item in mentions
        )
        db.execute(
            "INSERT INTO group_topic_affinity("
            "group_id,entity_id,qualified_mention_count,distinct_actor_count,"
            "distinct_scene_count,salience,first_seen_at,last_seen_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(group_id,entity_id) DO UPDATE SET "
            "qualified_mention_count=excluded.qualified_mention_count,"
            "distinct_actor_count=excluded.distinct_actor_count,"
            "distinct_scene_count=excluded.distinct_scene_count,"
            "salience=excluded.salience,first_seen_at=excluded.first_seen_at,"
            "last_seen_at=excluded.last_seen_at,updated_at=excluded.updated_at",
            (
                group_id,
                entity_id,
                int(aggregate["mention_count"]),
                int(aggregate["actor_count"]),
                int(aggregate["scene_count"]),
                salience,
                int(aggregate["first_seen_at"]),
                last_seen_at,
                last_seen_at,
            ),
        )

    @staticmethod
    def _decayed_weight(now: int, occurred_at: int) -> float:
        age = max(0, int(now) - int(occurred_at))
        return 0.5 ** (age / AFFINITY_HALF_LIFE_SECONDS)


__all__ = (
    "AFFINITY_HALF_LIFE_SECONDS",
    "ClaimWriteResult",
    "GroupConventionRecord",
    "KnowledgeAliasRecord",
    "KnowledgeClaimRecord",
    "KnowledgeEntityRecord",
    "KnowledgeRepository",
    "NegativeSearchSnapshotRecord",
    "NegativeSnapshotKey",
    "ReleaseStateConflict",
    "SeedVersionConflict",
    "SeedVersionOrderConflict",
    "SeedVersionRecord",
    "StoredKnowledgeObservation",
    "TopicAffinity",
)
