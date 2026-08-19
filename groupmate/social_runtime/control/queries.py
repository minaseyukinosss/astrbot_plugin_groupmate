"""Scope-checked queries over privacy-trimmed control-plane read models."""

from __future__ import annotations

import json
from pathlib import Path

from ..persistence.schema import connect_database
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
        return self._query("persona", persona_id=persona_id, group_id=group_id)

    def governance(self, *, persona_id: str, group_id: str) -> dict[str, object]:
        return self._query("governance", persona_id=persona_id, group_id=group_id)

    def evaluation(self, *, persona_id: str, group_id: str) -> dict[str, object]:
        return self._query("evaluation", persona_id=persona_id, group_id=group_id)

    def health(self, *, persona_id: str, group_id: str) -> dict[str, object]:
        return self._query("health", persona_id=persona_id, group_id=group_id)

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
