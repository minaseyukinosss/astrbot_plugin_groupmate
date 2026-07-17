# Groupmate Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a production-oriented AstrBot plugin that observes QQ group chat, restores recent NapCat history, performs structured proactive participation decisions, generates persona-consistent replies, and maintains bounded social memory.

**Architecture:** A modular monolith uses ports and adapters around a deterministic cognitive workflow. Domain modules are independent of AstrBot; a per-group actor serializes state, while AstrBot/NapCat, model, vision, storage, and sending implementations live at the boundary.

**Tech Stack:** Python 3.10+, asyncio, dataclasses, sqlite3, AstrBot 4.24+, NapCat/OneBot v11, pytest.

---

## File Map

- `main.py`: AstrBot plugin class, handlers, hooks, and admin commands.
- `metadata.yaml`: AstrBot marketplace and compatibility metadata.
- `_conf_schema.json`: WebUI configuration schema.
- `groupmate/models.py`: immutable domain events, decisions, policies, and memory types.
- `groupmate/ports.py`: protocols for models, vision, storage, platform, history, and time.
- `groupmate/triggers.py`: deterministic trigger and command routing.
- `groupmate/topics.py`: bounded working context and topic accumulation.
- `groupmate/rate_limit.py`: per-group spontaneous participation budget.
- `groupmate/memory.py`: SQLite event, profile, memory, decision, and outbox repository.
- `groupmate/persona.py`: prompt composition and the Aemeath preset loader.
- `groupmate/guardrails.py`: deterministic response validation and splitting.
- `groupmate/workflow.py`: typed cognitive state machine.
- `groupmate/runtime.py`: per-group actor mailbox and debounce scheduling.
- `groupmate/astrbot_adapter.py`: AstrBot event translation and concrete model/platform/history ports.
- `resources/aemeath_persona.md`: default persona preset.
- `tests/`: offline unit and contract tests.

### Task 1: Plugin Skeleton and Domain Types

**Files:**
- Create: `metadata.yaml`
- Create: `requirements.txt`
- Create: `groupmate/__init__.py`
- Create: `groupmate/models.py`
- Create: `groupmate/ports.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing model tests**

```python
from groupmate.models import ChatMessage, Decision, DecisionAction, TriggerKind


def test_chat_message_normalizes_identity_without_astrbot_types():
    message = ChatMessage(
        message_id="m1", group_id="g1", sender_id="u1",
        sender_name="Alice", text=" hello ", timestamp=10,
    )
    assert message.text == "hello"
    assert message.identity == ("g1", "m1")


def test_decision_ignore_has_safe_defaults():
    decision = Decision.ignore("low_relevance")
    assert decision.action is DecisionAction.IGNORE
    assert decision.trigger is TriggerKind.CANDIDATE
    assert decision.contribution == ""
```

- [ ] **Step 2: Run the tests and verify the missing-module failure**

Run: `pytest tests/test_models.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'groupmate'`.

- [ ] **Step 3: Implement immutable domain types and ports**

Define string enums for `TriggerKind`, `DecisionAction`, `Urgency`, and
`MemoryKind`; frozen dataclasses for `ChatMessage`, `TopicSnapshot`, `Decision`,
`ReplyPlan`, `MemoryItem`, and `GroupPolicy`; and protocols with async methods:

```python
class DecisionModelPort(Protocol):
    async def decide(self, topic: TopicSnapshot, policy: GroupPolicy,
                     memories: Sequence[MemoryItem]) -> Decision: ...


class GenerationModelPort(Protocol):
    async def generate(self, plan: ReplyPlan, topic: TopicSnapshot,
                       memories: Sequence[MemoryItem]) -> str: ...


class PlatformPort(Protocol):
    async def send_text(self, group_id: str, text: str,
                        decision_id: str) -> None: ...
```

Include sync repository methods and an injectable `Clock` protocol. Keep every
domain module free of `astrbot` imports.

- [ ] **Step 4: Run model tests**

Run: `pytest tests/test_models.py -q`
Expected: 2 passed.

- [ ] **Step 5: Commit the skeleton**

```bash
git add metadata.yaml requirements.txt groupmate tests/test_models.py
git commit -m "feat: add groupmate domain contracts"
```

### Task 2: Trigger Routing, Topics, and Rate Limits

**Files:**
- Create: `groupmate/triggers.py`
- Create: `groupmate/topics.py`
- Create: `groupmate/rate_limit.py`
- Test: `tests/test_triggers.py`
- Test: `tests/test_topics.py`
- Test: `tests/test_rate_limit.py`

- [ ] **Step 1: Write trigger-routing tests**

```python
def test_existing_command_is_bypassed(router, message_factory):
    message = message_factory(text="help", is_command=True)
    assert router.classify(message).kind is TriggerKind.COMMAND


def test_native_at_is_not_generated_by_plugin(router, message_factory):
    message = message_factory(text="在吗", mentions_bot=True)
    assert router.classify(message).kind is TriggerKind.NATIVE_DIRECT


def test_alias_direct_and_alias_discussion_are_distinct(router, message_factory):
    assert router.classify(message_factory(text="小爱，在吗")).kind is TriggerKind.ALIAS_DIRECT
    assert router.classify(message_factory(text="小爱是不是挺难调的")).kind is TriggerKind.ALIAS_MENTION
```

- [ ] **Step 2: Write topic and rate tests**

```python
def test_topic_window_is_bounded(message_factory):
    window = TopicWindow(max_messages=3)
    for index in range(5):
        window.append(message_factory(message_id=str(index), timestamp=index))
    assert [item.message_id for item in window.snapshot().messages] == ["2", "3", "4"]


def test_spontaneous_budget_blocks_seventh_message():
    limiter = SlidingWindowRateLimiter(hourly_limit=6, cooldown_seconds=0)
    for timestamp in range(6):
        limiter.record(timestamp)
    assert limiter.allow(10) is False
```

- [ ] **Step 3: Verify failures**

Run: `pytest tests/test_triggers.py tests/test_topics.py tests/test_rate_limit.py -q`
Expected: FAIL because the policy modules do not exist.

- [ ] **Step 4: Implement policy modules**

Implement pure deterministic components:

```python
class TriggerRouter:
    def classify(self, message: ChatMessage) -> TriggerResult:
        if message.is_bot or not message.has_content:
            return TriggerResult(TriggerKind.IGNORE, "ignored_sender_or_empty")
        if message.is_command:
            return TriggerResult(TriggerKind.COMMAND, "existing_command")
        if message.mentions_bot or message.reply_to_bot:
            return TriggerResult(TriggerKind.NATIVE_DIRECT, "native_direct")
        # Alias direct-address patterns are hard wake; other alias references
        # receive an elevated semantic decision rather than an unconditional reply.
```

`TopicWindow` deduplicates by `(group_id, message_id)` and uses a deque.
`SlidingWindowRateLimiter` records only spontaneous sends and enforces both the
hourly cap and cooldown.

- [ ] **Step 5: Run all policy tests and commit**

Run: `pytest tests/test_triggers.py tests/test_topics.py tests/test_rate_limit.py -q`
Expected: all pass.

```bash
git add groupmate/triggers.py groupmate/topics.py groupmate/rate_limit.py tests
git commit -m "feat: add group participation policies"
```

### Task 3: SQLite Memory and Replay Store

**Files:**
- Create: `groupmate/memory.py`
- Test: `tests/test_memory.py`

- [ ] **Step 1: Write persistence tests**

```python
def test_messages_are_idempotent_and_recent_ordered(tmp_path, message_factory):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    message = message_factory(message_id="same", timestamp=2)
    store.save_message(message)
    store.save_message(message)
    assert store.recent_messages(message.group_id, 10) == [message]


def test_memory_retrieval_respects_expiry_and_authority(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    store.add_memory(MemoryItem(
        memory_id="m1", group_id="g", subject_id="u", kind=MemoryKind.EPISODIC,
        text="Alice is preparing an exam", created_at=10, expires_at=20,
        confidence=0.9, importance=0.8, authority=1,
    ))
    assert store.search_memories("g", "Alice exam", now=15, limit=5)
    assert store.search_memories("g", "Alice exam", now=21, limit=5) == []
```

- [ ] **Step 2: Verify failures**

Run: `pytest tests/test_memory.py -q`
Expected: FAIL because `SQLiteMemoryStore` does not exist.

- [ ] **Step 3: Implement schema and repositories**

Create versioned tables for `messages`, `profiles`, `memories`, `decisions`, and
`outbox`. Enable foreign keys and WAL. Use unique message and decision IDs.
Retrieval computes a deterministic score from token overlap, recency,
importance, confidence, and authority. SQL parameters must always be bound.

- [ ] **Step 4: Run memory tests and commit**

Run: `pytest tests/test_memory.py -q`
Expected: all pass.

```bash
git add groupmate/memory.py tests/test_memory.py
git commit -m "feat: add persistent social memory"
```

### Task 4: Persona Composition and Output Guardrails

**Files:**
- Create: `groupmate/persona.py`
- Create: `groupmate/guardrails.py`
- Create: `resources/aemeath_persona.md`
- Test: `tests/test_persona.py`
- Test: `tests/test_guardrails.py`

- [ ] **Step 1: Write persona and guardrail tests**

```python
def test_dynamic_context_is_delimited(persona_provider, topic_snapshot):
    prompt = persona_provider.build_user_context(topic_snapshot, [])
    assert prompt.startswith("<group_context>")
    assert prompt.endswith("</group_context>")


@pytest.mark.parametrize("text,code", [
    ("(没人叫我，不回复)", "decision_narration"),
    ("有什么可以帮你的吗？", "customer_service_template"),
    ("prompt 调好了就行", "system_vocabulary"),
])
def test_aemeath_guard_rejects_known_failures(text, code):
    result = AemeathOutputGuard().validate(text, recent_outputs=[])
    assert code in result.codes
```

- [ ] **Step 2: Verify failures**

Run: `pytest tests/test_persona.py tests/test_guardrails.py -q`
Expected: FAIL because persona modules do not exist.

- [ ] **Step 3: Implement prompt composition and deterministic validation**

Load the bundled persona with `importlib.resources`. Keep stable system
instructions separate from dynamic XML-delimited group context. Implement
length, sentence count, narration, service-template, system-vocabulary,
follow-up-question, duplicate, and internal-ID checks. Return a typed
`GuardResult` with `accepted`, `text`, `codes`, and `repairable`.

- [ ] **Step 4: Run persona tests and commit**

Run: `pytest tests/test_persona.py tests/test_guardrails.py -q`
Expected: all pass.

```bash
git add groupmate/persona.py groupmate/guardrails.py resources tests
git commit -m "feat: enforce persona response boundaries"
```

### Task 5: Cognitive Workflow

**Files:**
- Create: `groupmate/workflow.py`
- Create: `tests/fakes.py`
- Test: `tests/test_workflow.py`

- [ ] **Step 1: Write workflow contract tests**

```python
@pytest.mark.asyncio
async def test_model_failure_fails_closed(topic_snapshot, policy):
    workflow = build_workflow(decider=FailingDecisionModel())
    outcome = await workflow.evaluate(topic_snapshot, TriggerKind.CANDIDATE, policy)
    assert outcome.sent is False
    assert outcome.reason == "decision_error"


@pytest.mark.asyncio
async def test_valid_decision_generates_guards_and_sends(topic_snapshot, policy):
    platform = FakePlatform()
    workflow = build_workflow(
        decider=StaticDecisionModel(Decision.respond("useful_reaction")),
        generator=StaticGenerationModel("这也太离谱了呀。"),
        platform=platform,
    )
    outcome = await workflow.evaluate(topic_snapshot, TriggerKind.CANDIDATE, policy)
    assert outcome.sent is True
    assert platform.sent[0].text == "这也太离谱了呀。"
```

- [ ] **Step 2: Verify failures**

Run: `pytest tests/test_workflow.py -q`
Expected: FAIL because `CognitiveWorkflow` does not exist.

- [ ] **Step 3: Implement the explicit workflow**

Implement state transitions with one decision call, optional vision call, one
generation call, deterministic guard, and at most one style repair call.
Persist reason-coded transitions through `TraceSink`. Direct alias wakes bypass
the spontaneous quota but still pass output guardrails. Invalid decisions,
stale topics, and model failures return silent outcomes.

- [ ] **Step 4: Run workflow tests and commit**

Run: `pytest tests/test_workflow.py -q`
Expected: all pass.

```bash
git add groupmate/workflow.py tests/fakes.py tests/test_workflow.py
git commit -m "feat: add cognitive response workflow"
```

### Task 6: Per-Group Actor Runtime

**Files:**
- Create: `groupmate/runtime.py`
- Test: `tests/test_runtime.py`

- [ ] **Step 1: Write actor concurrency tests**

```python
@pytest.mark.asyncio
async def test_burst_collapses_to_one_evaluation(message_factory):
    workflow = RecordingWorkflow()
    actor = GroupActor("g", workflow, policy=fast_policy())
    await actor.start()
    for index in range(4):
        await actor.submit(message_factory(message_id=str(index), timestamp=index))
    await actor.drain()
    assert len(workflow.evaluations) == 1
    await actor.close()


@pytest.mark.asyncio
async def test_native_wake_cancels_pending_spontaneous_topic(message_factory):
    workflow = RecordingWorkflow()
    actor = GroupActor("g", workflow, policy=fast_policy())
    await actor.start()
    await actor.submit(message_factory(message_id="soft"))
    await actor.submit(message_factory(message_id="direct", mentions_bot=True))
    await actor.drain()
    assert workflow.evaluations == []
    await actor.close()
```

- [ ] **Step 2: Verify failures**

Run: `pytest tests/test_runtime.py -q`
Expected: FAIL because the actor runtime does not exist.

- [ ] **Step 3: Implement actor messages and lifecycle**

Use an `asyncio.Queue` with internal `Ingest`, `EvaluateTopic`, `Flush`, and
`Stop` messages. A debounce task only enqueues `EvaluateTopic`; all state
mutations remain in the actor loop. `GroupRuntimeManager` lazily creates actors,
routes messages, exposes snapshots, and closes all tasks on plugin termination.

- [ ] **Step 4: Run runtime tests and commit**

Run: `pytest tests/test_runtime.py -q`
Expected: all pass with no pending-task warnings.

```bash
git add groupmate/runtime.py tests/test_runtime.py
git commit -m "feat: serialize per-group agent state"
```

### Task 7: AstrBot and NapCat Adapter

**Files:**
- Create: `groupmate/astrbot_adapter.py`
- Create: `main.py`
- Test: `tests/test_astrbot_translation.py`

- [ ] **Step 1: Write raw OneBot translation tests**

```python
def test_onebot_history_translation_preserves_reply_and_image():
    raw = {
        "message_id": "1", "group_id": "2", "user_id": "3", "time": 10,
        "sender": {"nickname": "Alice"},
        "message": [
            {"type": "reply", "data": {"id": "0"}},
            {"type": "text", "data": {"text": "看看这个"}},
            {"type": "image", "data": {"url": "https://example/image.jpg"}},
        ],
    }
    message = OneBotTranslator.from_history(raw, bot_id="9")
    assert message.text == "看看这个"
    assert message.reply_to_message_id == "0"
    assert message.image_urls == ("https://example/image.jpg",)
```

- [ ] **Step 2: Verify the translation test fails**

Run: `pytest tests/test_astrbot_translation.py -q`
Expected: FAIL because `OneBotTranslator` does not exist.

- [ ] **Step 3: Implement concrete adapter ports**

Implement:

- `OneBotTranslator` for live AstrBot events and NapCat history dictionaries;
- `NapCatHistoryPort` using `event.bot.call_action("get_group_msg_history",
  group_id=..., count=100, reverseOrder=True)`;
- `AstrBotDecisionModel` using the configured decision provider and strict JSON;
- `AstrBotGenerationModel` using `get_current_chat_provider_id(umo)`;
- `AstrBotPlatformPort` using `context.send_message(umo, MessageChain)`;
- `AstrBotPersonaProvider` using `context.persona_manager` with bundled fallback.

`main.py` registers an AIOCQHTTP group observer, `on_llm_request` enrichment,
and `/groupmate status|pause|resume|reset` admin commands. Existing command
handlers are detected from activated handler filter class names and bypassed.

- [ ] **Step 4: Run adapter tests and syntax checks**

Run: `pytest tests/test_astrbot_translation.py -q`
Expected: all pass.

Run: `python -m compileall -q main.py groupmate`
Expected: exit 0.

- [ ] **Step 5: Commit the adapter**

```bash
git add main.py groupmate/astrbot_adapter.py tests/test_astrbot_translation.py
git commit -m "feat: integrate AstrBot and NapCat"
```

### Task 8: WebUI Configuration and Diagnostics

**Files:**
- Create: `_conf_schema.json`
- Create: `groupmate/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write configuration tests**

```python
def test_defaults_are_balanced_and_safe():
    settings = PluginSettings.from_mapping({})
    assert settings.history_limit == 100
    assert settings.spontaneous_hourly_limit == 6
    assert settings.decision_threshold >= 0.7
    assert settings.enabled_groups == ()


def test_invalid_ranges_are_clamped():
    settings = PluginSettings.from_mapping({"history_limit": 9999,
                                            "decision_threshold": -2})
    assert settings.history_limit == 500
    assert settings.decision_threshold == 0.0
```

- [ ] **Step 2: Verify failure**

Run: `pytest tests/test_config.py -q`
Expected: FAIL because `PluginSettings` does not exist.

- [ ] **Step 3: Implement settings and schema**

Expose group allowlist, aliases, decision provider, persona, vision, history,
thresholds, 3-6/hour controls, cooldown, debounce, quiet hours, memory retention,
relationship entries, and diagnostics. Provider and persona fields use only
documented AstrBot `_special` selectors.

- [ ] **Step 4: Validate schema and run tests**

Run: `python -m json.tool _conf_schema.json >/dev/null`
Expected: exit 0.

Run: `pytest tests/test_config.py -q`
Expected: all pass.

- [ ] **Step 5: Commit configuration**

```bash
git add _conf_schema.json groupmate/config.py tests/test_config.py
git commit -m "feat: add groupmate WebUI configuration"
```

### Task 9: Replay Evaluation, Documentation, and Final Verification

**Files:**
- Create: `groupmate/evaluation.py`
- Create: `tests/fixtures/replay.jsonl`
- Create: `tests/test_replay.py`
- Create: `README.md`
- Create: `.gitignore`
- Create: `pyproject.toml`

- [ ] **Step 1: Write the replay acceptance test**

```python
def test_replay_has_perfect_command_bypass_and_hard_wake_recall(replay_runner):
    report = replay_runner.run_fixture("tests/fixtures/replay.jsonl")
    assert report.command_interference == 0
    assert report.duplicate_responses == 0
    assert report.hard_wake_recall == 1.0
```

- [ ] **Step 2: Verify failure**

Run: `pytest tests/test_replay.py -q`
Expected: FAIL because the replay evaluator and fixture do not exist.

- [ ] **Step 3: Implement replay metrics and operator documentation**

The evaluator replays normalized events with fake ports and reports hard-wake
recall, spontaneous precision from fixture labels, command interference,
duplicate response count, persona pass rate, and model call counts. README
documents installation under `AstrBot/data/plugins`, NapCat requirements,
configuration, privacy, commands, architecture, and known first-version limits.

- [ ] **Step 4: Run the complete verification suite**

Run: `pytest -q`
Expected: all tests pass.

Run: `python -m compileall -q main.py groupmate tests`
Expected: exit 0.

Run: `python -m json.tool _conf_schema.json >/dev/null`
Expected: exit 0.

Run: `git diff --check`
Expected: no output.

- [ ] **Step 5: Commit completed plugin**

```bash
git add README.md .gitignore pyproject.toml groupmate/evaluation.py tests
git commit -m "test: add replay evaluation and operator guide"
```

