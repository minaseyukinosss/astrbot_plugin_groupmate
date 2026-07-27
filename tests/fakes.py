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
        self.favorability = {}
        self.messages = []
        self.social_events = []
        self.relationship_state = {}

    def save_message(self, message):
        if any(item.identity == message.identity for item in self.messages):
            return False
        self.messages.append(message)
        return True

    def search_memories(
        self,
        group_id,
        query,
        now,
        limit,
        subject_id=None,
        subject_ids=None,
        include_user_in_group=True,
    ):
        del group_id, query, now, subject_id, subject_ids, include_user_in_group
        return list(self.memories)[:limit]

    def add_memory(self, memory):
        self.memories.append(memory)

    def record_transition(self, decision_id, group_id, state, reason, timestamp):
        self.transitions.append((decision_id, group_id, state, reason, timestamp))

    def enqueue_outbox(self, decision_id, group_id, text, created_at, expires_at=None):
        if decision_id in self.outbox:
            return False
        self.outbox[decision_id] = {
            "text": text,
            "sent_at": None,
            "status": "pending",
            "attempt": 0,
        }
        return True

    def mark_outbox_sent(self, decision_id, sent_at):
        self.outbox[decision_id]["sent_at"] = sent_at
        self.outbox[decision_id]["status"] = "sent"

    def get_favorability(self, group_id, user_id):
        return self.favorability.get((str(group_id), str(user_id)))

    def set_favorability(self, group_id, user_id, score, updated_at):
        from groupmate.core.favorability import clamp_score

        del updated_at
        value = clamp_score(score)
        self.favorability[(str(group_id), str(user_id))] = value
        return value

    def adjust_favorability(self, group_id, user_id, delta, updated_at, *, default=0):
        from groupmate.core.favorability import apply_delta

        current = self.get_favorability(group_id, user_id)
        return self.set_favorability(
            group_id, user_id, apply_delta(current, delta, default=default), updated_at
        )

    def append_social_event(self, event):
        key = (event.group_id, event.source_message_id, event.kind.value)
        if any(
            (e.group_id, e.source_message_id, e.kind.value) == key
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

    def upsert_relationship_state(self, state):
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
        self.set_favorability(group_id, user_id, state.affinity, state.updated_at or now)
        return state

    def record_social_interaction(
        self,
        event,
        *,
        soft_trigger=False,
        configured_relationship=None,
        now=0,
    ):
        from groupmate.models import RelationshipState
        from groupmate.social.projector import SocialStateProjector

        inserted = self.append_social_event(event)
        current = self.get_relationship_state(event.group_id, event.user_id)
        if current is None:
            fav = self.get_favorability(event.group_id, event.user_id)
            current = RelationshipState(
                group_id=event.group_id,
                user_id=event.user_id,
                affinity=int(fav) if fav is not None else 0,
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
            soft_trigger=soft_trigger,
        )
        self.upsert_relationship_state(state)
        self.set_favorability(
            event.group_id, event.user_id, state.affinity, state.updated_at
        )
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
