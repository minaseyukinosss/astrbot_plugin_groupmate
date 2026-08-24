# Participation Policy and Dialogue Lease Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make explicit interactions and short dialogue continuations produce reliable, explainable reply candidates while keeping ambient participation model-gated and SHADOW side-effect free.

**Architecture:** Add a deterministic ParticipationPolicy between cognition and SocialGovernor, persist a bounded ConversationLease in GroupWorld, and reserve structured model workers for AMBIENT understanding. Accepted scene evaluations atomically persist safe attention, cognition, candidate, lane, and governor evidence; ReplyPreview or generated delivery plans advance the lease through the existing event fabric.

**Tech Stack:** Python 3.13, asyncio, dataclasses, SQLite/WAL, pytest, existing Social Runtime v2 event fabric and AstrBot bridge.

---

## File map

- Create `groupmate/social_runtime/participation.py`: participation lanes and deterministic candidate policy.
- Create `tests/social_runtime/test_participation.py`: focused policy tests.
- Modify `groupmate/social_runtime/world.py`: durable ConversationLease projection and backward-compatible snapshots.
- Modify `groupmate/social_runtime/attention.py`: CONTINUATION attention lane and no structured worker for direct chat.
- Modify `groupmate/social_runtime/cognition/service.py`: concurrent AMBIENT workers with deterministic result integration.
- Modify `groupmate/social_runtime/manager.py`: bounded context, ParticipationPolicy integration, evaluation evidence, lease events.
- Modify `groupmate/social_runtime/scene_actor.py`: carry and safely serialize accepted cognition/candidates/lane.
- Modify `groupmate/social_runtime/persistence/event_store.py`: atomically write accepted attention, observations, candidates, and governor result.
- Modify `groupmate/social_runtime/replying.py`: retain participation lane in ReplyPlan with backward-compatible decoding.
- Modify `groupmate/adapters/astrbot_bridge.py`: advance lease only after READY preview or generated delivery plan.
- Modify `groupmate/social_runtime/control/message_traces.py`: expose lane, would-reply, candidate source, and diagnostics.
- Modify targeted tests under `tests/social_runtime/`, `tests/scenarios/`, and `tests/contracts/`.

### Task 1: Persist a bounded conversation lease in GroupWorld

**Files:**
- Modify: `groupmate/social_runtime/world.py`
- Modify: `tests/social_runtime/test_group_world.py`

- [ ] **Step 1: Write failing projection and compatibility tests**

Add tests that project a `conversation.lease_opened` event, advance it, reject a mismatched advance, and restore an old snapshot without the new field:

```python
def test_conversation_lease_opens_and_advances_without_resetting_turn_budget():
    projector = GroupWorldProjector()
    state = projector.empty("885617919")
    opened = _lease_event("open", "conversation.lease_opened", remaining_turns=5)
    advanced = _lease_event("next", "conversation.lease_advanced", remaining_turns=4)

    state = projector.apply(state, opened)
    state = projector.apply(state, advanced)

    assert state.conversation_lease is not None
    assert state.conversation_lease.target_id == "u1"
    assert state.conversation_lease.topic_id == "m1"
    assert state.conversation_lease.source_plan_id == "reply:next"
    assert state.conversation_lease.remaining_turns == 4


def test_old_world_snapshot_restores_with_no_conversation_lease():
    projector = GroupWorldProjector()
    payload = projector.to_dict(projector.empty("885617919"))
    payload.pop("conversation_lease")

    restored = projector.from_dict(payload)

    assert restored.conversation_lease is None
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
.venv/bin/pytest tests/social_runtime/test_group_world.py -q
```

Expected: FAIL because `conversation_lease` and lease event projection do not exist.

- [ ] **Step 3: Implement the immutable lease projection**

Add this contract and field:

```python
@dataclass(frozen=True)
class ConversationLease:
    target_id: str
    topic_id: str
    source_plan_id: str
    opened_at: int
    expires_at: int
    remaining_turns: int


@dataclass(frozen=True)
class GroupWorldState:
    # existing fields remain unchanged
    conversation_lease: ConversationLease | None = None
```

In `GroupWorldProjector.apply()`, project only validated `conversation.lease_opened` and `conversation.lease_advanced` payloads. Opening accepts 1–5 remaining turns; advancing must match the existing target/topic and strictly reduce the remaining count. Invalid lease events leave the lease unchanged. `from_dict()` must use `payload.get("conversation_lease")` so existing snapshots remain readable.

- [ ] **Step 4: Run the tests and verify GREEN**

Run:

```bash
.venv/bin/pytest tests/social_runtime/test_group_world.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add groupmate/social_runtime/world.py tests/social_runtime/test_group_world.py
git commit -m "feat: persist bounded conversation leases"
```

### Task 2: Add DIRECT_FAST and CONTINUATION attention lanes

**Files:**
- Modify: `groupmate/social_runtime/attention.py`
- Modify: `tests/social_runtime/test_attention.py`

- [ ] **Step 1: Write failing lane-selection tests**

Add tests proving ordinary direct chat has no structured worker, a matching live lease creates CONTINUATION immediately, and mismatched/expired leases stay AMBIENT:

```python
def test_direct_chat_fast_frame_does_not_request_structured_cognition():
    event = _event(payload={"text": "在吗", "mentions_bot": True})
    frame = AttentionScheduler().on_event(event, _world_with(event), _persona(), 100)[0]
    assert frame.trigger_kind == "FAST"
    assert frame.requested_workers == ()


def test_matching_live_lease_creates_continuation_frame_without_waiting():
    world = _world_with_lease(target_id="u1", topic_id="m1", expires_at=280)
    event = _event("m2", actor_id="u1", occurred_at=120,
                   payload={"text": "然后呢", "reply_to": "m1"})
    world = GroupWorldProjector().apply(world, event)

    frame = AttentionScheduler().on_event(event, world, _persona(), 120)[0]

    assert frame.trigger_kind == "CONTINUATION"
    assert frame.requested_workers == ()
```

- [ ] **Step 2: Run and verify RED**

```bash
.venv/bin/pytest tests/social_runtime/test_attention.py -q
```

Expected: direct frames still request `direct_interaction`; continuation is treated as AMBIENT.

- [ ] **Step 3: Implement lane selection**

Change ordinary direct chat `_fast_workers()` to return `()`, while safety and capability frames retain their existing workers. Before opening an AMBIENT window, compare `world.conversation_lease` to the projected event actor/topic/current time and create an immediate `CONTINUATION` frame when all lease constraints pass.

```python
if self._matches_conversation_lease(event, world, now):
    return (self._frame(
        event=event,
        world=world,
        persona=persona,
        trigger_kind="CONTINUATION",
        urgency="high",
        deadline=now,
        requested_workers=(),
    ),)
```

- [ ] **Step 4: Run and verify GREEN**

```bash
.venv/bin/pytest tests/social_runtime/test_attention.py -q
```

Expected: all attention tests pass.

- [ ] **Step 5: Commit**

```bash
git add groupmate/social_runtime/attention.py tests/social_runtime/test_attention.py
git commit -m "feat: classify direct and continuation attention"
```

### Task 3: Introduce the deterministic ParticipationPolicy

**Files:**
- Create: `groupmate/social_runtime/participation.py`
- Create: `tests/social_runtime/test_participation.py`
- Modify: `groupmate/social_runtime/intentions.py`

- [ ] **Step 1: Write failing policy tests**

Cover deterministic identity, sufficient utility, degraded direct/continuation behavior, and fail-closed AMBIENT:

```python
def test_direct_fast_proposes_actionable_candidate_without_model_observation():
    proposal = ParticipationPolicy().propose(
        _frame("FAST", requested_workers=()),
        _blackboard(degraded=True),
        now=100,
    )
    assert proposal.lane is ParticipationLane.DIRECT_FAST
    assert proposal.allow_degraded is True
    assert [item.kind for item in proposal.candidates] == ["ACKNOWLEDGE"]
    assert SocialGovernor.utility(proposal.candidates[0]) >= 1.0


def test_ambient_degradation_remains_observe_only():
    proposal = ParticipationPolicy().propose(
        _frame("AMBIENT", requested_workers=("scene_interpreter",)),
        _blackboard(degraded=True),
        now=100,
    )
    assert proposal.lane is ParticipationLane.AMBIENT
    assert proposal.allow_degraded is False
    assert all(item.kind == "OBSERVE" for item in proposal.candidates)
```

- [ ] **Step 2: Run and verify RED**

```bash
.venv/bin/pytest tests/social_runtime/test_participation.py -q
```

Expected: import failure because ParticipationPolicy does not exist.

- [ ] **Step 3: Implement the policy**

Define:

```python
class ParticipationLane(str, Enum):
    DIRECT_FAST = "DIRECT_FAST"
    CONTINUATION = "CONTINUATION"
    AMBIENT = "AMBIENT"


@dataclass(frozen=True)
class ParticipationProposal:
    lane: ParticipationLane
    candidates: tuple[CandidateIntention, ...]
    allow_degraded: bool
    diagnostics: tuple[str, ...]
```

`FAST` with no requested structured worker creates `ACKNOWLEDGE / respond_to_direct_interaction`; `CONTINUATION` creates `CONTINUE / continue_dialogue`; other lanes delegate to `IntentionEngine.propose()`. Build deterministic IDs from lane, target, topic, evidence, act, and expiry. Add `CONTINUE` to no special hard-gate category; existing target/topic/privacy/paused/boundary rules still apply.

- [ ] **Step 4: Run policy and governor tests**

```bash
.venv/bin/pytest tests/social_runtime/test_participation.py tests/social_runtime/test_governor.py tests/social_runtime/test_intentions.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add groupmate/social_runtime/participation.py groupmate/social_runtime/intentions.py tests/social_runtime/test_participation.py
git commit -m "feat: add deterministic participation policy"
```

### Task 4: Run AMBIENT cognition concurrently with a bounded context

**Files:**
- Modify: `groupmate/social_runtime/cognition/service.py`
- Modify: `groupmate/social_runtime/manager.py`
- Modify: `tests/contracts/test_cognitive_worker.py`
- Modify: `tests/scenarios/test_social_runtime_shadow.py`

- [ ] **Step 1: Write failing concurrency and context tests**

Use two workers that signal entry and wait on a shared release event. The test must prove both entered before either is released, without asserting fragile elapsed milliseconds. Add a capturing worker that verifies `world_summary` contains only relevant topics/audiences/activity/persona/lease and does not contain full `participants` or `interaction_edges`.

```python
async def scenario():
    both_entered = asyncio.Event()
    workers = {
        name: BarrierWorker(name, entered, both_entered, release)
        for name, entered in (("scene_interpreter", first),
                              ("participation_assessor", second))
    }
    task = asyncio.create_task(service.evaluate(frame, context))
    await asyncio.wait_for(both_entered.wait(), timeout=1)
    release.set()
    return await task

assert all(item.status == "SUCCEEDED" for item in result.worker_diagnostics)
```

- [ ] **Step 2: Run and verify RED**

```bash
.venv/bin/pytest tests/contracts/test_cognitive_worker.py tests/scenarios/test_social_runtime_shadow.py -q
```

Expected: barrier times out under serial execution and the capturing worker sees the full world.

- [ ] **Step 3: Refactor worker invocation and context projection**

Split model invocation from Blackboard mutation: concurrently collect `CognitiveWorkerResult` objects under `_WorkerConcurrencyGate`, then add observations to the board in requested-worker order. Admit workers against call/cost budgets before creating tasks so budget behavior remains deterministic.

Add `SocialRuntimeManager._cognitive_world_view(request, frame, profile)` returning only:

```python
{
    "topics": relevant_topics,
    "audiences": relevant_participants,
    "group_activity": asdict(world.group_activity),
    "last_bot_event_at": world.recent_presence.last_bot_event_at,
    "conversation_lease": asdict(world.conversation_lease) if present else None,
    "persona_profile": profile.to_mapping(),
}
```

Set constraints to `("no_side_effects", "evidence_required")`; never include `shadow_only` in either runtime mode.

- [ ] **Step 4: Run and verify GREEN**

```bash
.venv/bin/pytest tests/contracts/test_cognitive_worker.py tests/scenarios/test_social_runtime_shadow.py tests/evaluation/test_load_budget.py -q
```

Expected: concurrent admission, bounded context, and budget tests pass.

- [ ] **Step 5: Commit**

```bash
git add groupmate/social_runtime/cognition/service.py groupmate/social_runtime/manager.py tests/contracts/test_cognitive_worker.py tests/scenarios/test_social_runtime_shadow.py
git commit -m "perf: bound and parallelize ambient cognition"
```

### Task 5: Integrate participation and atomically persist accepted evidence

**Files:**
- Modify: `groupmate/social_runtime/manager.py`
- Modify: `groupmate/social_runtime/scene_actor.py`
- Modify: `groupmate/social_runtime/persistence/event_store.py`
- Modify: `tests/scenarios/test_social_runtime_shadow.py`
- Modify: `tests/recovery/test_stale_cognition.py`

- [ ] **Step 1: Write failing accepted/stale persistence tests**

For an accepted direct evaluation, assert one attention frame, at least the LevelZero observation, one candidate, a participation lane, and one governor row. For a stale result, assert none of those accepted cognitive rows are written.

```python
with connect_database(path) as db:
    counts = {
        table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("attention_frames", "cognitive_observations",
                      "candidate_intentions", "governor_results")
    }
assert counts == {
    "attention_frames": 1,
    "cognitive_observations": 1,
    "candidate_intentions": 1,
    "governor_results": 1,
}
```

Also assert serialized rows do not contain `chain_of_thought`, `prompt`, `url`, or raw model output.

- [ ] **Step 2: Run and verify RED**

```bash
.venv/bin/pytest tests/scenarios/test_social_runtime_shadow.py tests/recovery/test_stale_cognition.py -q
```

Expected: cognitive and candidate tables remain empty.

- [ ] **Step 3: Integrate ParticipationPolicy and safe evidence**

In `_evaluate_cycle()`, call ParticipationPolicy after cognition and use `proposal.allow_degraded` when computing `force_observe`. Add lane, proposal diagnostics, observations, and candidates to `ShadowEvaluation` and `SceneWorkResult`.

In `GroupSceneActor._evaluation_payload()`, whitelist observation proposition keys and serialize:

```python
{
    "frame": safe_frame,
    "participation_lane": result.participation_lane,
    "participation_diagnostics": list(result.participation_diagnostics),
    "cognitive_observations": safe_observations,
    "candidates": [asdict(item) for item in result.candidates],
    "governor_result": safe_governor,
}
```

In `_insert_shadow_evaluation()`, insert frame, observations, candidates, journal, and governor rows in the existing `BEGIN IMMEDIATE` transaction. Derive observation IDs from canonical safe JSON and reject partial or conflicting identity reuse. Stale resolution continues to pass `evaluation=None`, so it cannot write accepted cognition.

- [ ] **Step 4: Run and verify GREEN**

```bash
.venv/bin/pytest tests/scenarios/test_social_runtime_shadow.py tests/recovery/test_stale_cognition.py tests/shared/test_memory_privacy.py -q
```

Expected: persistence, stale, and privacy tests pass.

- [ ] **Step 5: Commit**

```bash
git add groupmate/social_runtime/manager.py groupmate/social_runtime/scene_actor.py groupmate/social_runtime/persistence/event_store.py tests/scenarios/test_social_runtime_shadow.py tests/recovery/test_stale_cognition.py
git commit -m "feat: persist accepted participation evidence"
```

### Task 6: Advance leases only after a usable reply exists

**Files:**
- Modify: `groupmate/social_runtime/replying.py`
- Modify: `groupmate/social_runtime/manager.py`
- Modify: `groupmate/adapters/astrbot_bridge.py`
- Modify: `tests/social_runtime/actions/test_replying.py`
- Modify: `tests/scenarios/test_chat_mainline.py`

- [ ] **Step 1: Write failing ReplyPlan and bridge tests**

Assert ReplyPlan retains `participation_lane`, old serialized plans decode with `AMBIENT`, READY SHADOW preview opens a five-turn lease, MODEL_FAILED/REJECTED preview does not, and a generated SOCIAL_RUNTIME part opens the same lease before delivery. The SHADOW scenario must then ingest a natural same-member/same-topic follow-up and assert it produces a CONTINUATION evaluation without any structured cognition call.

```python
assert snapshot.conversation_lease is not None
assert snapshot.conversation_lease.target_id == "u1"
assert snapshot.conversation_lease.topic_id == "m1"
assert snapshot.conversation_lease.remaining_turns == 5
assert context.client.calls == []  # SHADOW still never sends
```

- [ ] **Step 2: Run and verify RED**

```bash
.venv/bin/pytest tests/social_runtime/actions/test_replying.py tests/scenarios/test_chat_mainline.py -q
```

Expected: ReplyPlan has no lane and GroupWorld has no lease after preview/generation.

- [ ] **Step 3: Implement lane propagation and lease events**

Add `participation_lane` to ReplyPlan and default it to `AMBIENT` in repository decode for old rows. ReplyPlanner copies the evaluation lane.

Add a backward-compatible `ReplyExecutionResult` and `execute_with_result()` API:

```python
@dataclass(frozen=True)
class ReplyExecutionResult:
    part: OutboxPart | None
    status: str
    diagnostic_code: str | None = None

    @property
    def usable_for_lease(self) -> bool:
        return self.status == "READY" and self.part is not None
```

Keep `execute()` returning `OutboxPart | None` by delegating to `execute_with_result()` so existing callers remain compatible. A required fallback may still produce an OutboxPart, but its result status is `MODEL_FAILED` and therefore cannot open a lease.

Add `SocialRuntimeManager.record_usable_reply(plan, now)` that emits a deterministic `conversation.lease_opened` event for a new/direct/ambient plan and `conversation.lease_advanced` for a matching continuation. It sets 180-second expiry, starts with five remaining future Bot turns, and decrements without resetting for continuation.

In the bridge:

- after SHADOW preview, call it only when `preview.status == "READY"`;
- after SOCIAL_RUNTIME `execute_with_result`, call it only when `result.usable_for_lease` is true;
- never call it on MODEL_FAILED/REJECTED/silent generation.

- [ ] **Step 4: Run and verify GREEN**

```bash
.venv/bin/pytest tests/social_runtime/actions/test_replying.py tests/scenarios/test_chat_mainline.py tests/shared/test_shadow_side_effects.py -q
```

Expected: lease lifecycle works and SHADOW side-effect tests pass.

- [ ] **Step 5: Commit**

```bash
git add groupmate/social_runtime/replying.py groupmate/social_runtime/manager.py groupmate/adapters/astrbot_bridge.py tests/social_runtime/actions/test_replying.py tests/scenarios/test_chat_mainline.py
git commit -m "feat: open dialogue lease from usable replies"
```

### Task 7: Expose explainable participation in message traces

**Files:**
- Modify: `groupmate/social_runtime/control/message_traces.py`
- Modify: `tests/contracts/test_message_traces.py`
- Modify: `tests/contracts/test_message_trace_bridge.py`

- [ ] **Step 1: Write failing public-trace tests**

Assert evaluation traces include stable, reader-safe fields:

```python
assert detail["decision"]["participation_lane"] == "DIRECT_FAST"
assert detail["decision"]["would_reply"] is True
assert detail["understanding"]["candidate_count"] == 1
assert detail["understanding"]["candidate_source"] == "deterministic"
assert "chain_of_thought" not in json.dumps(detail)
```

- [ ] **Step 2: Run and verify RED**

```bash
.venv/bin/pytest tests/contracts/test_message_traces.py tests/contracts/test_message_trace_bridge.py -q
```

Expected: lane, would-reply, and candidate source are absent.

- [ ] **Step 3: Add safe trace projection fields**

Map `ACT` to `would_reply=True`, all other outcomes to false; expose lane, candidate count, candidate source (`deterministic`, `model`, or `none`), worker diagnostics, and reply diagnostic. Do not expose candidate feature internals, prompt, CoT, platform URL, or raw identity beyond existing opaque participant references.

- [ ] **Step 4: Run and verify GREEN**

```bash
.venv/bin/pytest tests/contracts/test_message_traces.py tests/contracts/test_message_trace_bridge.py tests/contracts/test_trace_web_api.py -q
```

Expected: trace contracts pass.

- [ ] **Step 5: Commit**

```bash
git add groupmate/social_runtime/control/message_traces.py tests/contracts/test_message_traces.py tests/contracts/test_message_trace_bridge.py
git commit -m "feat: expose participation decisions in traces"
```

### Task 8: Run the bounded final verification

**Files:**
- Verify only; production and behavior tests were completed test-first in Tasks 1–7.

- [ ] **Step 1: Run targeted final verification**

```bash
.venv/bin/pytest \
  tests/social_runtime/test_group_world.py \
  tests/social_runtime/test_attention.py \
  tests/social_runtime/test_participation.py \
  tests/contracts/test_cognitive_worker.py \
  tests/scenarios/test_social_runtime_shadow.py \
  tests/scenarios/test_chat_mainline.py \
  tests/shared/test_shadow_side_effects.py \
  tests/contracts/test_message_traces.py \
  tests/contracts/test_message_trace_bridge.py -q
```

Expected: all selected tests pass with no warnings or leaked coroutine tasks.

- [ ] **Step 2: Run static and diff checks**

```bash
.venv/bin/python -m compileall -q groupmate
git diff --check
git status --short
```

Expected: compile and diff checks succeed; only intentional analysis artifacts may remain untracked.

- [ ] **Step 3: Confirm commit and workspace state**

Run `git log -8 --oneline` and confirm each completed task has its scoped commit. Do not stage or commit the untracked `analysis/` directory.

## Online SHADOW acceptance after installation

The implementation is code-complete after Task 8, but intelligence improvement is confirmed only after a new bounded SHADOW sample records:

- nonzero `DIRECT_FAST` and `CONTINUATION` candidates;
- direct eligible-message candidate rate at or above 99%, excluding explicit hard-gate rejections;
- zero actionable AMBIENT candidate when required model workers fail;
- nonzero rows in attention, cognition, and candidate tables;
- DIRECT_FAST participation decision P50 ≤ 1 second and P90 ≤ 3 seconds;
- AMBIENT participation decision P50 ≤ 12 seconds and P90 ≤ 15 seconds;
- SHADOW OneBot send and ready Outbox counts remain zero.

Candidate generation, final reply generation, and delivery timing must be reported separately so a slow text model cannot be mistaken for a slow participation decision.
