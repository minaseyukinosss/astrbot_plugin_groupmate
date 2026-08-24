# SHADOW Observability, Message Media, and Runtime UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make SHADOW record a real pre-delivery decision and candidate reply while remaining no-send, resolve @ targets and media previews safely, add immediate refresh, and restyle the runtime center to the approved neutral visual system.

**Architecture:** Extend existing immutable evaluation and message-trace JSON contracts instead of adding a parallel pipeline. The manager owns structured cognition diagnostics, the AstrBot bridge owns reply preview and bounded background media work, repositories own private identity/media resolution, and the existing runtime workspace renders the resulting public projection.

**Tech Stack:** Python 3.11+, asyncio, SQLite JSON projections, AstrBot/OneBot adapters, browser-native ES modules, CSS custom properties, pytest, Node module tests.

---

### Task 1: Persist Structured Cognition Diagnostics

**Files:**
- Modify: `groupmate/social_runtime/cognition/contracts.py`
- Modify: `groupmate/social_runtime/cognition/blackboard.py`
- Modify: `groupmate/social_runtime/cognition/service.py`
- Modify: `groupmate/social_runtime/manager.py`
- Test: `tests/contracts/test_cognitive_worker.py`
- Test: `tests/social_runtime/test_blackboard.py`

- [ ] **Step 1: Write failing diagnostics tests**

Add assertions that a timed-out worker produces a JSON-safe diagnostic with worker name, `TIMED_OUT`, latency, and `worker_timeout`, while a completed worker produces `SUCCEEDED`.

```python
assert snapshot.worker_diagnostics[0].status == "TIMED_OUT"
assert snapshot.worker_diagnostics[0].diagnostic_code == "worker_timeout"
assert snapshot.worker_diagnostics[0].latency_ms >= 0
```

- [ ] **Step 2: Run only the cognition tests and verify failure**

Run: `pytest -q tests/contracts/test_cognitive_worker.py tests/social_runtime/test_blackboard.py`

Expected: failure because `worker_diagnostics` does not exist.

- [ ] **Step 3: Add the immutable diagnostic contract**

Add a frozen `CognitiveWorkerDiagnostic` dataclass with validated status values, timestamps, latency, and stable diagnostic code. Add `worker_diagnostics` to `BlackboardSnapshot` and make `CognitionService._run_worker` return both completion state and diagnostic.

```python
@dataclass(frozen=True)
class CognitiveWorkerDiagnostic:
    worker: str
    status: str
    started_at: int
    completed_at: int
    latency_ms: int
    diagnostic_code: str | None = None
```

- [ ] **Step 4: Carry diagnostics through ShadowEvaluation evidence**

Add `cognition_diagnostics` to `ShadowEvaluation`, encode it in `to_capture_evidence`, decode it in `from_capture_evidence`, and populate it from the blackboard in `_evaluate_cycle`. External compatibility evaluations use an empty tuple.

- [ ] **Step 5: Run the focused tests**

Run: `pytest -q tests/contracts/test_cognitive_worker.py tests/social_runtime/test_blackboard.py tests/scenarios/test_social_runtime_shadow.py`

Expected: all selected tests pass.

- [ ] **Step 6: Commit**

```bash
git add groupmate/social_runtime/cognition groupmate/social_runtime/manager.py tests/contracts/test_cognitive_worker.py tests/social_runtime/test_blackboard.py tests/scenarios/test_social_runtime_shadow.py
git commit -m "feat: persist cognition worker diagnostics"
```

### Task 2: Separate SHADOW Decision from Delivery and Generate a Safe Preview

**Files:**
- Modify: `groupmate/social_runtime/replying.py`
- Modify: `groupmate/adapters/astrbot_bridge.py`
- Modify: `groupmate/social_runtime/control/message_traces.py`
- Modify: `eval/shadow.py`
- Test: `tests/social_runtime/actions/test_replying.py`
- Test: `tests/contracts/test_message_traces.py`
- Test: `tests/evaluation/test_shadow_review.py`
- Test: `tests/scenarios/test_chat_mainline.py`

- [ ] **Step 1: Write failing no-send preview tests**

Create an ACT evaluation in SHADOW and assert that preview generation returns accepted text without creating an Outbox part. Assert trace state contains `decision.pre_gate_outcome == "ACT"`, `delivery.status == "BLOCKED_BY_SHADOW"`, candidate target/intent/text, and diagnostics.

```python
preview = await executor.preview(plan, context_events=events, persona_profile={})
assert preview.text == "晚上好呀"
assert outbox.pending() == ()
assert trace["decision"]["pre_gate_outcome"] == "ACT"
assert trace["delivery"]["status"] == "BLOCKED_BY_SHADOW"
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `pytest -q tests/social_runtime/actions/test_replying.py tests/contracts/test_message_traces.py tests/evaluation/test_shadow_review.py`

Expected: failure because preview and split delivery fields are absent.

- [ ] **Step 3: Extract draft generation from enqueueing**

Add a `ReplyPreview` value object and `ReplyExecutor.preview(...)`. Reuse the existing prompt, repair, and OutputFirewall path; do not save a bundle or call `OutboxService.commit_bundle`.

```python
@dataclass(frozen=True)
class ReplyPreview:
    text: str | None
    status: str
    diagnostic_code: str | None = None
```

- [ ] **Step 4: Generate SHADOW preview in the bridge**

For a SHADOW ACT plan, save the plan, call `preview`, then update both message trace and accepted shadow review evidence before reconciliation. Keep all real sending code behind `RuntimeMode.SOCIAL_RUNTIME`.

- [ ] **Step 5: Extend public trace and review capture**

Replace the misleading `forced_observe` label with a cognition/governance label. Store pre-gate outcome, selected target, intent, preview, diagnostics, and independent delivery status. In `eval/shadow.py`, populate `candidate_response` from the preview instead of hard-coding `None`.

- [ ] **Step 6: Run the focused tests**

Run: `pytest -q tests/social_runtime/actions/test_replying.py tests/contracts/test_message_traces.py tests/evaluation/test_shadow_review.py tests/scenarios/test_chat_mainline.py`

Expected: all selected tests pass and SHADOW Outbox remains empty.

- [ ] **Step 7: Commit**

```bash
git add groupmate/social_runtime/replying.py groupmate/adapters/astrbot_bridge.py groupmate/social_runtime/control/message_traces.py eval/shadow.py tests
git commit -m "feat: expose shadow pre-delivery decisions"
```

### Task 3: Resolve Mentioned Members and Make Media Failures Recoverable

**Files:**
- Modify: `groupmate/adapters/participants.py`
- Modify: `groupmate/adapters/message_media.py`
- Modify: `groupmate/social_runtime/control/message_traces.py`
- Modify: `groupmate/adapters/web_api.py`
- Test: `tests/contracts/test_participants.py`
- Test: `tests/contracts/test_message_traces.py`
- Test: `tests/contracts/test_trace_web_api.py`

- [ ] **Step 1: Write failing mention and media-state tests**

Record an earlier sender, then a later message that @ mentions the sender. Assert the public part contains nickname, member ref, and avatar ref but not QQ ID. Add cases for `qq=all` and unresolved members. Assert rejected media returns a stable public error code and the same scoped ref can be retried.

```python
assert mention == {
    "kind": "at",
    "label": "@夏夏",
    "member_ref": member["member_ref"],
    "avatar_ref": member["avatar_ref"],
    "display_name": "夏夏",
}
assert "42" not in str(mention)
```

- [ ] **Step 2: Run the contract tests and verify failure**

Run: `pytest -q tests/contracts/test_participants.py tests/contracts/test_message_traces.py tests/contracts/test_trace_web_api.py`

Expected: failure because mention resolution and stable media state are absent.

- [ ] **Step 3: Add scoped participant lookup**

Implement `ParticipantDirectory.resolve_actor(persona_id, group_id, actor_id)` returning the existing safe public identity. Make `MessageMediaDirectory.remember` accept a mention resolver and convert `at` segments without leaking `qq`.

- [ ] **Step 4: Add public media availability state**

Return stable `error_code` values from `/media`; do not cache frontend failures permanently. Preserve SSRF, MIME, size, redirect, and scope checks. Treat a later GET as an explicit retry for the same opaque reference.

- [ ] **Step 5: Run the focused contract tests**

Run: `pytest -q tests/contracts/test_participants.py tests/contracts/test_message_traces.py tests/contracts/test_trace_web_api.py`

Expected: all selected tests pass.

- [ ] **Step 6: Commit**

```bash
git add groupmate/adapters/participants.py groupmate/adapters/message_media.py groupmate/social_runtime/control/message_traces.py groupmate/adapters/web_api.py tests/contracts
git commit -m "feat: resolve mentions and recover media previews"
```

### Task 4: Add Immediate Refresh and Render Rich Message Details

**Files:**
- Modify: `pages/settings/app.js`
- Modify: `pages/settings/workspaces/runtime.js`
- Modify: `pages/settings/components/message.js`
- Modify: `pages/settings/components/inspector.js`
- Modify: `tests/page/fixtures/fake_bridge.js`
- Test: `tests/page/test_product_ui.py`
- Test: `tests/page/test_iframe_workflows.py`

- [ ] **Step 1: Write failing frontend contract tests**

Assert the runtime heading exposes a refresh control, message rendering uses mention display names, media failure creates a retry control, and decision details include pre-gate and final delivery labels.

```python
assert 'data-action="refresh-runtime"' in runtime
assert "刷新运行中心" in runtime
assert "BLOCKED_BY_SHADOW" in inspector_fixture
```

- [ ] **Step 2: Run page tests and verify failure**

Run: `pytest -q tests/page/test_product_ui.py tests/page/test_iframe_workflows.py`

Expected: failure because the controls and richer fields are absent.

- [ ] **Step 3: Implement deduplicated immediate refresh**

Pass a `refresh` callback into `renderRuntime`. Keep one in-flight Promise in `app.js`, fetch the five runtime projections in parallel, update the existing store, and rerender without resetting filter/search state. Disable and animate the button until completion.

- [ ] **Step 4: Render mentions, thumbnails, errors, and decisions**

Render resolved @ chips with avatar hydration. Show compact image thumbnails in rows, full previews in the inspector, and a retry button that evicts the failed cache key before re-requesting `/media`. Present normal silence, degraded cognition, pre-gate ACT, and SHADOW delivery as separate rows.

- [ ] **Step 5: Run the focused page tests**

Run: `pytest -q tests/page/test_product_ui.py tests/page/test_iframe_workflows.py tests/page/test_frontend_security.py`

Expected: all selected tests pass.

- [ ] **Step 6: Commit**

```bash
git add pages/settings tests/page
git commit -m "feat: add runtime refresh and rich message details"
```

### Task 5: Apply the Neutral Runtime Visual System

**Files:**
- Modify: `pages/settings/styles/tokens.css`
- Modify: `pages/settings/styles/layout.css`
- Modify: `pages/settings/styles/components.css`
- Modify: `pages/settings/index.html`
- Test: `tests/page/test_runtime_layout.py`
- Test: `tests/page/test_accessibility_contract.py`

- [ ] **Step 1: Write failing style-contract assertions**

Assert the neutral canvas/surface tokens, 12–14px panel radii, refresh icon asset, visible focus treatment, responsive inspector, and reduced-motion handling exist.

- [ ] **Step 2: Run the style tests and verify failure**

Run: `pytest -q tests/page/test_runtime_layout.py tests/page/test_accessibility_contract.py`

Expected: failure on the new refresh and neutral hierarchy contracts.

- [ ] **Step 3: Update tokens and component hierarchy**

Move the light theme to neutral gray canvas plus white surfaces; keep green for status only. Flatten the selected navigation to light gray, use fine borders instead of large shadows, align toolbar/button density with the reference, and style media, mention, diagnostic, refresh, and retry states.

- [ ] **Step 4: Preserve dark and responsive modes**

Keep dark tokens semantically equivalent. At 70rem use the existing inspector drawer, and at 44rem ensure trace cards, media previews, toolbar, and refresh remain usable.

- [ ] **Step 5: Run the focused style tests**

Run: `pytest -q tests/page/test_runtime_layout.py tests/page/test_accessibility_contract.py tests/page/test_shell_assets.py`

Expected: all selected tests pass.

- [ ] **Step 6: Run one build/static verification**

Run: `python -m pytest -q tests/page tests/contracts/test_message_traces.py tests/contracts/test_trace_web_api.py`

Expected: selected page and trace contracts pass without running the full repository suite.

- [ ] **Step 7: Commit**

```bash
git add pages/settings tests/page
git commit -m "style: refine the runtime center visual system"
```

### Task 6: Browser Verification and Package Readiness

**Files:**
- Create: `design-qa.md`
- Modify if required: `pages/settings/**`

- [ ] **Step 1: Start the existing local fake AstrBot page**

Run the repository's fake page server on the existing approved local port and open `#/runtime`.

- [ ] **Step 2: Verify primary interactions**

Check refresh deduplication, filtering, opening/closing details, @ nickname/avatar display, image thumbnail/full preview, retry state, light/dark theme, and narrow viewport.

- [ ] **Step 3: Compare against the approved reference**

Record P0–P3 findings in `design-qa.md`; fix P0/P1/P2 only. The final line must be `final result: passed` before handoff.

- [ ] **Step 4: Run diff and status checks**

Run: `git diff --check && git status --short`

Expected: no whitespace errors; only intended files appear.

- [ ] **Step 5: Commit verification adjustments**

```bash
git add pages/settings tests design-qa.md
git commit -m "fix: complete runtime center verification"
```
