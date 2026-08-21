# Groupmate Chat Mainline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect real AstrBot group events to one conservative, validated, durable text reply.

**Architecture:** `SocialRuntimeManager` remains responsible for scene, cognition, intentions, and governance. `AstrBotSocialRuntimeBridge` composes the AstrBot model, Ambient wakeup, narrow `ReplyPlan` executor, Outbox dispatcher, and OneBot transport.

**Tech Stack:** Python 3.13, asyncio, SQLite, AstrBot, OneBot v11, pytest.

**Spec:** `docs/superpowers/specs/2026-08-21-groupmate-chat-mainline-design.md`

## Global Constraints

- Do not modify `pages/`.
- Do not add feature plugins, autonomy, complex tasks, or media selection.
- Platform facts are authoritative; uncertainty returns `SILENCE`.
- External-plugin-owned events create no cognition call, ReplyPlan, or Outbox part.
- Ordinary chat creates at most one text reply.
- Run focused tests only; do not repeatedly run the full suite.

## File Map

Create:

- `groupmate/adapters/astrbot_models.py`: AstrBot JSON/text LLM port.
- `groupmate/adapters/astrbot_delivery.py`: OneBot send boundary.
- `groupmate/social_runtime/replying.py`: ReplyPlan, repository, generation, Outbox.
- `tests/social_runtime/actions/test_replying.py`
- `tests/scenarios/test_chat_mainline.py`

Modify:

- `groupmate/adapters/astrbot_events.py`
- `groupmate/social_runtime/persistence/event_store.py`
- `groupmate/social_runtime/world.py`
- `groupmate/social_runtime/scene_actor.py`
- `groupmate/social_runtime/event_fabric.py`
- `groupmate/social_runtime/manager.py`
- `groupmate/social_runtime/cognition/astrbot_workers.py`
- `groupmate/social_runtime/cognition/service.py`
- `groupmate/social_runtime/intentions.py`
- `groupmate/social_runtime/governor.py`
- `groupmate/adapters/onebot_delivery.py`
- `groupmate/adapters/astrbot_bridge.py`
- `_conf_schema.json`

---

### Task 1: Authoritative Platform Facts and Conversation Scene

**Files:**
- Modify: `groupmate/adapters/astrbot_events.py`
- Modify: `groupmate/social_runtime/persistence/event_store.py`
- Modify: `groupmate/social_runtime/manager.py`
- Modify: `groupmate/social_runtime/world.py`
- Modify: `groupmate/social_runtime/attention.py`
- Test: `tests/contracts/test_astrbot_events.py`
- Test: `tests/social_runtime/test_group_world.py`

**Interfaces:**
- Produces `event_by_source_message(persona_id, group_id, platform, source_message_id) -> SocialEventEnvelope | None`.
- Produces payload facts `bot_id`, `platform_id`, `session`, `reply_to_actor_id`, `reply_to_bot`.

- [ ] **Step 1: Write failing tests**

```python
def test_translator_preserves_platform_route():
    event = AstrBotEventTranslator("groupmate:default").translate(fake_event())
    assert event.payload["bot_id"] == "bot-1"
    assert event.payload["platform_id"] == "onebot-main"
    assert event.payload["session"] == "aiocqhttp:GroupMessage:885617919"

def test_world_tracks_reply_edge_and_bot_presence(projector, empty_world):
    state = projector.apply(empty_world, bot_message())
    state = projector.apply(state, reply_to_bot_message())
    assert state.recent_presence.last_bot_event_at is not None
    assert state.interaction_edges[-1].target_actor_id == "bot-1"
```

- [ ] **Step 2: Verify failure**

Run: `.venv/bin/python -m pytest tests/contracts/test_astrbot_events.py tests/social_runtime/test_group_world.py -q`

- [ ] **Step 3: Implement fact preservation and reply lookup**

```python
def event_by_source_message(self, persona_id, group_id, platform, source_message_id):
    events = self.event_envelopes(
        persona_id, group_id, (f"{platform}:{source_message_id}",)
    )
    if events:
        return events[0]
    with connect_database(self.path) as db:
        row = db.execute(
            "SELECT envelope_json FROM inbox WHERE persona_id=? AND group_id=? "
            "AND json_extract(envelope_json, '$.source_message_id')=? "
            "ORDER BY sequence DESC LIMIT 1",
            (persona_id, group_id, source_message_id),
        ).fetchone()
    return None if row is None else SocialEventEnvelope.from_dict(json.loads(row[0]))
```

Translator reads `get_platform_id()` and `unified_msg_origin`. Manager resolves immutable reply facts before `fabric.publish()`.

- [ ] **Step 4: Bound scene and presence**

Expire topics after 300 seconds, retain at most 12, and merge only same-participant activity within 30 seconds. Update Bot presence for self messages and `delivery.sent`; treat `reply_to_bot` as FAST.

- [ ] **Step 5: Verify and commit**

Run: `.venv/bin/python -m pytest tests/contracts/test_astrbot_events.py tests/social_runtime/test_group_world.py tests/social_runtime/test_attention.py -q`

```bash
git add groupmate/adapters/astrbot_events.py groupmate/social_runtime/persistence/event_store.py groupmate/social_runtime/manager.py groupmate/social_runtime/world.py groupmate/social_runtime/attention.py tests/contracts/test_astrbot_events.py tests/social_runtime/test_group_world.py
git commit -m "feat: resolve group conversation facts"

```

---

### Task 2: Reliable Ambient Wakeup

**Files:**
- Modify: `groupmate/social_runtime/scene_actor.py`
- Modify: `groupmate/social_runtime/event_fabric.py`
- Modify: `groupmate/social_runtime/manager.py`
- Modify: `groupmate/adapters/astrbot_bridge.py`
- Test: `tests/scenarios/test_attention_windows.py`

**Interfaces:**
- Produces `GroupSceneActor.pending_attention_deadline() -> Awaitable[int | None]`.
- Produces `SocialRuntimeManager.next_attention_deadline() -> Awaitable[int | None]`.
- Produces one bridge-owned wakeup task.

- [ ] **Step 1: Add a failing no-second-message test**

```python
def test_bridge_wakes_ambient_without_second_message(tmp_path):
    async def scenario():
        clock = FakeClock(100)
        bridge = configured_bridge(tmp_path, clock=clock)
        await bridge.start()
        await bridge.handle_event(ambient_event("m1"))
        clock.advance(3)
        bridge.notify_clock_advanced_for_test()
        await bridge.wait_until_idle_for_test()
        rows = bridge.manager.event_store.shadow_evaluations(
            bridge.settings.persona_id, "group-1"
        )
        await bridge.close()
        return rows
    assert asyncio.run(scenario())
```

- [ ] **Step 2: Verify failure**

Run: `.venv/bin/python -m pytest tests/scenarios/test_attention_windows.py -q`

- [ ] **Step 3: Expose deadline through actor mailbox and fabric**

Add an actor mailbox command whose handler reads the pending window. Fabric gathers actor deadlines and returns the minimum non-null value; Manager delegates.

- [ ] **Step 4: Implement one wakeup loop**

```python
async def _attention_wakeup_loop(self):
    while self._manager is not None:
        deadline = await self._manager.next_attention_deadline()
        if deadline is None:
            await self._attention_changed.wait()
        else:
            try:
                await asyncio.wait_for(
                    self._attention_changed.wait(),
                    max(0.0, deadline - self.clock()),
                )
            except TimeoutError:
                evaluations = await self._manager.drain(now=deadline)
                await self._handle_evaluations(evaluations)
        self._attention_changed.clear()
```

Start one task, set the event after inserted messages, and cancel/await it in `close()`.

- [ ] **Step 5: Verify and commit**

Run: `.venv/bin/python -m pytest tests/scenarios/test_attention_windows.py tests/scenarios/test_social_runtime_shadow.py -q`

```bash
git add groupmate/social_runtime/scene_actor.py groupmate/social_runtime/event_fabric.py groupmate/social_runtime/manager.py groupmate/adapters/astrbot_bridge.py tests/scenarios/test_attention_windows.py
git commit -m "feat: wake ambient attention windows"
```

---

### Task 3: AstrBot Model Port and P0 Workers

**Files:**
- Create: `groupmate/adapters/astrbot_models.py`
- Modify: `groupmate/social_runtime/cognition/astrbot_workers.py`
- Modify: `groupmate/social_runtime/cognition/service.py`
- Modify: `groupmate/adapters/astrbot_bridge.py`
- Test: `tests/contracts/test_cognitive_worker.py`
- Test: `tests/shared/test_plugin_skeleton.py`

**Interfaces:**
- Produces `AstrBotModelPort.complete_json(schema, payload)`.
- Produces `AstrBotModelPort.complete_text(system_prompt, prompt)`.
- Registers `direct_interaction`, `scene_interpreter`, `participation_assessor`.

- [ ] **Step 1: Write failing adapter and degradation tests**

```python
def test_model_port_uses_provider_and_parses_fence():
    context = FakeContext(fenced_json_response)
    port = AstrBotModelPort(context, "provider:text")
    value = asyncio.run(port.complete_json(
        schema={"type": "object"}, payload={"worker": "direct_interaction"}
    ))
    assert value == {"observations": []}
    assert context.calls[0]["chat_provider_id"] == "provider:text"

def test_missing_requested_worker_is_degraded(frame, context):
    service = CognitionService(workers={}, budget=CognitionBudget(2, 2))
    snapshot = asyncio.run(service.evaluate(frame, context))
    assert snapshot.degraded
    assert "worker_missing:direct_interaction" in snapshot.diagnostics
```

- [ ] **Step 2: Verify failure**

Run: `.venv/bin/python -m pytest tests/contracts/test_cognitive_worker.py tests/shared/test_plugin_skeleton.py -q`

- [ ] **Step 3: Implement the model port**

```python
async def complete_json(self, *, schema, payload):
    response = await self.context.llm_generate(
        chat_provider_id=self.provider_id,
        system_prompt=WORKER_SYSTEM_PROMPT,
        prompt=json.dumps({"schema": schema, "input": payload}, ensure_ascii=False),
        temperature=0.1,
    )
    return json.loads(strip_json_fence(str(response.completion_text or "")))

async def complete_text(self, *, system_prompt, prompt):
    response = await self.context.llm_generate(
        chat_provider_id=self.provider_id,
        system_prompt=system_prompt,
        prompt=prompt,
        temperature=0.7,
    )
    return str(response.completion_text or "").strip()
```

The worker prompt permits only `help_request`, `care_signal`, `humor_signal`, `greeting`, `boundary_signal`, and `participation_assessment`; it requires evidence IDs and forbids reply prose/reasoning.

- [ ] **Step 4: Register workers and mark missing roles**

Include `"worker": self.name` in structured worker input. Missing requested names append `worker_missing:<name>` and set `degraded=True`. Bridge injects all three workers.

- [ ] **Step 5: Verify and commit**

Run: `.venv/bin/python -m pytest tests/contracts/test_cognitive_worker.py tests/shared/test_plugin_skeleton.py tests/social_runtime/test_blackboard.py -q`

```bash
git add groupmate/adapters/astrbot_models.py groupmate/social_runtime/cognition/astrbot_workers.py groupmate/social_runtime/cognition/service.py groupmate/adapters/astrbot_bridge.py tests/contracts/test_cognitive_worker.py tests/shared/test_plugin_skeleton.py
git commit -m "feat: connect astrbot cognition workers"

```

---

### Task 4: Conservative Ambient Participation

**Files:**
- Modify: `groupmate/social_runtime/intentions.py`
- Modify: `groupmate/social_runtime/governor.py`
- Test: `tests/social_runtime/test_intentions.py`
- Test: `tests/social_runtime/test_governor.py`

**Interfaces:**
- Consumes `participation_assessment`: should-participate, target/topic confidence, disruption, novelty, repetition.
- Produces no Ambient candidate when rejected or either confidence is below 0.75.
- Governor selects at most one intention.

- [ ] **Step 1: Write failing gate tests**

```python
def test_assessment_vetoes_social_signal():
    board = snapshot_with(
        observation("humor_signal", confidence=0.9),
        observation("participation_assessment", proposition={
            "should_participate": False,
            "target_confidence": 0.9,
            "topic_confidence": 0.9,
            "disruption_cost": 0.7,
            "novelty": 0.0,
            "repetition_cost": 0.4,
        }),
    )
    assert IntentionEngine().propose(board, now=100) == ()

def test_governor_selects_one_primary_intention():
    result = SocialGovernor().decide((high_help(), high_care()), allowed_context())
    assert result.outcome == "ACT"
    assert len(result.selected_intention_ids) == 1
```

- [ ] **Step 2: Verify failure**

Run: `.venv/bin/python -m pytest tests/social_runtime/test_intentions.py tests/social_runtime/test_governor.py -q`

- [ ] **Step 3: Apply assessment and cap selection**

Use the freshest assessment. Reject on `should_participate=false` or confidence below 0.75. Merge bounded disruption, novelty, repetition, and uncertainty into social candidates. Ignore the assessment as a standalone intention. Keep the deterministic top Governor candidate and reject the rest.

- [ ] **Step 4: Verify and commit**

Run: `.venv/bin/python -m pytest tests/social_runtime/test_intentions.py tests/social_runtime/test_governor.py tests/scenarios/test_parallel_topic_governance.py -q`

```bash
git add groupmate/social_runtime/intentions.py groupmate/social_runtime/governor.py tests/social_runtime/test_intentions.py tests/social_runtime/test_governor.py
git commit -m "feat: gate ambient social participation"
```

---

### Task 5: Durable ReplyPlan and Async Generation Firewall

**Files:**
- Create: `groupmate/social_runtime/replying.py`
- Modify: `groupmate/social_runtime/manager.py`
- Create: `tests/social_runtime/actions/test_replying.py`

**Interfaces:**
- Produces immutable `ReplyPlan` with group, platform, session, topic, target, evidence, act, required, expiry, and style.
- Produces `ReplyPlanner.plan(evaluation, now) -> ReplyPlan | None`.
- Produces `ReplyExecutor.execute(plan: ReplyPlan, *, context_events: tuple[SocialEventEnvelope, ...], persona_profile: Mapping[str, object], recent_outputs: tuple[str, ...]) -> Awaitable[OutboxPart | None]`.
- Produces `ReplyPlanRepository.authorizes_bundle(bundle) -> bool`.

- [ ] **Step 1: Write failing reply tests**

```python
def test_planner_builds_one_short_plan(act_evaluation):
    plan = ReplyPlanner().plan(act_evaluation, now=100)
    assert plan.target_id == "u1"
    assert plan.style_directive.max_chars == 120

def test_optional_generation_failure_stays_silent(tmp_path):
    executor = reply_executor(tmp_path, model=FailingTextModel())
    part = asyncio.run(executor.execute(
        ambient_plan(), context_events=(), persona_profile={}, recent_outputs=()
    ))
    assert part is None
    assert executor.outbox.count() == 0
```

- [ ] **Step 2: Verify failure**

Run: `.venv/bin/python -m pytest tests/social_runtime/actions/test_replying.py -q`

- [ ] **Step 3: Implement plan and repository**

Create `reply_plans(plan_id PRIMARY KEY, correlation_id UNIQUE, persona_id, group_id, plan_json, status, created_at, expires_at)`. Identical saves are idempotent; identity reuse fails. Authorization requires matching persona/group/topic and bundle expiry within plan expiry.

Planner accepts only accepted, current ACT with exactly one selected candidate. `required=True` only for FAST. Use text-only `StyleDirective(max_chars=120, max_sentences=3, max_segments=2)`.

- [ ] **Step 4: Implement async generation, firewall, and bundle**

Call the text model once, review using existing `OutputFirewall`, and make at most one repair call. Optional failure returns no bundle; required failure uses the existing safe fallback. Commit one text part with `text`, `platform_id`, `session`, and optional `self_id`.

Manager creates the reply repository before Outbox and authorizes either a matching ActionPlan or ReplyPlan.

- [ ] **Step 5: Verify and commit**

Run: `.venv/bin/python -m pytest tests/social_runtime/actions/test_replying.py tests/social_runtime/actions/test_output_firewall.py tests/social_runtime/delivery/test_outbox.py -q`

```bash
git add groupmate/social_runtime/replying.py groupmate/social_runtime/manager.py tests/social_runtime/actions/test_replying.py
git commit -m "feat: create durable social replies"

```

---

### Task 6: OneBot Delivery and Production Composition

**Files:**
- Create: `groupmate/adapters/astrbot_delivery.py`
- Modify: `groupmate/adapters/onebot_delivery.py`
- Modify: `groupmate/adapters/astrbot_bridge.py`
- Modify: `_conf_schema.json`
- Test: `tests/recovery/test_production_fault_matrix.py`
- Test: `tests/shared/test_plugin_skeleton.py`

**Interfaces:**
- Produces `AstrBotOneBotSender.__call__(*, group_id: str, segments: list[dict], idempotency_key: str, platform_id: str, self_id: str | None = None) -> Awaitable[Mapping[str, object]]`.
- Bridge plans every accepted ACT; only SOCIAL_RUNTIME executes and dispatches.

- [ ] **Step 1: Write failing route and ACT tests**

```python
def test_sender_uses_selected_platform():
    context = FakeContext(platform_id="onebot-main", response={"message_id": 77})
    result = asyncio.run(AstrBotOneBotSender(context)(
        group_id="885617919",
        segments=[{"type": "text", "data": {"text": "在"}}],
        idempotency_key="reply:1",
        platform_id="onebot-main",
    ))
    assert result["message_id"] == 77

def test_live_act_reaches_sent_outbox_and_feedback(tmp_path):
    bridge = live_bridge(tmp_path, model=FixedModel(), onebot=SuccessfulOneBot())
    asyncio.run(deliver_direct_help(bridge))
    parts = bridge.manager.outbox.receipted_parts()
    assert len(parts) == 1 and parts[0].status is OutboxStatus.SENT
    assert any(
        event_id.startswith("delivery-feedback:")
        for event_id in bridge.manager.event_store.event_ids()
    )
```

- [ ] **Step 2: Verify failure**

Run: `.venv/bin/python -m pytest tests/recovery/test_production_fault_matrix.py tests/shared/test_plugin_skeleton.py -q`

- [ ] **Step 3: Implement the AstrBot/OneBot boundary**

```python
async def __call__(
    self, *, group_id, segments, idempotency_key, platform_id, self_id=None
):
    del idempotency_key
    platform = self.context.get_platform_inst(platform_id)
    client = None if platform is None else platform.get_client()
    if client is None or not callable(getattr(client, "send_group_msg", None)):
        raise PermanentOneBotError("onebot_platform_unavailable")
    routing = {"self_id": self_id} if self_id else {}
    return await client.send_group_msg(
        group_id=int(group_id), message=segments, **routing
    )
```

Forward route fields from `OneBotDeliveryAdapter`; preserve UNKNOWN for ambiguous exceptions.

- [ ] **Step 4: Compose replies and feedback in Bridge**

Both message handling and Ambient wakeup call `_handle_evaluations()`. Save plans in SHADOW; execute only in SOCIAL_RUNTIME. Configure Dispatcher with `receipt_handler=manager.coordinator.apply_delivery_receipt` so delivery feedback re-enters Event Fabric. Dispatch until no ready part remains.

- [ ] **Step 5: Expose external trigger ownership through native config**

Add list settings `external_command_prefixes` and `external_link_domains` to `_conf_schema.json`, with `rule=owner` examples. Do not touch `pages/`.

- [ ] **Step 6: Verify and commit**

Run: `.venv/bin/python -m pytest tests/recovery/test_production_fault_matrix.py tests/shared/test_plugin_skeleton.py tests/social_runtime/delivery/test_dispatcher.py -q`

```bash
git add groupmate/adapters/astrbot_delivery.py groupmate/adapters/onebot_delivery.py groupmate/adapters/astrbot_bridge.py _conf_schema.json tests/recovery/test_production_fault_matrix.py tests/shared/test_plugin_skeleton.py
git commit -m "feat: deliver social replies through astrbot"
```

---

### Task 7: Minimal Mainline Acceptance

**Files:**
- Create: `tests/scenarios/test_chat_mainline.py`
- Modify only for proven integration defects: files from Tasks 1-6

**Interfaces:** Verifies production composition with fake AstrBot model and OneBot client; no UI assertions or broad permutations.

- [ ] **Step 1: Add exactly four acceptance assertions**

```python
def test_direct_reply_reaches_onebot_once():
    result = asyncio.run(run_mainline_case("direct_help"))
    assert result.onebot_calls == 1
    assert result.sent_parts == 1
    assert result.delivery_feedback == 1

def test_ambient_wakes_and_uncertainty_stays_silent():
    result = asyncio.run(run_mainline_case("ambient_uncertain"))
    assert result.ambient_evaluations == 1
    assert result.onebot_calls == 0

def test_external_command_and_video_link_skip_cognition_and_outbox():
    result = asyncio.run(run_mainline_case("external_owned"))
    assert result.worker_calls == 0
    assert result.outbox_parts == 0

def test_bot_echo_updates_presence_without_attention():
    result = asyncio.run(run_mainline_case("bot_echo"))
    assert result.last_bot_event_at == 104
    assert result.new_worker_calls == 0
```

Define `MainlineResult` as a frozen dataclass with the asserted integer fields, and implement `run_mainline_case(name)` in the same test file using one fake AstrBot Context, one fake OneBot client, and a fake clock. The direct case checks one text segment, one SENT part, and feedback. Ambient advances the fake clock without sleeping. External rules use `bq=astrbot.meme` and `v.douyin.com=astrbot.video`. Bot echo ingests a self-authored platform event at timestamp 104 and records the worker-call delta.

- [ ] **Step 2: Run the mainline test**

Run: `.venv/bin/python -m pytest tests/scenarios/test_chat_mainline.py -q`

Expected: 4 passed.

- [ ] **Step 3: Run the bounded regression set once**

```bash
.venv/bin/python -m pytest tests/contracts/test_astrbot_events.py tests/contracts/test_cognitive_worker.py tests/scenarios/test_attention_windows.py tests/scenarios/test_chat_mainline.py tests/scenarios/test_social_runtime_shadow.py tests/social_runtime/test_group_world.py tests/social_runtime/test_attention.py tests/social_runtime/test_intentions.py tests/social_runtime/test_governor.py tests/social_runtime/actions/test_replying.py tests/social_runtime/delivery/test_dispatcher.py tests/social_runtime/delivery/test_outbox.py -q
```

Expected: selected tests pass. Do not run page tests or the full suite.

- [ ] **Step 4: Verify scope and commit**

```bash
git diff --check
git diff --name-only HEAD~6..HEAD -- pages
git add tests/scenarios/test_chat_mainline.py
git commit -m "test: cover group chat mainline"
```

Expected: no `pages/` path. Report focused coverage only.
