from groupmate.models import Decision


class FakeClock:
    def __init__(self, now=101):
        self.value = now

    def now(self):
        return self.value


class FakeMemoryRepository:
    def __init__(self):
        self.transitions = []
        self.outbox = {}

    def search_memories(self, group_id, query, now, limit, subject_id=None):
        return []

    def record_transition(self, decision_id, group_id, state, reason, timestamp):
        self.transitions.append((decision_id, group_id, state, reason, timestamp))

    def enqueue_outbox(self, decision_id, group_id, text, created_at, expires_at=None):
        if decision_id in self.outbox:
            return False
        self.outbox[decision_id] = {"text": text, "sent_at": None}
        return True

    def mark_outbox_sent(self, decision_id, sent_at):
        self.outbox[decision_id]["sent_at"] = sent_at


class StaticDecisionModel:
    def __init__(self, decision):
        self.decision = decision
        self.calls = 0

    async def decide(self, topic, policy, memories):
        self.calls += 1
        return self.decision


class FailingDecisionModel:
    async def decide(self, topic, policy, memories):
        raise RuntimeError("provider unavailable")


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


class StaticPersona:
    async def system_prompt(self, group_id):
        return "你是爱弥斯。"

    def build_user_context(self, topic, memories):
        return "<group_context>test</group_context>"


class NullVision:
    async def describe(self, image_urls):
        return ""

