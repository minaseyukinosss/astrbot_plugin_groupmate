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
        self.continuity_items: List[Any] = []
        self.self_commitments: List[Any] = []
        self.messages: List[Any] = []
        self.social_events: List[Any] = []
        self.relationship_state: Dict[tuple, Any] = {}
        self._message_personas: Dict[tuple, str] = {}
        self._memory_personas: Dict[int, str] = {}
        self._social_event_personas: Dict[int, str] = {}

    def resolve_member_subject_id(self, persona_id, group_id, subject_id):
        del persona_id, group_id
        return str(subject_id)

    def member_subject_ids(self, persona_id, group_id, subject_id):
        del persona_id, group_id
        return (str(subject_id),)

    def member_display_name(self, persona_id, group_id, subject_id):
        del persona_id, group_id, subject_id
        return ""

    def member_name_index(self, persona_id, group_id):
        del persona_id, group_id
        return {}

    def save_message(self, persona_id, message) -> bool:
        key = (str(persona_id), message.identity)
        if key in self._message_personas:
            return False
        self._message_personas[key] = str(persona_id)
        self.messages.append(message)
        return True

    def recent_messages(self, persona_id: str, group_id: str, limit: int):
        items = [
            item
            for item in self.messages
            if item.group_id == str(group_id)
            and (str(persona_id), item.identity) in self._message_personas
        ]
        return items[-max(0, int(limit)) :]

    def search_memories(
        self,
        persona_id: str,
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
        items = [
            item
            for item in self.memories
            if item.group_id == str(group_id)
            and self._memory_personas.get(id(item)) == str(persona_id)
        ]
        if subjects:
            focused = [item for item in items if item.subject_id in subjects]
            if focused:
                items = focused
        return items[: max(0, int(limit))]

    def add_memory(self, persona_id, memory) -> None:
        self._memory_personas[id(memory)] = str(persona_id)
        self.memories.append(memory)

    def list_continuity_items(
        self,
        persona_id,
        *,
        group_id=None,
        subject_id=None,
        subject_ids=None,
        statuses=None,
        limit=100,
    ):
        del persona_id
        subjects = {str(item) for item in (subject_ids or ())}
        if subject_id is not None:
            subjects.add(str(subject_id))
        selected_statuses = {
            item.value if hasattr(item, "value") else str(item)
            for item in (statuses or ())
        }
        items = [
            item
            for item in self.continuity_items
            if (group_id is None or item.group_id == str(group_id))
            and (not subjects or item.subject_id in subjects)
            and (not selected_statuses or item.status.value in selected_statuses)
        ]
        return items[: max(0, int(limit))]

    def list_self_commitments(
        self,
        persona_id,
        *,
        group_id=None,
        beneficiary_subject_ids=None,
        statuses=None,
        limit=100,
    ):
        del persona_id
        subjects = {str(item) for item in (beneficiary_subject_ids or ())}
        selected_statuses = {
            item.value if hasattr(item, "value") else str(item)
            for item in (statuses or ())
        }
        items = [
            item
            for item in self.self_commitments
            if (group_id is None or item.group_id == str(group_id))
            and (not subjects or item.beneficiary_subject_id in subjects)
            and (not selected_statuses or item.status.value in selected_statuses)
        ]
        return items[: max(0, int(limit))]

    def next_self_commitment_attempt_at(self, persona_id):
        del persona_id
        pending = [
            int(item.next_attempt_at)
            for item in self.self_commitments
            if getattr(item, "next_attempt_at", None)
            and str(getattr(item.status, "value", item.status)) == "pending"
        ]
        return min(pending) if pending else None

    def list_memories(
        self,
        persona_id,
        group_id,
        *,
        now,
        limit=20,
        subject_id=None,
        status_accepted_only=True,
        **kwargs,
    ):
        del now, status_accepted_only, kwargs
        items = [
            item
            for item in self.memories
            if item.group_id == str(group_id)
            and self._memory_personas.get(id(item)) == str(persona_id)
            and (subject_id is None or item.subject_id == str(subject_id))
        ]
        return items[: max(0, int(limit))]

    def append_memory_candidate(self, persona_id, candidate):
        del persona_id
        return candidate

    def decide_candidate(
        self,
        persona_id,
        candidate_id,
        status,
        *,
        reason="",
        decided_at,
    ) -> None:
        del persona_id, candidate_id, status, reason, decided_at

    def accept_candidate_memory(
        self,
        persona_id,
        candidate_id,
        memory,
        *,
        reason,
        decided_at,
        superseded_memory_id=None,
    ) -> None:
        del candidate_id, reason, decided_at, superseded_memory_id
        self.add_memory(persona_id, memory)

    def has_tombstone(
        self,
        persona_id,
        group_id,
        subject_id,
        claim_hash_value,
    ) -> bool:
        del persona_id, group_id, subject_id, claim_hash_value
        return False

    def record_transition(
        self,
        persona_id: str,
        decision_id: str,
        group_id: str,
        state: str,
        reason: str,
        timestamp: int,
    ) -> None:
        del persona_id
        self.transitions.append((decision_id, group_id, state, reason, timestamp))

    def enqueue_outbox(
        self,
        persona_id: str,
        decision_id: str,
        group_id: str,
        text: str,
        created_at: int,
        expires_at: Optional[int] = None,
    ) -> bool:
        del persona_id
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

    async def enqueue_outbox_async(
        self,
        persona_id: str,
        decision_id: str,
        group_id: str,
        text: str,
        created_at: int,
        expires_at: Optional[int] = None,
        **kwargs,
    ) -> bool:
        del kwargs
        return self.enqueue_outbox(
            persona_id,
            decision_id,
            group_id,
            text,
            created_at,
            expires_at,
        )

    async def transition_outbox_async(
        self,
        persona_id: str,
        decision_id: str,
        expected: str,
        status: str,
        *,
        failure_code: str = "",
        failure_detail: str = "",
        increment_attempt: bool = False,
    ) -> bool:
        del persona_id, failure_detail
        row = self.outbox.get(decision_id)
        if row is None or row.get("status", "pending") != expected:
            return False
        row["status"] = status
        row["failure_code"] = failure_code
        if increment_attempt:
            row["attempt"] = int(row.get("attempt", 0)) + 1
        return True

    async def finalize_delivery_async(
        self,
        persona_id: str,
        decision_id: str,
        sent_at: int,
        bot_message,
        reason: str = "sent",
    ) -> bool:
        row = self.outbox.get(decision_id)
        if row is None or row.get("status") != "sending":
            return False
        row["status"] = "sent"
        row["sent_at"] = int(sent_at)
        self.save_message(persona_id, bot_message)
        self.record_transition(
            persona_id,
            decision_id,
            bot_message.group_id,
            "SEND",
            reason,
            sent_at,
        )
        self.record_transition(
            persona_id,
            decision_id,
            bot_message.group_id,
            "END",
            reason,
            sent_at,
        )
        return True

    def append_social_event(self, persona_id, event) -> bool:
        key = (
            str(persona_id),
            event.group_id,
            event.source_message_id,
            str(event.kind),
        )
        if any(
            (
                self._social_event_personas.get(id(e)),
                e.group_id,
                e.source_message_id,
                str(e.kind),
            ) == key
            for e in self.social_events
        ):
            return False
        self._social_event_personas[id(event)] = str(persona_id)
        self.social_events.append(event)
        return True

    def list_social_events(self, persona_id, group_id, user_id=None, limit=200):
        items = [
            e
            for e in self.social_events
            if self._social_event_personas.get(id(e)) == str(persona_id)
            and e.group_id == str(group_id)
            and (user_id is None or e.user_id == str(user_id))
        ]
        return items[:limit]

    def get_relationship_state(self, persona_id, group_id, user_id):
        return self.relationship_state.get(
            (str(persona_id), str(group_id), str(user_id))
        )

    def get_member_relationship_state(
        self,
        persona_id,
        group_id,
        user_id,
        *,
        configured_relationship=None,
        now=0,
    ):
        state = self.get_relationship_state(persona_id, group_id, user_id)
        if state is not None or not configured_relationship:
            return state
        from groupmate.models import RelationshipState
        from groupmate.social.affinity import initial_affinity_for_relationship

        return RelationshipState(
            group_id=str(group_id),
            user_id=str(user_id),
            affinity=initial_affinity_for_relationship(configured_relationship),
            configured_relationship=configured_relationship,
            updated_at=int(now),
        )

    def upsert_relationship_state(self, persona_id, state) -> None:
        self.relationship_state[
            (str(persona_id), state.group_id, state.user_id)
        ] = state

    def rebuild_relationship_state(
        self,
        persona_id,
        group_id,
        user_id,
        *,
        configured_relationship=None,
        seed_affinity=0,
        now=0,
    ):
        from groupmate.social.projector import SocialStateProjector

        state = SocialStateProjector().project(
            self.list_social_events(
                persona_id, group_id, user_id=user_id, limit=5000
            ),
            group_id=group_id,
            user_id=user_id,
            configured_relationship=configured_relationship,
            seed_affinity=seed_affinity,
            now=now,
        )
        self.upsert_relationship_state(persona_id, state)
        return state

    def record_social_interaction(
        self,
        persona_id,
        event,
        *,
        configured_relationship=None,
        now=0,
    ):
        from groupmate.models import RelationshipState
        from groupmate.social.affinity import initial_affinity_for_relationship
        from groupmate.social.projector import SocialStateProjector

        inserted = self.append_social_event(persona_id, event)
        current = self.get_relationship_state(
            persona_id, event.group_id, event.user_id
        )
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
        self.upsert_relationship_state(persona_id, state)
        return state


class RecordingPlatform:
    def __init__(self) -> None:
        self.sent: List[Dict[str, Any]] = []

    async def send_outbound(
        self,
        group_id: str,
        outbound,
        decision_id: str,
        quote_message_id: Optional[str] = None,
    ):
        from groupmate.models import OutboundKind, SendResult

        for segment in outbound:
            if segment.kind is not OutboundKind.TEXT:
                continue
            self.sent.append(
                {
                    "group_id": str(group_id),
                    "text": segment.text,
                    "decision_id": str(decision_id),
                    "quote_message_id": quote_message_id,
                }
            )
        return SendResult.confirmed(len(tuple(outbound)))


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
