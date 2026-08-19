"""Privacy-trimmed resumable SSE feed over Projection events."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..persistence.schema import connect_database
from .projections import ProjectionConsumer


@dataclass(frozen=True)
class StreamBatch:
    events: tuple[dict[str, object], ...]
    latest_cursor: int
    snapshot_required: bool


class ProjectionStream:
    def __init__(self, path: Path, *, retention: int = 2_000) -> None:
        if int(retention) < 1:
            raise ValueError("SSE retention must be positive")
        self.path = Path(path)
        self.retention = int(retention)
        ProjectionConsumer(self.path, "health")

    def read(
        self,
        *,
        last_event_id: str | None,
        persona_id: str,
        group_id: str,
        limit: int = 100,
    ) -> StreamBatch:
        if int(limit) < 1 or int(limit) > 500:
            raise ValueError("SSE limit must be between 1 and 500")
        persona = str(persona_id).strip()
        group = str(group_id).strip()
        if not persona or not group:
            raise ValueError("SSE requires persona and group scope")
        cursor = self._parse_cursor(last_event_id)
        db = connect_database(self.path)
        try:
            db.execute("BEGIN IMMEDIATE")
            latest = int(
                db.execute(
                    "SELECT COALESCE(MAX(stream_cursor), 0) "
                    "FROM control_projection_events"
                ).fetchone()[0]
            )
            cutoff = max(0, latest - self.retention)
            if cutoff:
                db.execute(
                    "DELETE FROM control_projection_events WHERE stream_cursor<=?",
                    (cutoff,),
                )
            minimum = db.execute(
                "SELECT MIN(stream_cursor) FROM control_projection_events"
            ).fetchone()[0]
            minimum = None if minimum is None else int(minimum)
            if (
                last_event_id is not None
                and minimum is not None
                and cursor < minimum - 1
            ):
                event = self._snapshot_required(latest, persona, group)
                db.commit()
                return StreamBatch((event,), latest, True)
            rows = db.execute(
                "SELECT stream_cursor, kind, entity_ref, projection_version, "
                "summary_json FROM control_projection_events "
                "WHERE stream_cursor>? AND persona_id=? AND group_id=? "
                "ORDER BY stream_cursor LIMIT ?",
                (cursor, persona, group, int(limit)),
            ).fetchall()
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()
        events = tuple(
            {
                "cursor": int(row["stream_cursor"]),
                "kind": str(row["kind"]),
                "scope": {"persona_id": persona, "group_id": group},
                "entity": str(row["entity_ref"]),
                "projection_version": int(row["projection_version"]),
                "summary": json.loads(str(row["summary_json"])),
            }
            for row in rows
        )
        return StreamBatch(events, latest, False)

    @staticmethod
    def encode(batch: StreamBatch) -> str:
        chunks = []
        for event in batch.events:
            event_name = (
                "snapshot_required"
                if event["kind"] == "snapshot_required"
                else "projection"
            )
            chunks.append(
                f"id: {event['cursor']}\n"
                f"event: {event_name}\n"
                "data: "
                + json.dumps(
                    event,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n\n"
            )
        return "".join(chunks) or ": keep-alive\n\n"

    @staticmethod
    def _snapshot_required(
        latest: int, persona_id: str, group_id: str
    ) -> dict[str, object]:
        return {
            "cursor": latest,
            "kind": "snapshot_required",
            "scope": {"persona_id": persona_id, "group_id": group_id},
            "entity": None,
            "projection_version": 0,
            "summary": {"reason": "cursor_expired"},
        }

    @staticmethod
    def _parse_cursor(value: str | None) -> int:
        if value is None or not str(value).strip():
            return 0
        try:
            cursor = int(str(value))
        except ValueError as exc:
            raise ValueError("Last-Event-ID must be an integer") from exc
        if cursor < 0:
            raise ValueError("Last-Event-ID must not be negative")
        return cursor


__all__ = ("ProjectionStream", "StreamBatch")
