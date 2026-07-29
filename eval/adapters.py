"""Side-effect-free adapters used by the offline evaluation runner."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence


class FixedClock:
    def __init__(self, now: int) -> None:
        self.value = int(now)

    def now(self) -> int:
        return self.value


class InMemoryRepository:
    def __init__(self) -> None:
        self.transitions: List[tuple] = []
        self.outbox: Dict[str, Dict[str, Any]] = {}
        self.memories: List[Any] = []
        self.messages: List[Any] = []
        self.social_events: List[Any] = []
        self.relationship_state: Dict[tuple, Any] = {}

    def save_message(self, message) -> bool:
        if any(item.identity == message.identity for item in self.messages):
            return False
        self.messages.append(message)
        return True

    def recent_messages(self, group_id: str, limit: int):
        items = [item for item in self.messages if item.group_id == str(group_id)]
        return items[-max(0, int(limit)) :]

    def search_memories(
        self,
        group_id: str,
        query: str,
        now: int,
        limit: int,
        subject_id: Optional[str] = None,
        subject_ids: Optional[Sequence[str]] = None,
        include_user_in_group: bool = True,
    ):
        del query, now, include_user_in_group
        subjects = {str(item) for item in (subject_ids or ())}
        if subject_id:
            subjects.add(str(subject_id))
        items = [item for item in self.memories if item.group_id == str(group_id)]
        if subjects:
            focused = [item for item in items if item.subject_id in subjects]
            if focused:
                items = focused
        return items[: max(0, int(limit))]

    def add_memory(self, memory) -> None:
        self.memories.append(memory)

    def record_transition(
        self,
        decision_id: str,
        group_id: str,
        state: str,
        reason: str,
        timestamp: int,
    ) -> None:
        self.transitions.append((decision_id, group_id, state, reason, timestamp))

    def enqueue_outbox(
        self,
        decision_id: str,
        group_id: str,
        text: str,
        created_at: int,
        expires_at: Optional[int] = None,
    ) -> bool:
        if decision_id in self.outbox:
            return False
        self.outbox[decision_id] = {
            "group_id": group_id,
            "text": text,
            "created_at": created_at,
            "expires_at": expires_at,
            "sent_at": None,
        }
        return True

    def mark_outbox_sent(self, decision_id: str, sent_at: int) -> None:
        self.outbox[decision_id]["sent_at"] = int(sent_at)

    def append_social_event(self, event) -> bool:
        key = (event.group_id, event.source_message_id, str(event.kind))
        if any(
            (e.group_id, e.source_message_id, str(e.kind)) == key
            for e in self.social_events
        ):
            return False
        self.social_events.append(event)
        return True

    def list_social_events(self, group_id, user_id=None, limit=200):
        items = [
            e
            for e in self.social_events
            if e.group_id == str(group_id)
            and (user_id is None or e.user_id == str(user_id))
        ]
        return items[:limit]

    def get_relationship_state(self, group_id, user_id):
        return self.relationship_state.get((str(group_id), str(user_id)))

    def upsert_relationship_state(self, state) -> None:
        self.relationship_state[(state.group_id, state.user_id)] = state

    def rebuild_relationship_state(
        self,
        group_id,
        user_id,
        *,
        configured_relationship=None,
        seed_affinity=0,
        now=0,
    ):
        from groupmate.social.projector import SocialStateProjector

        state = SocialStateProjector().project(
            self.list_social_events(group_id, user_id=user_id, limit=5000),
            group_id=group_id,
            user_id=user_id,
            configured_relationship=configured_relationship,
            seed_affinity=seed_affinity,
            now=now,
        )
        self.upsert_relationship_state(state)
        return state

    def record_social_interaction(
        self,
        event,
        *,
        configured_relationship=None,
        now=0,
    ):
        from groupmate.models import RelationshipState
        from groupmate.social.affinity import initial_affinity_for_relationship
        from groupmate.social.projector import SocialStateProjector

        inserted = self.append_social_event(event)
        current = self.get_relationship_state(event.group_id, event.user_id)
        if current is None:
            current = RelationshipState(
                group_id=event.group_id,
                user_id=event.user_id,
                affinity=initial_affinity_for_relationship(
                    configured_relationship or ""
                ),
                configured_relationship=configured_relationship,
                updated_at=int(now or event.occurred_at),
            )
        if not inserted:
            return current
        state = SocialStateProjector().apply_event(
            current,
            event,
            configured_relationship=configured_relationship,
            now=int(now or event.occurred_at),
        )
        self.upsert_relationship_state(state)
        return state


class RecordingPlatform:
    def __init__(self) -> None:
        self.sent: List[Dict[str, Any]] = []

    async def send_text(self, group_id: str, text: str, decision_id: str) -> None:
        self.sent.append(
            {
                "group_id": str(group_id),
                "text": str(text),
                "decision_id": str(decision_id),
                "quote_message_id": None,
            }
        )
        from groupmate.models import SendResult

        return SendResult.confirmed()

    async def send_segments(
        self,
        group_id: str,
        segments,
        decision_id: str,
        quote_message_id: Optional[str] = None,
    ):
        for segment in segments:
            self.sent.append(
                {
                    "group_id": str(group_id),
                    "text": str(segment),
                    "decision_id": str(decision_id),
                    "quote_message_id": quote_message_id,
                }
            )
        from groupmate.models import SendResult

        return SendResult.confirmed(len(tuple(segments)))


class NullVision:
    async def describe(self, image_urls) -> str:
        del image_urls
        return ""


class ScriptedGenerationModel:
    def __init__(self, output: str, repair_output: Optional[str] = None) -> None:
        self.output = output
        self.repair_output = repair_output
        self.plans: List[Any] = []
        self.repairs: List[Dict[str, Any]] = []

    async def generate(self, plan, topic, memories) -> str:
        del topic, memories
        self.plans.append(plan)
        return self.output

    async def repair(self, text: str, violations) -> str:
        self.repairs.append({"text": text, "violations": tuple(violations)})
        return self.repair_output if self.repair_output is not None else text
