"""Read-only dialogue facts from frozen inbox events and confirmed outbox parts.

Nothing here appends an event, advances a lease or authorizes a reply.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path

from .contracts import SocialEventEnvelope
from .social_context import MAX_CONTEXT_EVENTS, SceneContextBuilder, SceneEventFact


# Session gap used to bound background retrieval, never lease lifetime or
# reply validity.
DIALOGUE_SESSION_GAP_SECONDS = 300


class DialogueContextReader:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def read(
        self, persona_id: str, group_id: str,
        events: tuple[SocialEventEnvelope, ...], focus_event_ids: tuple[str, ...],
        *, required_context_event_ids: tuple[str, ...] = (),
    ) -> tuple[SocialEventEnvelope, ...]:
        # Only receipts already present in the frozen world are eligible. A later
        # outbox completion cannot leak into an earlier evaluation on replay.
        scoped = tuple(event for event in events
                       if event.persona_id == persona_id and event.group_id == group_id)
        turns = []
        with closing(sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            for event in scoped:
                if event.event_type == "delivery.sent":
                    turn = self._sent_turn(db, event)
                    if turn is not None:
                        turns.append(turn)
                elif (event.event_type == "platform.message"
                      and event.payload.get("social_eligible") is not False
                      and not event.payload.get("is_self")):
                    turns.append(event)
        # Confirmed own output is canonical, even if the platform echoed it.
        own_ids = {event.source_message_id for event in turns if event.payload.get("is_self")}
        turns = [event for event in turns if event.payload.get("is_self")
                 or event.source_message_id not in own_ids]
        by_id = {event.event_id: event for event in turns}
        aliases = {event.source_message_id: event.event_id for event in turns if event.source_message_id}
        aliases.update({f"qq:{key}": value for key, value in tuple(aliases.items())})
        normalized = []
        for event in by_id.values():
            parent = SceneEventFact.from_event(event).reply_to
            normalized.append(replace(event, payload={
                **dict(event.payload), "reply_to_event_id": aliases.get(parent, parent),
            }))
        targets = {event.actor_id for event in normalized if event.event_id in focus_event_ids}
        parents = {SceneEventFact.from_event(event).reply_to for event in normalized
                   if event.event_id in focus_event_ids}
        newest = max((event.occurred_at for event in normalized if event.event_id in focus_event_ids), default=0)
        session_ids = set(focus_event_ids) | set(required_context_event_ids)
        current_session_ids = set()
        previous_at = newest
        for event in sorted(normalized, key=lambda item: item.occurred_at, reverse=True):
            if event.occurred_at > newest:
                continue
            if previous_at - event.occurred_at > DIALOGUE_SESSION_GAP_SECONDS:
                break
            session_ids.add(event.event_id)
            current_session_ids.add(event.event_id)
            previous_at = event.occurred_at
        normalized = [event for event in normalized
                      if event.event_id in session_ids or event.event_id in parents]
        bot_turns = {event.event_id for event in normalized if event.payload.get("is_self")
                     and (event.payload.get("target_id") in targets or event.event_id in parents)}
        parents.update(SceneEventFact.from_event(event).reply_to for event in normalized
                       if event.event_id in bot_turns)
        def priority(event):
            rank = (0 if event.event_id in focus_event_ids or event.event_id in required_context_event_ids else
                    1 if event.event_id in parents or event.event_id in bot_turns else
                    2 if event.actor_id in targets else 3)
            return rank, -event.occurred_at

        ranked = sorted(normalized, key=priority)[:MAX_CONTEXT_EVENTS]
        budget = SceneContextBuilder().max_chars
        selected = []
        for index, event in enumerate(ranked):
            # Reserve meaningful space for remaining turns instead of allowing a
            # single long message to evict both halves of a recent exchange.
            allowance = max(0, min(800, budget - 80 * (len(ranked) - index - 1)))
            text = str(event.payload.get("text") or "")[:allowance]
            budget -= len(text)
            selected.append(replace(event, payload={
                **dict(event.payload), "text": text,
                "current_dialogue_session": event.event_id in current_session_ids,
            }))
        # Stable ordering preserves inbox order when timestamps have second precision.
        order = {event_id: index for index, event_id in enumerate(dict.fromkeys(event.event_id for event in scoped))}
        return tuple(sorted(selected, key=lambda event: (event.occurred_at, order[event.event_id])))

    @staticmethod
    def _sent_turn(db, event: SocialEventEnvelope) -> SocialEventEnvelope | None:
        row = db.execute(
            "SELECT payload_json, receipt_json FROM outbox WHERE part_id=? "
            "AND persona_id=? AND group_id=? AND status='sent'",
            (event.payload.get("part_id"), event.persona_id, event.group_id),
        ).fetchone()
        if row is None or not row["receipt_json"]:
            return None
        part, receipt = json.loads(row["payload_json"]), json.loads(row["receipt_json"])
        payload = part.get("payload") or {}
        if (part.get("kind") != "text" or not payload.get("text")
                or receipt.get("receipt_id") != event.payload.get("receipt_id")
                or not receipt.get("platform_message_id")):
            return None
        plan_id = str(part.get("part_id", "")).removeprefix("reply-part:")
        plan_row = (db.execute("SELECT plan_json FROM reply_plans WHERE plan_id=?", (plan_id,)).fetchone()
                    if str(part.get("part_id", "")).startswith("reply-part:") else None)
        plan = json.loads(plan_row[0]) if plan_row else {}
        evidence = plan.get("evidence_event_ids") or []
        return replace(
            event, actor_id=str(payload.get("self_id") or plan.get("bot_id") or event.persona_id),
            source_message_id=str(receipt["platform_message_id"]),
            occurred_at=int(receipt["occurred_at"]),
            payload={"text": str(payload["text"]), "is_self": True, "origin_kind": "BOT_TEXT",
                     "delivery_confirmed": True,
                     "target_id": plan.get("target_id"),
                     "reply_to_event_id": plan.get("anchor_event_id") or (evidence[0] if evidence else None)},
        )


def dialogue_messages(events: tuple[SocialEventEnvelope, ...]) -> list[dict[str, object]]:
    """Use the same bounded facts for ordinary and grounded generation."""
    return [
        {"event_id": event.event_id, "actor_id": event.actor_id,
         "text": str(event.payload.get("text") or ""), "occurred_at": event.occurred_at,
         "reply_to": SceneEventFact.from_event(event).reply_to,
         "is_self": bool(event.payload.get("is_self")),
         "origin_kind": event.payload.get("origin_kind", "USER_TEXT")}
        for event in events if str(event.payload.get("text") or "").strip()
    ]


def continue_from_event_id(
    events: tuple[SocialEventEnvelope, ...],
    *,
    target_id: str | None,
    anchor_event_id: str | None,
    planned_id: str | None = None,
) -> str | None:
    """Pick the verified own turn the current member is answering.

    Prefer an already verified planned ID. Otherwise take the latest is_self
    message addressed to the current target that is not later than the anchor.
    Never guess a later turn spoken to someone else.
    """
    planned = str(planned_id or "").strip() or None
    if planned and planned != anchor_event_id:
        if not events:
            return planned
        chosen = next((event for event in events if event.event_id == planned), None)
        if (
            chosen is not None
            and chosen.payload.get("is_self")
            and str(chosen.payload.get("text") or "").strip()
        ):
            return planned
    if not target_id:
        return None
    by_id = {event.event_id: event for event in events}
    anchor = by_id.get(anchor_event_id) if anchor_event_id else None
    latest = None
    for event in events:
        if event.event_id == anchor_event_id:
            continue
        if not event.payload.get("is_self"):
            continue
        if not str(event.payload.get("text") or "").strip():
            continue
        if anchor is not None and event.occurred_at >= anchor.occurred_at:
            continue
        if not _addressed_to(event, target_id, by_id):
            continue
        if latest is None or (event.occurred_at, event.event_id) > (
            latest.occurred_at, latest.event_id
        ):
            latest = event
    return None if latest is None else latest.event_id


def _addressed_to(
    event: SocialEventEnvelope, target_id: str,
    by_id: dict[str, SocialEventEnvelope],
) -> bool:
    payload = event.payload
    if payload.get("target_id") == target_id or payload.get("reply_to_actor_id") == target_id:
        return True
    parent_id = SceneEventFact.from_event(event).reply_to
    parent = by_id.get(parent_id) if parent_id else None
    return parent is not None and parent.actor_id == target_id


__all__ = (
    "DIALOGUE_SESSION_GAP_SECONDS",
    "DialogueContextReader",
    "continue_from_event_id",
    "dialogue_messages",
)
