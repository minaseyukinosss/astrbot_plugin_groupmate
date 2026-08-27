"""Scope-checked queries over privacy-trimmed control-plane read models."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from ..persona.profile import GroupmatePersonaProfile
from ..persistence.schema import connect_database
from ..profile.repository import ProfileRepository
from ..profile.speech_style import MemberStyleMaturity
from ..profile.style_repository import MemberStyleRepository
from .config_versions import ConfigVersionRepository
from .message_traces import MessageTraceRepository
from .projections import ProjectionConsumer


class ProjectionQueries:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def bootstrap(self, *, persona_id: str, group_id: str) -> dict[str, object]:
        views = [
            self._query(name, persona_id=persona_id, group_id=group_id)
            for name in ProjectionConsumer.PROJECTION_NAMES
        ]
        return {
            "projection": "bootstrap",
            "as_of": max(
                (view["as_of"] for view in views if view["as_of"] is not None),
                default=None,
            ),
            "cursor": min((int(view["cursor"]) for view in views), default=0),
            "projection_version": max(
                (int(view["projection_version"]) for view in views), default=0
            ),
            "stale": any(bool(view["stale"]) for view in views),
            "items": [
                {
                    "projection": view["projection"],
                    "as_of": view["as_of"],
                    "cursor": view["cursor"],
                    "projection_version": view["projection_version"],
                    "stale": view["stale"],
                }
                for view in views
            ],
        }

    def runtime(self, *, persona_id: str, group_id: str) -> dict[str, object]:
        return self._query("runtime", persona_id=persona_id, group_id=group_id)

    def traces(
        self,
        *,
        persona_id: str,
        group_id: str,
        limit: int = 100,
        before: str | None = None,
    ) -> dict[str, object]:
        return MessageTraceRepository(self.path).query(
            persona_id=persona_id,
            group_id=group_id,
            limit=limit,
            before=before,
        )

    def activity(self, *, persona_id: str, group_id: str) -> dict[str, object]:
        return self._query("activity", persona_id=persona_id, group_id=group_id)

    def scenes(self, *, persona_id: str, group_id: str) -> dict[str, object]:
        return self._query("scenes", persona_id=persona_id, group_id=group_id)

    def people(self, *, persona_id: str, group_id: str) -> dict[str, object]:
        return self._query("people", persona_id=persona_id, group_id=group_id)

    def culture(self, *, persona_id: str, group_id: str) -> dict[str, object]:
        return self._query("culture", persona_id=persona_id, group_id=group_id)

    def tasks(self, *, persona_id: str, group_id: str) -> dict[str, object]:
        return self._query("tasks", persona_id=persona_id, group_id=group_id)

    def persona(self, *, persona_id: str, group_id: str) -> dict[str, object]:
        view = self._query("persona", persona_id=persona_id, group_id=group_id)
        snapshot = ConfigVersionRepository(self.path).snapshot(
            persona_id=persona_id,
            group_id=group_id,
        )
        profile = GroupmatePersonaProfile.from_behavior_config(snapshot.config)
        view["items"].append(
            {
                "entity_ref": "persona:profile",
                "kind": "persona.profile",
                "projection_version": snapshot.version,
                "summary": {
                    "config_version": snapshot.version,
                    "profile": profile.to_mapping(),
                },
                "evidence_refs": [],
                "as_of": view["as_of"],
            }
        )
        return view

    def governance(self, *, persona_id: str, group_id: str) -> dict[str, object]:
        return self._query("governance", persona_id=persona_id, group_id=group_id)

    def evaluation(self, *, persona_id: str, group_id: str) -> dict[str, object]:
        return self._query("evaluation", persona_id=persona_id, group_id=group_id)

    def health(self, *, persona_id: str, group_id: str) -> dict[str, object]:
        return self._query("health", persona_id=persona_id, group_id=group_id)

    def profiles(self, *, persona_id: str, group_id: str) -> dict[str, object]:
        persona, group = self._scope(persona_id, group_id)
        repository = ProfileRepository(self.path)
        with connect_database(self.path) as db:
            members = db.execute(
                "SELECT member_ref,display_name,avatar_ref,actor_id,updated_at "
                "FROM participant_directory WHERE persona_id=? AND group_id=? "
                "ORDER BY updated_at DESC,display_name,member_ref",
                (persona, group),
            ).fetchall()
        items: list[dict[str, object]] = []
        as_of: int | None = None
        for row in members:
            actor_id = str(row["actor_id"])
            snapshot = repository.snapshot(persona, group, actor_id)
            facts = repository.facts(persona, group, actor_id)
            episodes = repository.episodes(persona, group, actor_id)
            relations = repository.edges(persona, group, actor_id)
            updated_at = max(
                int(row["updated_at"]),
                int(snapshot.generated_at) if snapshot is not None else 0,
            )
            as_of = max(as_of or 0, updated_at)
            items.append(
                {
                    "entity_ref": f"profiles:{row['member_ref']}",
                    "kind": "member.profile",
                    "projection_version": (
                        int(snapshot.source_revision) if snapshot is not None else 0
                    ),
                    "summary": {
                        "member_ref": str(row["member_ref"]),
                        "display_name": str(row["display_name"]),
                        "avatar_ref": str(row["avatar_ref"]),
                        "one_line_portrait": (
                            snapshot.one_line_portrait
                            if snapshot is not None
                            else "画像正在形成"
                        ),
                        "maturity": snapshot.maturity if snapshot is not None else "new",
                        "group_roles": list(snapshot.group_roles) if snapshot is not None else [],
                        "fact_count": len(facts),
                        "episode_count": len(episodes),
                        "relation_count": len(relations),
                        "personalization_enabled": repository.personalization_enabled(
                            persona, group, actor_id
                        ),
                        "updated_at": updated_at,
                    },
                    "evidence_refs": [],
                    "as_of": updated_at,
                }
            )
        items.sort(
            key=lambda item: (
                -int(item["projection_version"]),
                -int(item["summary"]["updated_at"]),
                str(item["summary"]["display_name"]),
            )
        )
        return self._direct_view("profiles", items, as_of=as_of)

    def profile(
        self, *, persona_id: str, group_id: str, member_ref: str
    ) -> dict[str, object]:
        persona, group = self._scope(persona_id, group_id)
        normalized_ref = str(member_ref or "").strip()
        with connect_database(self.path) as db:
            member = db.execute(
                "SELECT member_ref,display_name,avatar_ref,actor_id,updated_at "
                "FROM participant_directory WHERE persona_id=? AND group_id=? "
                "AND member_ref=?",
                (persona, group, normalized_ref),
            ).fetchone()
            if member is None:
                raise LookupError("profile member not found")
            directory_rows = db.execute(
                "SELECT actor_id,member_ref,display_name,avatar_ref "
                "FROM participant_directory WHERE persona_id=? AND group_id=?",
                (persona, group),
            ).fetchall()
            audit_rows = db.execute(
                "SELECT action_type,created_at FROM profile_audit "
                "WHERE persona_id=? AND group_id=? AND subject_id=? "
                "ORDER BY created_at DESC LIMIT 30",
                (persona, group, str(member["actor_id"])),
            ).fetchall()
        directory = {
            str(row["actor_id"]): {
                "member_ref": str(row["member_ref"]),
                "display_name": str(row["display_name"]),
                "avatar_ref": str(row["avatar_ref"]),
            }
            for row in directory_rows
        }
        actor_id = str(member["actor_id"])
        repository = ProfileRepository(self.path)
        snapshot = repository.snapshot(persona, group, actor_id)
        facts = repository.facts(persona, group, actor_id)
        episodes = repository.episodes(persona, group, actor_id)
        edges = repository.edges(persona, group, actor_id)
        relations: list[dict[str, object]] = []
        for edge in edges:
            other_id = (
                edge.target_member_id
                if edge.source_member_id == actor_id
                else edge.source_member_id
            )
            other = directory.get(other_id)
            if other is None:
                continue
            relations.append(
                {
                    "other_member_ref": other["member_ref"],
                    "other_display_name": other["display_name"],
                    "other_avatar_ref": other["avatar_ref"],
                    "relation_type": edge.relation_type,
                    "direction": edge.direction,
                    "strength": edge.strength,
                    "confidence": edge.confidence,
                    "status": edge.status,
                    "last_observed_at": edge.last_observed_at,
                }
            )
        snapshot_summary = (
            {
                "one_line_portrait": snapshot.one_line_portrait,
                "group_roles": list(snapshot.group_roles),
                "individual_fingerprints": list(snapshot.individual_fingerprints),
                "preferences_and_boundaries": list(
                    snapshot.preferences_and_boundaries
                ),
                "relationship_summary": snapshot.relationship_summary,
                "maturity": snapshot.maturity,
                "source_revision": snapshot.source_revision,
                "generated_at": snapshot.generated_at,
            }
            if snapshot is not None
            else {
                "one_line_portrait": "画像正在形成",
                "group_roles": [],
                "individual_fingerprints": [],
                "preferences_and_boundaries": [],
                "relationship_summary": "关系认知正在积累",
                "maturity": "new",
                "source_revision": 0,
                "generated_at": int(member["updated_at"]),
            }
        )
        as_of = max(int(member["updated_at"]), int(snapshot_summary["generated_at"]))
        summary = {
            "member": {
                "member_ref": str(member["member_ref"]),
                "display_name": str(member["display_name"]),
                "avatar_ref": str(member["avatar_ref"]),
            },
            "snapshot": snapshot_summary,
            "facts": [
                {
                    "fact_ref": fact.fact_id,
                    "category": fact.category,
                    "summary": fact.summary,
                    "source_kind": fact.source_kind,
                    "confidence": fact.confidence,
                    "status": fact.status,
                    "evidence_count": fact.evidence_count,
                    "valid_from": fact.valid_from,
                    "valid_until": fact.valid_until,
                    "injectable": fact.injectable,
                }
                for fact in facts
            ],
            "episodes": [
                {
                    "episode_ref": episode.episode_id,
                    "title": episode.title,
                    "summary": episode.summary,
                    "episode_type": episode.episode_type,
                    "valence": episode.valence,
                    "importance": episode.importance,
                    "confidence": episode.confidence,
                    "status": episode.status,
                    "occurred_at": episode.occurred_at,
                    "last_reinforced_at": episode.last_reinforced_at,
                }
                for episode in episodes
            ],
            "relations": relations,
            "audit": [
                {
                    "action_type": str(row["action_type"]),
                    "created_at": int(row["created_at"]),
                }
                for row in audit_rows
            ],
            "personalization_enabled": repository.personalization_enabled(
                persona, group, actor_id
            ),
            "profile_revision": int(snapshot_summary["source_revision"]),
            "speech_style": self._speech_style_summary(group, actor_id),
        }
        return self._direct_view(
            "profile",
            [
                {
                    "entity_ref": f"profile:{member['member_ref']}",
                    "kind": "member.profile.detail",
                    "projection_version": int(snapshot_summary["source_revision"]),
                    "summary": summary,
                    "evidence_refs": [],
                    "as_of": as_of,
                }
            ],
            as_of=as_of,
        )

    def _speech_style_summary(
        self, group_id: str, member_id: str
    ) -> dict[str, object]:
        repository = MemberStyleRepository(self.path)
        setting = repository.setting(group_id, member_id)
        evidence = repository.eligible_observations(group_id, member_id)
        maturity = MemberStyleMaturity.from_evidence(evidence)
        style = repository.latest_ready(group_id, member_id)
        return {
            "enabled": setting.enabled,
            "setting_version": setting.version,
            "status": (
                "DISABLED"
                if not setting.enabled
                else "READY"
                if style is not None
                else "ACCUMULATING"
            ),
            "eligible_message_count": (
                style.eligible_message_count
                if style is not None
                else maturity.eligible_message_count
            ),
            "active_day_count": (
                style.active_day_count if style is not None else maturity.active_day_count
            ),
            "scene_types": list(
                style.scene_types if style is not None else maturity.scene_types
            ),
            "style_version": style.version if style is not None else None,
            "generated_at": style.generated_at if style is not None else None,
            "opening_patterns": list(style.opening_patterns) if style else [],
            "progression_patterns": list(style.progression_patterns) if style else [],
            "closing_patterns": list(style.closing_patterns) if style else [],
            "stable_traits": list(style.stable_traits) if style else [],
        }

    def group_portrait(
        self, *, persona_id: str, group_id: str
    ) -> dict[str, object]:
        persona, group = self._scope(persona_id, group_id)
        portrait = ProfileRepository(self.path).group_portrait(persona, group)
        if portrait is None:
            return self._direct_view("group-portrait", [], as_of=None)
        summary = asdict(portrait)
        summary["common_topics"] = list(portrait.common_topics)
        return self._direct_view(
            "group-portrait",
            [
                {
                    "entity_ref": "group-portrait:current",
                    "kind": "group.profile",
                    "projection_version": portrait.source_revision,
                    "summary": summary,
                    "evidence_refs": [],
                    "as_of": portrait.generated_at,
                }
            ],
            as_of=portrait.generated_at,
        )

    @staticmethod
    def _scope(persona_id: str, group_id: str) -> tuple[str, str]:
        persona = str(persona_id).strip()
        group = str(group_id).strip()
        if not persona or not group:
            raise ValueError("profile query requires persona and group scope")
        return persona, group

    @staticmethod
    def _direct_view(
        name: str, items: list[dict[str, object]], *, as_of: int | None
    ) -> dict[str, object]:
        version = max(
            (int(item.get("projection_version") or 0) for item in items),
            default=0,
        )
        return {
            "projection": name,
            "as_of": as_of,
            "cursor": version,
            "projection_version": version,
            "stale": False,
            "items": items,
        }

    def _query(
        self, name: str, *, persona_id: str, group_id: str
    ) -> dict[str, object]:
        persona = str(persona_id).strip()
        group = str(group_id).strip()
        if not persona or not group:
            raise ValueError("projection query requires persona and group scope")
        with connect_database(self.path) as db:
            cursor_row = db.execute(
                "SELECT last_journal_rowid, version, updated_at "
                "FROM projection_cursors WHERE projection_name=?",
                (name,),
            ).fetchone()
            source_head = int(
                db.execute(
                    "SELECT last_journal_rowid FROM control_projection_source "
                    "WHERE singleton=1"
                ).fetchone()[0]
            )
            rows = db.execute(
                "SELECT entity_ref, kind, projection_version, summary_json, "
                "evidence_refs_json, as_of FROM control_projection_items "
                "WHERE projection_name=? AND persona_id=? AND group_id=? "
                "ORDER BY projection_version, entity_ref",
                (name, persona, group),
            ).fetchall()
        cursor = int(cursor_row[0]) if cursor_row is not None else 0
        version = int(cursor_row[1]) if cursor_row is not None else 0
        as_of = int(cursor_row[2]) if cursor_row is not None else None
        return {
            "projection": name,
            "as_of": as_of,
            "cursor": cursor,
            "projection_version": version,
            "stale": cursor < source_head,
            "items": [
                {
                    "entity_ref": str(row["entity_ref"]),
                    "kind": str(row["kind"]),
                    "projection_version": int(row["projection_version"]),
                    "summary": json.loads(str(row["summary_json"])),
                    "evidence_refs": json.loads(str(row["evidence_refs_json"])),
                    "as_of": int(row["as_of"]),
                }
                for row in rows
            ],
        }


__all__ = ("ProjectionQueries",)
