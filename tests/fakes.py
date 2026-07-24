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

    def search_memories(self, group_id, query, now, limit, subject_id=None):
        return list(self.memories)[:limit]

    def add_memory(self, memory):
        self.memories.append(memory)

    def record_transition(self, decision_id, group_id, state, reason, timestamp):
        self.transitions.append((decision_id, group_id, state, reason, timestamp))

    def enqueue_outbox(self, decision_id, group_id, text, created_at, expires_at=None):
        if decision_id in self.outbox:
            return False
        self.outbox[decision_id] = {"text": text, "sent_at": None}
        return True

    def mark_outbox_sent(self, decision_id, sent_at):
        self.outbox[decision_id]["sent_at"] = sent_at

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

    async def send_segments(
        self, group_id, segments, decision_id, quote_message_id=None
    ):
        for segment in segments:
            await self.send_text(group_id, segment, decision_id)


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
