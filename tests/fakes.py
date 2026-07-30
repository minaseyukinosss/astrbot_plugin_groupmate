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
        self.messages = []
        self.social_events = []
        self.relationship_state = {}
        self._message_personas = set()
        self._social_event_personas = {}

    def save_message(self, persona_id, message):
        key = (str(persona_id), message.identity)
        if key in self._message_personas:
            return False
        self._message_personas.add(key)
        self.messages.append(message)
        return True

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

    def mark_outbox_sent(self, persona_id, decision_id, sent_at):
        del persona_id
        self.outbox[decision_id]["sent_at"] = sent_at
        self.outbox[decision_id]["status"] = "sent"

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

    async def send_text(self, group_id, text, decision_id):
        self.sent.append(
            {"group_id": group_id, "text": text, "decision_id": decision_id}
        )
        from groupmate.models import SendResult

        return SendResult.confirmed()

    async def send_segments(
        self, group_id, segments, decision_id, quote_message_id=None
    ):
        for index, segment in enumerate(segments):
            await self.send_text(group_id, segment, decision_id)
            self.sent[-1]["quote_message_id"] = (
                quote_message_id if index == 0 else None
            )
        from groupmate.models import SendResult

        return SendResult.confirmed(len(segments))


class StaticPersona:
    async def system_prompt(self, group_id):
        return "你是爱弥斯。"

    def build_user_context(self, topic, memories, **kwargs):
        del kwargs
        return "<group_context>test</group_context>"


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
