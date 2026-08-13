class FakeClock:
    def __init__(self, now=101):
        self.value = now

    def now(self):
        return self.value


class FakeMemoryRepository:
    def __init__(self):
        self.transitions = []
        self.outbox = {}
        self.memories = []
        self.continuity_items = []
        self.self_commitments = []
        self.messages = []
        self.social_events = []
        self.relationship_state = {}
        self._message_personas = set()
        self._social_event_personas = {}
        self._topic_epochs = {}
        self.continuation_grants = []
        self.member_links = {}
        self.member_names = {}
        self.member_aliases = {}

    def resolve_member_subject_id(self, persona_id, group_id, subject_id):
        del persona_id
        return self.member_links.get((str(group_id), str(subject_id)), str(subject_id))

    def member_subject_ids(self, persona_id, group_id, subject_id):
        del persona_id
        canonical = self.resolve_member_subject_id("", group_id, subject_id)
        aliases = [
            source
            for (linked_group, source), target in self.member_links.items()
            if linked_group == str(group_id) and target == canonical
        ]
        return tuple(dict.fromkeys([canonical, *aliases]))

    def member_display_name(self, persona_id, group_id, subject_id):
        del persona_id
        canonical = self.resolve_member_subject_id("", group_id, subject_id)
        return self.member_names.get((str(group_id), canonical), "")

    def member_name_index(self, persona_id, group_id):
        del persona_id
        return {
            name: subject_id
            for (linked_group, name), subject_id in self.member_aliases.items()
            if linked_group == str(group_id)
        }

    def get_member_relationship_state(
        self,
        persona_id,
        group_id,
        user_id,
        *,
        configured_relationship=None,
        now=0,
    ):
        del now
        canonical = self.resolve_member_subject_id(persona_id, group_id, user_id)
        state = self.get_relationship_state(persona_id, group_id, canonical)
        if state is None and configured_relationship:
            from groupmate.models import RelationshipState
            from groupmate.social.affinity import initial_affinity_for_relationship

            return RelationshipState(
                group_id=str(group_id),
                user_id=canonical,
                affinity=initial_affinity_for_relationship(configured_relationship),
                configured_relationship=configured_relationship,
            )
        return state

    def save_message(self, persona_id, message):
        key = (str(persona_id), message.identity)
        if key in self._message_personas:
            return False
        self._message_personas.add(key)
        self.messages.append(message)
        return True

    async def save_message_async(self, persona_id, message):
        return self.save_message(persona_id, message)

    async def flush_async(self):
        return None

    def search_memories(
        self,
        persona_id,
        group_id,
        query,
        now,
        limit,
        subject_id=None,
        subject_ids=None,
        include_user_in_group=True,
    ):
        del persona_id, group_id, query, now, subject_id, subject_ids, include_user_in_group
        return list(self.memories)[:limit]

    def add_memory(self, persona_id, memory):
        del persona_id
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
        selected_subjects = set(subject_ids or ())
        if subject_id is not None:
            selected_subjects.add(str(subject_id))
        selected_statuses = {
            item.value if hasattr(item, "value") else str(item)
            for item in (statuses or ())
        }
        items = [
            item
            for item in self.continuity_items
            if (group_id is None or item.group_id == str(group_id))
            and (not selected_subjects or item.subject_id in selected_subjects)
            and (not selected_statuses or item.status.value in selected_statuses)
        ]
        return items[:limit]

    def resolve_continuity_item(
        self,
        persona_id,
        item_id,
        *,
        status,
        resolution_message_id="",
        resolution_quote="",
        resolved_at=0,
    ):
        del persona_id
        from dataclasses import replace

        from groupmate.models import ContinuityStatus

        next_status = (
            status
            if isinstance(status, ContinuityStatus)
            else ContinuityStatus(str(status))
        )
        for index, item in enumerate(self.continuity_items):
            if item.item_id != str(item_id) or item.status is not ContinuityStatus.OPEN:
                continue
            updated = replace(
                item,
                status=next_status,
                resolution_message_id=str(resolution_message_id or "") or None,
                resolution_quote=str(resolution_quote or ""),
                resolved_at=int(resolved_at or 0) or None,
                updated_at=int(resolved_at or item.updated_at),
            )
            self.continuity_items[index] = updated
            return updated
        return None

    def append_continuity_item(self, persona_id, item):
        del persona_id
        if any(existing.item_id == item.item_id for existing in self.continuity_items):
            return None
        self.continuity_items.append(item)
        return item

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
        subjects = set(str(item) for item in (beneficiary_subject_ids or ()))
        selected_statuses = {
            item.value if hasattr(item, "value") else str(item)
            for item in (statuses or ())
        }
        items = [
            item
            for item in self.self_commitments
            if (group_id is None or item.group_id == str(group_id))
            and (
                not subjects or item.beneficiary_subject_id in subjects
            )
            and (not selected_statuses or item.status.value in selected_statuses)
        ]
        return items[:limit]

    def append_continuity_followup(self, persona_id, event):
        del persona_id
        from dataclasses import replace

        from groupmate.models import ContinuityFollowupOutcome, ContinuityStatus

        if not hasattr(self, "continuity_followups"):
            self.continuity_followups = []
        if any(item.event_id == event.event_id for item in self.continuity_followups):
            return None
        self.continuity_followups.append(event)
        if (
            event.outcome
            in (
                ContinuityFollowupOutcome.COMPLETED,
                ContinuityFollowupOutcome.CANCELLED,
            )
            and event.response_policy == "speak"
        ):
            next_status = (
                ContinuityStatus.COMPLETED
                if event.outcome is ContinuityFollowupOutcome.COMPLETED
                else ContinuityStatus.CANCELLED
            )
            for index, item in enumerate(self.continuity_items):
                if item.item_id == event.item_id:
                    self.continuity_items[index] = replace(
                        item,
                        status=next_status,
                        resolution_message_id=event.source_message_id,
                        resolution_quote=event.evidence_quote,
                        resolved_at=event.occurred_at,
                        updated_at=event.occurred_at,
                    )
                    break
        return event

    def mark_continuity_followup_sent(self, persona_id, event_id, *, sent_at):
        del persona_id
        from dataclasses import replace

        if not hasattr(self, "continuity_followups"):
            return None
        for index, item in enumerate(self.continuity_followups):
            if item.event_id == event_id and not item.sent:
                updated = replace(item, sent=True, sent_at=sent_at)
                self.continuity_followups[index] = updated
                return updated
        return None

    def reopen_continuity_item_after_unsent_followup(self, persona_id, event_id, *, now):
        del persona_id
        from dataclasses import replace

        from groupmate.models import ContinuityFollowupOutcome, ContinuityStatus

        if not hasattr(self, "continuity_followups"):
            return None
        event = next(
            (item for item in self.continuity_followups if item.event_id == event_id),
            None,
        )
        if (
            event is None
            or event.sent
            or event.outcome
            not in (
                ContinuityFollowupOutcome.COMPLETED,
                ContinuityFollowupOutcome.CANCELLED,
            )
        ):
            return None
        for index, item in enumerate(self.continuity_items):
            if item.item_id != event.item_id:
                continue
            updated = replace(
                item,
                status=ContinuityStatus.OPEN,
                resolution_message_id=None,
                resolution_quote="",
                resolved_at=None,
                updated_at=int(now),
            )
            self.continuity_items[index] = updated
            return updated
        return None

    def next_self_commitment_attempt_at(self, persona_id):
        del persona_id
        pending = [
            int(item.next_attempt_at)
            for item in self.self_commitments
            if getattr(item, "next_attempt_at", None)
            and str(getattr(item.status, "value", item.status)) == "pending"
        ]
        return min(pending) if pending else None

    def resolve_self_commitment(
        self,
        persona_id,
        commitment_id,
        *,
        status,
        result_decision_id="",
        result_quote="",
        result_facts=(),
        failure_code="",
        resolved_at=0,
    ):
        del persona_id, result_facts, failure_code
        from dataclasses import replace

        for index, item in enumerate(self.self_commitments):
            if item.commitment_id != str(commitment_id):
                continue
            updated = replace(
                item,
                status=status,
                result_decision_id=str(result_decision_id or "") or None,
                result_quote=str(result_quote or ""),
                resolved_at=int(resolved_at or 0) or None,
                next_attempt_at=None,
                updated_at=int(resolved_at or item.updated_at),
            )
            self.self_commitments[index] = updated
            return updated
        return None

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
        del persona_id, now, status_accepted_only, kwargs
        items = [
            item
            for item in self.memories
            if item.group_id == str(group_id)
            and (subject_id is None or item.subject_id == str(subject_id))
        ]
        return items[:limit]

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
    ):
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
    ):
        del candidate_id, reason, decided_at, superseded_memory_id
        self.add_memory(persona_id, memory)

    def has_tombstone(
        self,
        persona_id,
        group_id,
        subject_id,
        claim_hash_value,
    ):
        del persona_id, group_id, subject_id, claim_hash_value
        return False

    def latest_open_topic_epoch(self, persona_id, group_id):
        return self._topic_epochs.get((str(persona_id), str(group_id)))

    async def open_topic_epoch_async(
        self,
        persona_id,
        group_id,
        topic_id,
        opened_at,
        last_message_id=None,
        close_existing_reason="HARD_WAKE",
    ):
        del close_existing_reason
        self._topic_epochs[(str(persona_id), str(group_id))] = {
            "topic_id": str(topic_id),
            "opened_at": int(opened_at),
            "last_message_id": last_message_id,
        }
        return True

    async def close_topic_epoch_async(
        self,
        persona_id,
        group_id,
        topic_id,
        closed_at,
        close_reason,
        last_message_id=None,
    ):
        del closed_at, close_reason, last_message_id
        key = (str(persona_id), str(group_id))
        current = self._topic_epochs.get(key)
        if current and current["topic_id"] == str(topic_id):
            self._topic_epochs.pop(key, None)
        return True

    async def grant_continuation_async(self, **kwargs):
        self.continuation_grants.append(dict(kwargs))
        return True

    def record_transition(
        self, persona_id, decision_id, group_id, state, reason, timestamp
    ):
        del persona_id
        self.transitions.append((decision_id, group_id, state, reason, timestamp))

    def enqueue_outbox(
        self, persona_id, decision_id, group_id, text, created_at, expires_at=None, **kwargs
    ):
        del persona_id, group_id, created_at, expires_at, kwargs
        if decision_id in self.outbox:
            return False
        self.outbox[decision_id] = {
            "text": text,
            "sent_at": None,
            "status": "pending",
            "attempt": 0,
        }
        return True

    async def enqueue_outbox_async(
        self,
        persona_id,
        decision_id,
        group_id,
        text,
        created_at,
        expires_at=None,
        **kwargs,
    ):
        return self.enqueue_outbox(
            persona_id,
            decision_id,
            group_id,
            text,
            created_at,
            expires_at,
            **kwargs,
        )

    async def transition_outbox_async(
        self,
        persona_id,
        decision_id,
        expected,
        status,
        *,
        failure_code="",
        failure_detail="",
        increment_attempt=False,
    ):
        del persona_id, failure_detail
        row = self.outbox.get(decision_id)
        if row is None or row.get("status") != expected:
            return False
        row["status"] = status
        if increment_attempt:
            row["attempt"] = int(row.get("attempt", 0)) + 1
        row["failure_code"] = failure_code
        return True

    async def finalize_delivery_async(
        self,
        persona_id,
        decision_id,
        sent_at,
        bot_message,
        reason="sent",
    ):
        row = self.outbox.get(decision_id)
        if row is None or row.get("status") != "sending":
            return False
        self.save_message(persona_id, bot_message)
        row["sent_at"] = sent_at
        row["status"] = "sent"
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

    def append_social_event(self, persona_id, event):
        key = (
            str(persona_id),
            event.group_id,
            event.source_message_id,
            event.kind.value,
        )
        if any(
            (
                self._social_event_personas.get(id(e)),
                e.group_id,
                e.source_message_id,
                e.kind.value,
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

    def relationship_learning_quality(self, persona_id, group_id=None):
        items = [
            event
            for event in self.social_events
            if self._social_event_personas.get(id(event)) == str(persona_id)
            and (group_id is None or event.group_id == str(group_id))
        ]
        reviewed = [event for event in items if event.review_code]
        errors = [event for event in reviewed if event.status.value == "rejected"]
        return {
            "pending": sum(event.status.value == "pending" for event in items),
            "accepted": sum(event.review_code == "correct" for event in reviewed),
            "wrong_person": sum(event.review_code == "wrong_person" for event in reviewed),
            "wrong_kind": sum(event.review_code == "wrong_kind" for event in reviewed),
            "insufficient_context": sum(
                event.review_code == "insufficient_context" for event in reviewed
            ),
            "other_error": 0,
            "reviewed_count": len(reviewed),
            "error_count": len(errors),
            "error_rate": len(errors) / len(reviewed) if reviewed else 0.0,
        }

    def get_relationship_state(self, persona_id, group_id, user_id):
        return self.relationship_state.get(
            (str(persona_id), str(group_id), str(user_id))
        )

    def upsert_relationship_state(self, persona_id, state):
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
        if event.status.value != "accepted":
            return current
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

class StaticGenerationModel:
    def __init__(self, text):
        self.text = text
        self.calls = 0
        self.repairs = 0

    async def generate(self, plan, topic, memories):
        self.calls += 1
        return self.text

    async def repair(self, text, violations):
        self.repairs += 1
        return "短一点呀。"


class FakePlatform:
    def __init__(self):
        self.sent = []

    async def send_outbound(
        self, group_id, outbound, decision_id, quote_message_id=None
    ):
        from groupmate.models import OutboundKind, SendResult

        for index, segment in enumerate(outbound):
            if segment.kind is not OutboundKind.TEXT:
                continue
            self.sent.append(
                {
                    "group_id": group_id,
                    "text": segment.text,
                    "decision_id": decision_id,
                    "quote_message_id": quote_message_id if index == 0 else None,
                }
            )
        return SendResult.confirmed(len(outbound))


class StaticPersona:
    class _Assembly:
        relationships = {}

    assembly = _Assembly()

    async def system_prompt(self, group_id):
        return "你是爱弥斯。"

    def build_user_context(self, topic, memories, **kwargs):
        del kwargs
        return "<group_context>test</group_context>"

    def assemble(self, topic, memories, **kwargs):
        from groupmate.core.context_assembly import AssembledPrompt

        return AssembledPrompt(
            system="你是爱弥斯。",
            user=self.build_user_context(topic, memories, **kwargs),
            soft_trigger=bool(kwargs.get("soft_trigger")),
        )


def persona_context(
    prompt_provider=None,
    aliases=("爱弥斯", "小爱", "飞行雪绒"),
    *,
    persona_id="aemeath",
    display_name="爱弥斯",
):
    from groupmate.persona.aemeath import AEMEATH_PARTICIPATION_PROFILE
    from groupmate.persona.registry import PersonaContext, PersonaDefinition

    provider = prompt_provider or StaticPersona()
    definition = PersonaDefinition(
        persona_id=persona_id,
        display_name=display_name,
        default_aliases=tuple(aliases),
        participation_profile=AEMEATH_PARTICIPATION_PROFILE,
        provider_factory=lambda relationships: provider,
    )
    return PersonaContext(
        definition=definition,
        aliases=tuple(aliases),
        relationship_seeds=(),
        prompt_provider=provider,
    )


class NullVision:
    async def describe(self, image_urls):
        return ""


class RecordingWorkflow:
    def __init__(self, sent=True, text="在呢。"):
        self.evaluations = []
        self.sent = sent
        self.text = text
        self.memory = FakeMemoryRepository()
        self.character_name = "爱弥斯"

    async def evaluate(self, topic, trigger, policy, trigger_alias=""):
        del trigger_alias
        self.evaluations.append((topic, trigger, policy))
        from groupmate.models import WorkflowOutcome

        return WorkflowOutcome(
            decision_id="test-" + str(len(self.evaluations)),
            sent=self.sent,
            reason="sent" if self.sent else "silent",
            text=self.text if self.sent else "",
        )
