# Groupmate Message Trace Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the projection-centric plugin page with a clear, message-centric NapCat → AstrBot → Groupmate trace console that shows participant identity, routing, decisions, and delivery outcomes.

**Architecture:** Add a durable `MessageTraceRepository` read model keyed by the translated QQ platform event ID. A high-priority AstrBot observer records receipt facts, the existing low-priority handler marks Groupmate entry, and bridge evaluation/delivery hooks update the same trace. The Plugin Page reads a dedicated `traces` projection and resolves avatars through a separate `ParticipantDirectory`, keeping QQ presentation enrichment out of Social Runtime cognition.

**Tech Stack:** Python 3.10+, SQLite, AstrBot Plugin Pages bridge, vanilla ES modules, CSS custom properties, pytest, Node-based frontend contract tests.

**Spec:** `docs/superpowers/specs/2026-08-21-groupmate-message-trace-console-design.md`

## Global Constraints

- Keep external AstrBot commands and dedicated plugins ahead of Groupmate's `priority=-100` social handler.
- The early observer records facts only: no model call, event mutation, message send, or `stop_event()`.
- Show no system prompt, chain of thought, raw Journal JSON, authentication data, or unrestricted QQ identifier.
- Do not add a general AstrBot APM subsystem or move video/meme plugin capabilities into Groupmate.
- Keep participant avatar enrichment outside Social Runtime core and fail to a stable generated avatar.
- `OFF`, `SHADOW`, `SOCIAL_RUNTIME`, and paused states must be mutually understandable and never contradictory.
- Keep tests targeted: one focused test cycle per task and one compact final verification set.
- Fix the installed iframe at approximately 1272 CSS pixels, plus one narrow layout and both light/dark themes.

---

### Task 1: Remove Unused Frontend Workspaces

**Files:**
- Delete: `pages/settings/workspaces/activity.js`
- Delete: `pages/settings/workspaces/governance.js`
- Delete: `pages/settings/workspaces/people.js`
- Delete: `pages/settings/workspaces/persona.js`
- Modify: `pages/settings/app.js`
- Modify: `pages/settings/router.js`
- Modify: `pages/settings/index.html`
- Modify: `pages/settings/i18n.js`
- Modify: `tests/page/test_workspaces.py`
- Modify: `tests/page/test_router_contract.py`
- Modify: `tests/page/fixtures/preview.html`
- Modify: `tests/page/test_iframe_workflows.py`

**Interfaces:**
- Consumes: existing `renderRuntime(select, command)`.
- Produces: a single `/runtime` Page route; SHADOW details remain inside the runtime inspector instead of a separate governance workspace.

- [ ] **Step 1: Rewrite the workspace contract test to require only runtime**

```python
def test_runtime_is_the_only_product_workspace():
    app = _source(PAGE / "app.js")
    router = _source(PAGE / "router.js")
    assert './workspaces/runtime.js' in app
    assert 'path: "/runtime"' in router
    for name in ("persona", "people", "activity", "governance"):
        assert not (WORKSPACES / f"{name}.js").exists()
        assert f'path: "/{name}"' not in router
```

- [ ] **Step 2: Delete the four unused workspace modules and remove their routes/imports/nav links**

Keep `WORKSPACE_PROJECTIONS` as:

```js
const WORKSPACE_PROJECTIONS = Object.freeze({
  "/runtime": ["runtime", "traces", "health", "persona", "governance"],
});
```

`normalizeHash()` must continue to redirect unknown and old hashes to `/runtime`.

- [ ] **Step 3: Run the focused page wiring tests**

Run: `pytest -q tests/page/test_workspaces.py tests/page/test_router_contract.py`

Expected: PASS with only the runtime route asserted.

- [ ] **Step 4: Commit the product-surface reduction**

```bash
git add pages/settings tests/page
git commit -m "refactor: focus plugin page on runtime"
```

### Task 2: Add the Durable Message Trace Read Model

**Files:**
- Create: `groupmate/social_runtime/control/message_traces.py`
- Create: `tests/contracts/test_message_traces.py`
- Modify: `groupmate/social_runtime/control/queries.py`

**Interfaces:**
- Consumes: `SocialEventEnvelope`, `ShadowEvaluation`, existing SQLite helpers.
- Produces: `MessageTraceRepository(path)`, `record_received(event, runtime_mode, now)`, `mark_entered(event_id, now)`, `record_evaluation(evaluation, now)`, `record_plan(event_id, plan, now)`, `record_delivery(correlation_id, status, platform_message_id, error_code, now)`, `query(persona_id, group_id)`, and `detail(persona_id, group_id, trace_ref)`.

- [ ] **Step 1: Write repository tests for one-row-per-message behavior**

Define `_platform_event(message_id, card, nickname)` with `SocialEventEnvelope.create()` using persona `groupmate:default`, group `g-1`, actor `42`, and sender display name `card or nickname`. Define `_evaluation(event, outcome)` as a `SimpleNamespace` containing `source_event`, `runtime_mode=RuntimeMode.SHADOW`, an accepted frame with `frame_id='frame-1'` and `trigger_kind='AMBIENT'`, and a `GovernorResult` with the requested outcome.

```python
def test_trace_updates_one_message_instead_of_appending_projection_rows(tmp_path):
    repo = MessageTraceRepository(tmp_path / "runtime.db")
    event = _platform_event("m-1", card="夏夏", nickname="小夏")
    repo.record_received(event, runtime_mode="SHADOW", now=10)
    repo.mark_entered(event.event_id, now=11)
    repo.record_evaluation(_evaluation(event, outcome="SILENCE"), now=12)

    view = repo.query(persona_id="groupmate:default", group_id="g-1")
    assert len(view["items"]) == 1
    summary = view["items"][0]["summary"]
    assert summary["actor"]["display_name"] == "夏夏"
    assert summary["route"]["owner"] == "GROUPMATE"
    assert summary["decision"]["outcome"] == "SILENCE"
    assert summary["delivery"]["status"] == "SILENT"
```

Also cover configured external ownership and stage ordering:

```python
assert [stage["kind"] for stage in detail["summary"]["stages"]] == [
    "RECEIVED", "ROUTED", "ATTENDED", "UNDERSTOOD", "DECIDED"
]
```

- [ ] **Step 2: Run the trace tests and verify failure**

Run: `pytest -q tests/contracts/test_message_traces.py`

Expected: FAIL because `MessageTraceRepository` does not exist.

- [ ] **Step 3: Implement the repository and privacy-trimmed summaries**

Create private tables `message_traces` and `message_trace_stages`. Use a SHA-256-derived `trace:<20 hex>` entity reference and never return raw `actor_id`. Store the original actor ID only in the private participant table added in Task 4.

Publish each revision into `control_projection_items` and `control_projection_events` with `projection_name='traces'` so the existing SSE stream can update the browser. The public summary must have exactly these top-level fields:

```python
{
    "actor": {"member_ref": ..., "display_name": ..., "avatar_ref": ...},
    "message": {"summary": ..., "media_types": [...]},
    "route": {"owner": ..., "label": ..., "reason": ...},
    "understanding": {"status": ..., "summary": ...},
    "decision": {"outcome": ..., "label": ..., "reasons": [...]},
    "delivery": {"mode": ..., "status": ..., "label": ...},
    "timing": {"total_ms": ...},
    "stages": [...],
}
```

Map internal values in Python, not in the browser: `AMBIENT → 观察群聊上下文`, `forced_observe → SHADOW 模式禁止发送`, external ownership → `交给外部能力`.

- [ ] **Step 4: Expose `ProjectionQueries.traces()` through the repository**

```python
def traces(self, *, persona_id: str, group_id: str) -> dict[str, object]:
    return MessageTraceRepository(self.path).query(
        persona_id=persona_id,
        group_id=group_id,
    )
```

- [ ] **Step 5: Run the focused repository tests**

Run: `pytest -q tests/contracts/test_message_traces.py`

Expected: PASS.

- [ ] **Step 6: Commit the trace read model**

```bash
git add groupmate/social_runtime/control/message_traces.py groupmate/social_runtime/control/queries.py tests/contracts/test_message_traces.py
git commit -m "feat: add message trace read model"
```

### Task 3: Instrument AstrBot Entry, Evaluation, and Delivery

**Files:**
- Modify: `main.py`
- Modify: `groupmate/adapters/astrbot_bridge.py`
- Modify: `groupmate/social_runtime/control/message_traces.py`
- Create: `tests/contracts/test_message_trace_bridge.py`
- Modify: `tests/contracts/test_astrbot_events.py`

**Interfaces:**
- Consumes: Task 2 repository methods and existing `AstrBotEventTranslator`.
- Produces: `AstrBotSocialRuntimeBridge.observe_event(event)` for fact-only early recording and existing `handle_event(event)` for the low-priority Social Runtime path.

- [ ] **Step 1: Write a bridge contract test for dual observation**

The test file defines `_FakeAstrEvent` with `message_obj.raw_message`, `message_str`, `get_group_id()`, `get_sender_id()`, and `get_sender_name()`. `_bridge_for(tmp_path)` builds the existing fake AstrBot context, SHADOW settings for `g-1`, and a started bridge using the temporary data directory.

```python
def test_early_observer_records_without_ingesting_or_stopping(tmp_path):
    bridge = _bridge_for(tmp_path)
    event = _FakeAstrEvent(message_id="9", text="bq 熊猫头")
    asyncio.run(bridge.observe_event(event))
    assert bridge.trace_repository.query(
        persona_id="groupmate:default", group_id="g-1"
    )["items"][0]["summary"]["route"]["owner"] == "EXTERNAL_PLUGIN"
    assert bridge.manager.event_store.inbox_count() == 0
```

Add a second assertion that `handle_event()` marks the same trace entered and does not create a second row.

- [ ] **Step 2: Run the bridge contract and verify failure**

Run: `pytest -q tests/contracts/test_message_trace_bridge.py`

Expected: FAIL because `observe_event()` is missing.

- [ ] **Step 3: Add the high-priority fact observer in `main.py`**

```python
@filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=1000)
async def observe_group_message_arrival(self, event: AstrMessageEvent):
    await self.bridge.observe_event(event)
```

Keep the existing `priority=-100` handler unchanged as the only call to `handle_event()`.

- [ ] **Step 4: Record evaluation, plan, and delivery state in the bridge**

In `_handle_evaluations`, call `record_evaluation()` before planning and `record_plan()` when a plan exists. In `_dispatch_ready`, record `SENT`, `FAILED`, or `UNKNOWN` using the persisted receipt and platform message ID. Trace recording errors must update a diagnostic string and must not block replies.

- [ ] **Step 5: Run the bridge and delivery tests**

Run: `pytest -q tests/contracts/test_message_trace_bridge.py tests/social_runtime/delivery/test_dispatcher.py`

Expected: PASS.

- [ ] **Step 6: Commit the traced runtime path**

```bash
git add main.py groupmate/adapters/astrbot_bridge.py groupmate/social_runtime/control/message_traces.py tests/contracts/test_message_trace_bridge.py tests/contracts/test_astrbot_events.py
git commit -m "feat: trace astrbot message lifecycle"
```

### Task 4: Add Participant Nickname and Avatar Enrichment

**Files:**
- Create: `groupmate/adapters/participants.py`
- Create: `tests/contracts/test_participants.py`
- Modify: `groupmate/social_runtime/control/message_traces.py`

**Interfaces:**
- Consumes: `event.payload['sender']`, private actor ID, group ID, plugin data directory.
- Produces: `ParticipantDirectory(path, cache_dir, fetcher=None)`, `remember(event) -> dict`, and async `avatar_data(avatar_ref) -> dict[str, str]` returning `{"data_uri": ..., "source": "qq"|"fallback"}`.

- [ ] **Step 1: Write participant priority and safe-avatar tests**

```python
def test_participant_prefers_card_and_hides_raw_qq_id(tmp_path):
    directory = ParticipantDirectory(tmp_path / "runtime.db", tmp_path / "avatars")
    participant = directory.remember(platform_event("m-1", card="夏夏", nickname="小夏"))
    assert participant["display_name"] == "夏夏"
    assert participant["avatar_ref"].startswith("participant:")
    assert "42" not in participant["avatar_ref"]
```

```python
def test_avatar_failure_returns_stable_generated_svg(tmp_path):
    async def failed_fetcher(_url):
        raise OSError("offline")
    directory = ParticipantDirectory(
        tmp_path / "runtime.db",
        tmp_path / "avatars",
        fetcher=failed_fetcher,
    )
    ref = directory.remember(
        _platform_event("m-1", card="夏夏", nickname="小夏")
    )["avatar_ref"]
    first = asyncio.run(directory.avatar_data(ref))
    second = asyncio.run(directory.avatar_data(ref))
    assert first == second
    assert first["source"] == "fallback"
    assert first["data_uri"].startswith("data:image/svg+xml;base64,")
```

- [ ] **Step 2: Run the participant tests and verify failure**

Run: `pytest -q tests/contracts/test_participants.py`

Expected: FAIL because `ParticipantDirectory` does not exist.

- [ ] **Step 3: Implement participant persistence and bounded avatar cache**

Use `urllib.request` only inside `asyncio.to_thread`, enforce `image/*`, a 512 KiB limit, 24-hour success cache, and short failure cache. Build the QQ avatar URL only on the server. Generate escaped SVG initials from the display name when remote fetch fails.

- [ ] **Step 4: Connect trace receipt to participant snapshots**

`record_received()` must call `ParticipantDirectory.remember()` and copy only `member_ref`, `display_name`, and `avatar_ref` into the public trace summary.

- [ ] **Step 5: Run participant and trace tests**

Run: `pytest -q tests/contracts/test_participants.py tests/contracts/test_message_traces.py`

Expected: PASS.

- [ ] **Step 6: Commit participant enrichment**

```bash
git add groupmate/adapters/participants.py groupmate/social_runtime/control/message_traces.py tests/contracts/test_participants.py tests/contracts/test_message_traces.py
git commit -m "feat: enrich traces with participants"
```

### Task 5: Expose Trace and Avatar APIs with Correct Runtime Readiness

**Files:**
- Modify: `groupmate/adapters/web_api.py`
- Modify: `main.py`
- Modify: `groupmate/social_runtime/control/queries.py`
- Create: `tests/contracts/test_trace_web_api.py`

**Interfaces:**
- Consumes: `ProjectionQueries.traces()` and `ParticipantDirectory.avatar_data()`.
- Produces: `GET traces`, `GET avatar?avatar_ref=...`, and bootstrap fields `configured_runtime_mode`, `runtime_ready`, `runtime_blockers`.

- [ ] **Step 1: Write API tests for scoped traces and avatar authorization**

Use the same `_request()` fixture pattern as `tests/contracts/test_web_api.py`. `_get(path, **query)` returns a `WebRequest(method='GET', path=path, query={'persona_id': 'groupmate:default', 'group_id': query.pop('group_id', 'g-1'), **query}, headers={}, json_body=None, username='admin:root')`. The `api` fixture seeds one trace and supplies the temporary `ParticipantDirectory.avatar_data` loader.

```python
def test_trace_endpoint_is_group_scoped(api):
    response = asyncio.run(api.handle(_get("/traces", group_id="g-1")))
    assert response.status == 200
    assert response.body["projection"] == "traces"
    assert all(item["summary"]["actor"] for item in response.body["items"])

def test_unknown_avatar_ref_is_not_a_freeform_qq_proxy(api):
    response = asyncio.run(api.handle(_get("/avatar", avatar_ref="participant:unknown")))
    assert response.status == 404
```

- [ ] **Step 2: Run API tests and verify failure**

Run: `pytest -q tests/contracts/test_trace_web_api.py`

Expected: FAIL because the endpoints are not registered.

- [ ] **Step 3: Add `traces` and `avatar` to the route registrar**

Handle `traces` through the normal scoped query path. Handle `avatar` as an awaited JSON endpoint returning only a data URI for an already registered opaque reference. Forward `avatar_ref` in `AstrBotControlPlaneRoutes._handler()` query mapping.

- [ ] **Step 4: Report configured runtime state from bootstrap**

Pass the immutable `SocialRuntimeSettings` state into `ControlPlaneWebAPI`. The bootstrap response must distinguish configured mode from projected history:

```python
{
    "configured_runtime_mode": self.runtime_mode,
    "runtime_ready": self.runtime_mode != "OFF",
    "runtime_blockers": [] if ready else ["未启用群或未选择文本模型"],
}
```

- [ ] **Step 5: Run API tests**

Run: `pytest -q tests/contracts/test_trace_web_api.py tests/contracts/test_web_api.py`

Expected: PASS.

- [ ] **Step 6: Commit the control API**

```bash
git add groupmate/adapters/web_api.py groupmate/social_runtime/control/queries.py main.py tests/contracts/test_trace_web_api.py
git commit -m "feat: expose message traces to plugin page"
```

### Task 6: Rebuild the Runtime Workspace Around Message Traces

**Files:**
- Rewrite: `pages/settings/workspaces/runtime.js`
- Rewrite: `pages/settings/components/inspector.js`
- Modify: `pages/settings/app.js`
- Modify: `pages/settings/bridge.js`
- Modify: `pages/settings/components/presenters.js`
- Rewrite: `tests/page/test_shadow_console.py`
- Modify: `tests/page/fixtures/fake_bridge.js`

**Interfaces:**
- Consumes: `select("traces")`, configured bootstrap state, and `bridge.query("avatar", {avatar_ref})`.
- Produces: one trace row per message, status mode banner, trace filters, progressive avatar loading, and a stage-based inspector.

- [ ] **Step 1: Write the frontend contract test for plain-language trace UI**

```python
def test_runtime_console_is_message_centric_and_hides_internal_terms():
    runtime = (PAGE / "workspaces/runtime.js").read_text(encoding="utf-8")
    for label in ("收到的消息", "处理路径", "Groupmate 的理解", "决定", "最终结果"):
        assert label in runtime
    for internal in ("forced_observe", "AMBIENT", "projection_version"):
        assert internal not in runtime
```

Fixture assertions must cover OFF, SHADOW silence, SHADOW candidate, external handoff, sent, and unknown delivery.

- [ ] **Step 2: Run the frontend contract and verify failure**

Run: `pytest -q tests/page/test_shadow_console.py`

Expected: FAIL against the old projection table.

- [ ] **Step 3: Implement the mode banner without contradictory state**

Use configured bootstrap state as authoritative. OFF renders “Groupmate 当前未运行” and no pause action. SHADOW renders “正在观察，不会向群里发消息”. SOCIAL_RUNTIME renders “正在运行，符合条件的回复会发送到群里”. Paused overrides either enabled mode.

- [ ] **Step 4: Implement the trace table and filters**

Columns: time, participant/message, route, understanding, decision, result. Filters: all, responded, silent, external, failed. Long summaries truncate to two lines; opening a row reveals the full safe summary.

- [ ] **Step 5: Implement stage inspector and progressive avatars**

Use a client-side `Map` keyed by `avatar_ref`. Render a stable initials avatar immediately, then replace it only after `query("avatar")` returns a valid `data:image/` URI. The inspector must show sender, message, fixed stage timeline, candidate/actual response, reasons, and a collapsed technical section.

- [ ] **Step 6: Run frontend unit/contract tests**

Run: `pytest -q tests/page/test_shadow_console.py tests/page/test_product_ui.py tests/page/test_frontend_security.py`

Expected: PASS.

- [ ] **Step 7: Commit the message-centric workspace**

```bash
git add pages/settings tests/page
git commit -m "feat: show message-centric runtime traces"
```

### Task 7: Fix Installed-Page Assets and Responsive Styles

**Files:**
- Modify: `pages/settings/index.html`
- Modify: `pages/settings/styles/layout.css`
- Modify: `pages/settings/styles/components.css`
- Modify: `pages/settings/styles/tokens.css`
- Modify: `pages/settings/workspaces/runtime.js`
- Modify: `pages/settings/components/inspector.js`
- Create: `tests/page/test_runtime_layout.py`

**Interfaces:**
- Consumes: Task 6 semantic markup.
- Produces: no runtime-generated relative image paths, readable 1272px desktop layout, overlay inspector below wide desktop, card traces on narrow screens, and theme-safe contrast.

- [ ] **Step 1: Write static asset and breakpoint regression tests**

```python
def test_runtime_does_not_construct_plugin_asset_urls_in_javascript():
    javascript = "\n".join(path.read_text() for path in PAGE.rglob("*.js"))
    assert "./assets/icons/${" not in javascript

def test_sidebar_does_not_collapse_at_normal_desktop_width():
    css = (PAGE / "styles/layout.css").read_text()
    assert "@media (max-width: 80rem)" not in css
    assert "@media (max-width: 60rem)" in css
```

- [ ] **Step 2: Run style regression tests and verify failure**

Run: `pytest -q tests/page/test_runtime_layout.py`

Expected: FAIL against current dynamic icon URLs and 80rem sidebar collapse.

- [ ] **Step 3: Replace dynamic asset URLs with inline SVG symbols**

Declare required symbols once in `index.html` inside a hidden SVG sprite. `icon(name)` must clone `<svg><use href="#icon-name"></use></svg>` and never construct `img.src` in JavaScript. Keep only statically declared brand assets as `<img>`.

- [ ] **Step 4: Correct desktop and inspector layout**

Keep sidebar labels through normal desktop widths. At constrained desktop widths, open the inspector as a right overlay instead of shrinking the main table. At narrow widths, switch trace rows to labeled cards and make the sidebar a compact top navigation.

- [ ] **Step 5: Correct truncation, controls, and theme contrast**

Add two-line clamping for safe message summaries, consistent 2.5rem controls, visible focus rings, stable loading/empty blocks, and readable muted text in both themes. Preserve existing OKLCH token conventions.

- [ ] **Step 6: Run layout and frontend contract tests**

Run: `pytest -q tests/page/test_runtime_layout.py tests/page/test_shadow_console.py tests/page/test_frontend_security.py`

Expected: PASS.

- [ ] **Step 7: Commit the installed-page style repairs**

```bash
git add pages/settings tests/page/test_runtime_layout.py
git commit -m "fix: repair plugin page layout and assets"
```

### Task 8: Compact Verification and Install Package

**Files:**
- Modify: `metadata.yaml`
- Modify: `README.md` only if installation instructions or version references require the new package number.
- Create: `dist/astrbot_plugin_groupmate-<version>.zip`

**Interfaces:**
- Consumes: all previous tasks.
- Produces: a clean `main` commit history and a single AstrBot upload ZIP with one top-level `astrbot_plugin_groupmate/` directory.

- [ ] **Step 1: Run the compact backend verification set**

Run:

```bash
pytest -q \
  tests/contracts/test_message_traces.py \
  tests/contracts/test_message_trace_bridge.py \
  tests/contracts/test_participants.py \
  tests/contracts/test_trace_web_api.py
```

Expected: PASS.

- [ ] **Step 2: Run the compact frontend verification set**

Run:

```bash
pytest -q \
  tests/page/test_workspaces.py \
  tests/page/test_shadow_console.py \
  tests/page/test_runtime_layout.py \
  tests/page/test_frontend_security.py
```

Expected: PASS.

- [ ] **Step 3: Perform one browser smoke check only**

Use the existing fake AstrBot server at approximately 1272 CSS pixels. Check light theme, dark theme, one trace inspector, OFF, and SHADOW. Do not run broad visual permutations.

- [ ] **Step 4: Bump the release candidate version and build the ZIP**

Increment the current `1.0.0-rc.8` to the next release candidate. Exclude `.git`, tests, caches, docs, and previous `dist` output. Validate with `unzip -t` and print SHA-256.

- [ ] **Step 5: Commit release metadata**

```bash
git add metadata.yaml README.md
git commit -m "chore: prepare groupmate message trace package"
```

- [ ] **Step 6: Report artifact path, checksum, and install/reload steps**

The handoff must state that an existing installed plugin needs upload/replacement followed by AstrBot “重载插件”, and that old trace history is not fabricated retroactively.
