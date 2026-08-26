"""Group-scoped SQLite repository for member cognition."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path

from ..persistence.schema import connect_database, initialize_database
from .contracts import (
    MemberAlias,
    MemberIdentity,
    ProfileEpisode,
    ProfileCorrection,
    ProfileFact,
    ProfileObservation,
    ProfileSnapshot,
    SocialEdge,
)
from .group_portrait import GroupPortrait


class ProfileIdentityConflict(RuntimeError):
    """Raised when a stable record id is replayed with different content."""


class ProfileRepository:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        initialize_database(self.path)

    def upsert_identity(self, identity: MemberIdentity) -> MemberIdentity:
        with connect_database(self.path) as db:
            db.execute(
                "INSERT INTO member_identities("
                "persona_id,platform,actor_id,display_name,avatar_ref,system_roles_json,"
                "first_seen_at,last_seen_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(persona_id,platform,actor_id) DO UPDATE SET "
                "display_name=CASE WHEN excluded.updated_at>=updated_at THEN excluded.display_name ELSE display_name END,"
                "avatar_ref=CASE WHEN excluded.updated_at>=updated_at THEN excluded.avatar_ref ELSE avatar_ref END,"
                "system_roles_json=CASE WHEN excluded.updated_at>=updated_at THEN excluded.system_roles_json ELSE system_roles_json END,"
                "last_seen_at=MAX(last_seen_at,excluded.last_seen_at),"
                "updated_at=MAX(updated_at,excluded.updated_at)",
                (
                    identity.persona_id,
                    identity.platform,
                    identity.actor_id,
                    identity.display_name,
                    identity.avatar_ref,
                    self._json(identity.system_roles),
                    identity.updated_at,
                    identity.updated_at,
                    identity.updated_at,
                ),
            )
        return identity

    def identity(
        self, persona_id: str, platform: str, actor_id: str
    ) -> MemberIdentity | None:
        with connect_database(self.path) as db:
            row = db.execute(
                "SELECT * FROM member_identities "
                "WHERE persona_id=? AND platform=? AND actor_id=?",
                (str(persona_id), str(platform), str(actor_id)),
            ).fetchone()
        if row is None:
            return None
        return MemberIdentity(
            persona_id=str(row["persona_id"]),
            platform=str(row["platform"]),
            actor_id=str(row["actor_id"]),
            display_name=str(row["display_name"]),
            avatar_ref=(
                str(row["avatar_ref"])
                if row["avatar_ref"] is not None
                else None
            ),
            system_roles=tuple(json.loads(row["system_roles_json"])),
            updated_at=int(row["updated_at"]),
        )

    def remember_alias(self, alias: MemberAlias) -> MemberAlias:
        with connect_database(self.path) as db:
            db.execute(
                "INSERT INTO member_aliases("
                "persona_id,group_id,actor_id,alias,alias_type,confidence,source_event_id,"
                "status,first_seen_at,last_seen_at) VALUES(?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(persona_id,group_id,actor_id,alias) DO UPDATE SET "
                "alias_type=excluded.alias_type,confidence=MAX(confidence,excluded.confidence),"
                "source_event_id=COALESCE(excluded.source_event_id,source_event_id),"
                "status=excluded.status,last_seen_at=MAX(last_seen_at,excluded.last_seen_at)",
                (
                    alias.persona_id,
                    alias.group_id,
                    alias.actor_id,
                    alias.alias,
                    alias.alias_type,
                    float(alias.confidence),
                    alias.source_event_id,
                    alias.status,
                    int(alias.first_seen_at),
                    int(alias.last_seen_at),
                ),
            )
        return alias

    def aliases(
        self, persona_id: str, group_id: str, actor_id: str
    ) -> tuple[MemberAlias, ...]:
        with connect_database(self.path) as db:
            rows = db.execute(
                "SELECT rowid,* FROM member_aliases "
                "WHERE persona_id=? AND group_id=? AND actor_id=? "
                "ORDER BY first_seen_at,rowid",
                (str(persona_id), str(group_id), str(actor_id)),
            ).fetchall()
        return tuple(
            MemberAlias(
                persona_id=str(row["persona_id"]),
                group_id=str(row["group_id"]),
                actor_id=str(row["actor_id"]),
                alias=str(row["alias"]),
                alias_type=str(row["alias_type"]),
                confidence=float(row["confidence"]),
                source_event_id=(
                    str(row["source_event_id"])
                    if row["source_event_id"] is not None
                    else None
                ),
                status=str(row["status"]),
                first_seen_at=int(row["first_seen_at"]),
                last_seen_at=int(row["last_seen_at"]),
            )
            for row in rows
        )

    def enqueue_observation(self, observation: ProfileObservation) -> bool:
        with connect_database(self.path) as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO profile_observations("
                "event_id,persona_id,group_id,actor_id,observation_json,occurred_at,"
                "status,attempt,next_attempt_at,diagnostic_code) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    observation.event_id,
                    observation.persona_id,
                    observation.group_id,
                    observation.actor_id,
                    self._json(observation.payload),
                    int(observation.occurred_at),
                    observation.status,
                    int(observation.attempt),
                    int(observation.next_attempt_at),
                    observation.diagnostic_code,
                ),
            )
            return cursor.rowcount == 1

    def claim_observations(
        self,
        persona_id: str,
        group_id: str,
        *,
        limit: int,
        now: int,
    ) -> tuple[ProfileObservation, ...]:
        with connect_database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT * FROM profile_observations WHERE persona_id=? AND group_id=? "
                "AND status IN ('pending','retry') AND next_attempt_at<=? "
                "ORDER BY occurred_at,event_id LIMIT ?",
                (str(persona_id), str(group_id), int(now), max(1, int(limit))),
            ).fetchall()
            event_ids = tuple(str(row["event_id"]) for row in rows)
            if event_ids:
                placeholders = ",".join("?" for _ in event_ids)
                db.execute(
                    f"UPDATE profile_observations SET status='processing',attempt=attempt+1 "
                    f"WHERE event_id IN ({placeholders})",
                    event_ids,
                )
        return tuple(self._observation(row, status="processing") for row in rows)

    def pending_observation_count(
        self, persona_id: str, group_id: str
    ) -> int:
        with connect_database(self.path) as db:
            row = db.execute(
                "SELECT COUNT(*) FROM profile_observations "
                "WHERE persona_id=? AND group_id=? AND status IN ('pending','retry')",
                (str(persona_id), str(group_id)),
            ).fetchone()
        return int(row[0]) if row is not None else 0

    def observation_diagnostics(
        self, persona_id: str, group_id: str
    ) -> tuple[str, ...]:
        with connect_database(self.path) as db:
            rows = db.execute(
                "SELECT diagnostic_code FROM profile_observations "
                "WHERE persona_id=? AND group_id=? AND diagnostic_code IS NOT NULL "
                "ORDER BY occurred_at DESC,event_id DESC LIMIT 20",
                (str(persona_id), str(group_id)),
            ).fetchall()
        return tuple(dict.fromkeys(str(row[0]) for row in rows))

    def complete_observations(
        self,
        event_ids: tuple[str, ...],
        *,
        status: str,
        diagnostic_code: str | None,
        next_attempt_at: int = 0,
    ) -> None:
        if not event_ids:
            return
        if status not in {"completed", "retry", "discarded"}:
            raise ValueError("invalid observation completion status")
        placeholders = ",".join("?" for _ in event_ids)
        with connect_database(self.path) as db:
            db.execute(
                f"UPDATE profile_observations SET status=?,diagnostic_code=?,next_attempt_at=? "
                f"WHERE event_id IN ({placeholders})",
                (status, diagnostic_code, int(next_attempt_at), *event_ids),
            )

    def put_fact(self, fact: ProfileFact) -> ProfileFact:
        values = self._fact_values(fact)
        with connect_database(self.path) as db:
            try:
                db.execute(
                    "INSERT INTO profile_facts("
                    "fact_id,persona_id,group_id,subject_id,category,summary,source_kind,"
                    "source_actor_id,source_event_ids_json,confidence,status,evidence_count,"
                    "valid_from,valid_until,supersedes_fact_id,injectable,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    values,
                )
            except sqlite3.IntegrityError:
                existing = db.execute(
                    "SELECT * FROM profile_facts WHERE fact_id=?", (fact.fact_id,)
                ).fetchone()
                if existing is None or self._fact(existing) != fact:
                    raise ProfileIdentityConflict(fact.fact_id) from None
        return fact

    def facts(
        self,
        persona_id: str,
        group_id: str,
        subject_id: str,
        *,
        injectable_only: bool = False,
    ) -> tuple[ProfileFact, ...]:
        query = (
            "SELECT * FROM profile_facts WHERE persona_id=? AND group_id=? AND subject_id=?"
        )
        parameters: tuple[object, ...] = (
            str(persona_id),
            str(group_id),
            str(subject_id),
        )
        if injectable_only:
            query += " AND status='confirmed' AND injectable=1"
        query += " ORDER BY valid_from,fact_id"
        with connect_database(self.path) as db:
            rows = db.execute(query, parameters).fetchall()
        return tuple(self._fact(row) for row in rows)

    def replace_fact(
        self,
        correction: ProfileCorrection,
        *,
        audit_id: str,
        actor_id: str,
        created_at: int,
    ) -> None:
        if (
            correction.old.persona_id,
            correction.old.group_id,
            correction.old.subject_id,
        ) != (
            correction.new.persona_id,
            correction.new.group_id,
            correction.new.subject_id,
        ):
            raise ValueError("profile correction scope mismatch")
        with connect_database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM profile_facts WHERE fact_id=?",
                (correction.old.fact_id,),
            ).fetchone()
            if row is None:
                raise KeyError(correction.old.fact_id)
            current = self._fact(row)
            if (
                current.persona_id,
                current.group_id,
                current.subject_id,
            ) != (
                correction.old.persona_id,
                correction.old.group_id,
                correction.old.subject_id,
            ):
                raise ValueError("profile correction target mismatch")
            db.execute(
                "UPDATE profile_facts SET status='superseded',injectable=0,"
                "valid_until=?,updated_at=? WHERE fact_id=?",
                (
                    correction.old.valid_until,
                    int(created_at),
                    correction.old.fact_id,
                ),
            )
            db.execute(
                "INSERT INTO profile_facts("
                "fact_id,persona_id,group_id,subject_id,category,summary,source_kind,"
                "source_actor_id,source_event_ids_json,confidence,status,evidence_count,"
                "valid_from,valid_until,supersedes_fact_id,injectable,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                self._fact_values(correction.new),
            )
            db.execute(
                "INSERT INTO profile_audit("
                "audit_id,persona_id,group_id,subject_id,actor_id,action_type,"
                "target_id,audit_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    str(audit_id),
                    correction.new.persona_id,
                    correction.new.group_id,
                    correction.new.subject_id,
                    str(actor_id),
                    "profile_fact_corrected",
                    correction.old.fact_id,
                    self._json(
                        {
                            "old_fact_id": correction.old.fact_id,
                            "new_fact_id": correction.new.fact_id,
                        }
                    ),
                    int(created_at),
                ),
            )

    def change_fact_status(
        self,
        fact_id: str,
        *,
        persona_id: str,
        group_id: str,
        subject_id: str,
        status: str,
        audit_id: str,
        actor_id: str,
        created_at: int,
    ) -> ProfileFact:
        if status not in {"stale", "rejected"}:
            raise ValueError("fact status change must be stale or rejected")
        with connect_database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM profile_facts WHERE fact_id=? AND persona_id=? "
                "AND group_id=? AND subject_id=?",
                (str(fact_id), str(persona_id), str(group_id), str(subject_id)),
            ).fetchone()
            if row is None:
                raise KeyError(fact_id)
            db.execute(
                "UPDATE profile_facts SET status=?,injectable=0,valid_until=?,"
                "updated_at=? WHERE fact_id=?",
                (status, int(created_at), int(created_at), str(fact_id)),
            )
            db.execute(
                "INSERT INTO profile_audit("
                "audit_id,persona_id,group_id,subject_id,actor_id,action_type,"
                "target_id,audit_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    str(audit_id),
                    str(persona_id),
                    str(group_id),
                    str(subject_id),
                    str(actor_id),
                    "profile_fact_stale"
                    if status == "stale"
                    else "profile_fact_invalidated",
                    str(fact_id),
                    self._json({"status": status}),
                    int(created_at),
                ),
            )
            updated = db.execute(
                "SELECT * FROM profile_facts WHERE fact_id=?", (str(fact_id),)
            ).fetchone()
        return self._fact(updated)

    def put_episode(self, episode: ProfileEpisode) -> ProfileEpisode:
        with connect_database(self.path) as db:
            db.execute(
                "INSERT INTO profile_episodes("
                "episode_id,persona_id,group_id,title,summary,participants_json,"
                "source_event_ids_json,episode_type,valence,importance,confidence,status,"
                "occurred_at,last_reinforced_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(episode_id) DO UPDATE SET "
                "title=excluded.title,summary=excluded.summary,"
                "participants_json=excluded.participants_json,"
                "source_event_ids_json=excluded.source_event_ids_json,"
                "episode_type=excluded.episode_type,valence=excluded.valence,"
                "importance=excluded.importance,confidence=excluded.confidence,"
                "status=excluded.status,last_reinforced_at=excluded.last_reinforced_at,"
                "updated_at=excluded.updated_at "
                "WHERE persona_id=excluded.persona_id AND group_id=excluded.group_id",
                (
                    episode.episode_id,
                    episode.persona_id,
                    episode.group_id,
                    episode.title,
                    episode.summary,
                    self._json(episode.participants),
                    self._json(episode.source_event_ids),
                    episode.episode_type,
                    episode.valence,
                    episode.importance,
                    episode.confidence,
                    episode.status,
                    episode.occurred_at,
                    episode.last_reinforced_at,
                    episode.last_reinforced_at,
                ),
            )
        return episode

    def episodes(
        self,
        persona_id: str,
        group_id: str,
        subject_id: str | None = None,
    ) -> tuple[ProfileEpisode, ...]:
        with connect_database(self.path) as db:
            rows = db.execute(
                "SELECT * FROM profile_episodes WHERE persona_id=? AND group_id=? "
                "ORDER BY occurred_at,episode_id",
                (str(persona_id), str(group_id)),
            ).fetchall()
        episodes = tuple(self._episode(row) for row in rows)
        if subject_id is None:
            return episodes
        normalized = str(subject_id)
        return tuple(item for item in episodes if normalized in item.participants)

    def put_edge(self, edge: SocialEdge) -> SocialEdge:
        with connect_database(self.path) as db:
            db.execute(
                "INSERT INTO social_edges("
                "edge_id,persona_id,group_id,source_member_id,target_member_id,"
                "relation_type,direction,strength,confidence,source_event_ids_json,"
                "status,valid_from,valid_until,last_observed_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(edge_id) DO UPDATE SET "
                "relation_type=excluded.relation_type,direction=excluded.direction,"
                "strength=excluded.strength,confidence=excluded.confidence,"
                "source_event_ids_json=excluded.source_event_ids_json,status=excluded.status,"
                "valid_until=excluded.valid_until,last_observed_at=excluded.last_observed_at,"
                "updated_at=excluded.updated_at "
                "WHERE persona_id=excluded.persona_id AND group_id=excluded.group_id "
                "AND source_member_id=excluded.source_member_id "
                "AND target_member_id=excluded.target_member_id",
                (
                    edge.edge_id,
                    edge.persona_id,
                    edge.group_id,
                    edge.source_member_id,
                    edge.target_member_id,
                    edge.relation_type,
                    edge.direction,
                    edge.strength,
                    edge.confidence,
                    self._json(edge.source_event_ids),
                    edge.status,
                    edge.valid_from,
                    edge.valid_until,
                    edge.last_observed_at,
                    edge.last_observed_at,
                ),
            )
        return edge

    def edges(
        self,
        persona_id: str,
        group_id: str,
        subject_id: str | None = None,
    ) -> tuple[SocialEdge, ...]:
        query = "SELECT * FROM social_edges WHERE persona_id=? AND group_id=?"
        values: tuple[object, ...] = (str(persona_id), str(group_id))
        if subject_id is not None:
            query += " AND (source_member_id=? OR target_member_id=?)"
            values += (str(subject_id), str(subject_id))
        query += " ORDER BY valid_from,edge_id"
        with connect_database(self.path) as db:
            rows = db.execute(query, values).fetchall()
        return tuple(self._edge(row) for row in rows)

    def put_snapshot(self, snapshot: ProfileSnapshot) -> ProfileSnapshot:
        payload = asdict(snapshot)
        with connect_database(self.path) as db:
            db.execute(
                "INSERT INTO profile_snapshots("
                "persona_id,group_id,subject_id,snapshot_json,source_revision,generated_at) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(persona_id,group_id,subject_id) "
                "DO UPDATE SET snapshot_json=excluded.snapshot_json,"
                "source_revision=excluded.source_revision,generated_at=excluded.generated_at "
                "WHERE excluded.source_revision>=source_revision",
                (
                    snapshot.persona_id,
                    snapshot.group_id,
                    snapshot.subject_id,
                    self._json(payload),
                    snapshot.source_revision,
                    snapshot.generated_at,
                ),
            )
        return snapshot

    def snapshot(
        self, persona_id: str, group_id: str, subject_id: str
    ) -> ProfileSnapshot | None:
        with connect_database(self.path) as db:
            row = db.execute(
                "SELECT snapshot_json FROM profile_snapshots "
                "WHERE persona_id=? AND group_id=? AND subject_id=?",
                (str(persona_id), str(group_id), str(subject_id)),
            ).fetchone()
        if row is None:
            return None
        values = dict(json.loads(row["snapshot_json"]))
        for name in (
            "group_roles",
            "individual_fingerprints",
            "preferences_and_boundaries",
            "representative_episode_ids",
        ):
            values[name] = tuple(values.get(name) or ())
        return ProfileSnapshot(**values)

    def put_group_portrait(self, portrait: GroupPortrait) -> GroupPortrait:
        with connect_database(self.path) as db:
            db.execute(
                "INSERT INTO group_portraits("
                "persona_id,group_id,portrait_json,source_revision,generated_at) "
                "VALUES(?,?,?,?,?) ON CONFLICT(persona_id,group_id) "
                "DO UPDATE SET portrait_json=excluded.portrait_json,"
                "source_revision=excluded.source_revision,generated_at=excluded.generated_at "
                "WHERE excluded.source_revision>=source_revision",
                (
                    portrait.persona_id,
                    portrait.group_id,
                    self._json(asdict(portrait)),
                    portrait.source_revision,
                    portrait.generated_at,
                ),
            )
        return portrait

    def group_portrait(
        self, persona_id: str, group_id: str
    ) -> GroupPortrait | None:
        with connect_database(self.path) as db:
            row = db.execute(
                "SELECT portrait_json FROM group_portraits "
                "WHERE persona_id=? AND group_id=?",
                (str(persona_id), str(group_id)),
            ).fetchone()
        if row is None:
            return None
        values = dict(json.loads(row["portrait_json"]))
        values["common_topics"] = tuple(values.get("common_topics") or ())
        values["role_counts"] = {
            str(key): int(value)
            for key, value in dict(values.get("role_counts") or {}).items()
        }
        values["relation_counts"] = {
            str(key): int(value)
            for key, value in dict(values.get("relation_counts") or {}).items()
        }
        return GroupPortrait(**values)

    @staticmethod
    def _json(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    @classmethod
    def _fact_values(cls, fact: ProfileFact) -> tuple[object, ...]:
        return (
            fact.fact_id,
            fact.persona_id,
            fact.group_id,
            fact.subject_id,
            fact.category,
            fact.summary,
            fact.source_kind,
            fact.source_actor_id,
            cls._json(fact.source_event_ids),
            fact.confidence,
            fact.status,
            fact.evidence_count,
            fact.valid_from,
            fact.valid_until,
            fact.supersedes_fact_id,
            int(fact.injectable),
            fact.valid_from,
        )

    @staticmethod
    def _fact(row) -> ProfileFact:
        return ProfileFact(
            fact_id=str(row["fact_id"]),
            persona_id=str(row["persona_id"]),
            group_id=str(row["group_id"]),
            subject_id=str(row["subject_id"]),
            category=str(row["category"]),
            summary=str(row["summary"]),
            source_kind=str(row["source_kind"]),
            source_actor_id=str(row["source_actor_id"]),
            source_event_ids=tuple(json.loads(row["source_event_ids_json"])),
            confidence=float(row["confidence"]),
            status=str(row["status"]),
            evidence_count=int(row["evidence_count"]),
            valid_from=int(row["valid_from"]),
            valid_until=(int(row["valid_until"]) if row["valid_until"] is not None else None),
            supersedes_fact_id=(
                str(row["supersedes_fact_id"])
                if row["supersedes_fact_id"] is not None
                else None
            ),
            injectable=bool(row["injectable"]),
        )

    @staticmethod
    def _observation(row, *, status: str | None = None) -> ProfileObservation:
        return ProfileObservation(
            event_id=str(row["event_id"]),
            persona_id=str(row["persona_id"]),
            group_id=str(row["group_id"]),
            actor_id=str(row["actor_id"]),
            payload=dict(json.loads(row["observation_json"])),
            occurred_at=int(row["occurred_at"]),
            status=status or str(row["status"]),
            attempt=int(row["attempt"]) + (1 if status == "processing" else 0),
            next_attempt_at=int(row["next_attempt_at"]),
            diagnostic_code=(
                str(row["diagnostic_code"])
                if row["diagnostic_code"] is not None
                else None
            ),
        )

    @staticmethod
    def _episode(row) -> ProfileEpisode:
        return ProfileEpisode(
            episode_id=str(row["episode_id"]),
            persona_id=str(row["persona_id"]),
            group_id=str(row["group_id"]),
            title=str(row["title"]),
            summary=str(row["summary"]),
            participants=tuple(json.loads(row["participants_json"])),
            source_event_ids=tuple(json.loads(row["source_event_ids_json"])),
            episode_type=str(row["episode_type"]),
            valence=float(row["valence"]),
            importance=float(row["importance"]),
            confidence=float(row["confidence"]),
            status=str(row["status"]),
            occurred_at=int(row["occurred_at"]),
            last_reinforced_at=int(row["last_reinforced_at"]),
        )

    @staticmethod
    def _edge(row) -> SocialEdge:
        return SocialEdge(
            edge_id=str(row["edge_id"]),
            persona_id=str(row["persona_id"]),
            group_id=str(row["group_id"]),
            source_member_id=str(row["source_member_id"]),
            target_member_id=str(row["target_member_id"]),
            relation_type=str(row["relation_type"]),
            direction=str(row["direction"]),
            strength=float(row["strength"]),
            confidence=float(row["confidence"]),
            source_event_ids=tuple(json.loads(row["source_event_ids_json"])),
            status=str(row["status"]),
            valid_from=int(row["valid_from"]),
            valid_until=(
                int(row["valid_until"])
                if row["valid_until"] is not None
                else None
            ),
            last_observed_at=int(row["last_observed_at"]),
        )


__all__ = ("ProfileIdentityConflict", "ProfileRepository")
