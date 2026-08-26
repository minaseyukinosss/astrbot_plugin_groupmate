# Runtime Trace, Status, Pagination, and Persona Config Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every message trace reach an honest terminal state, report the live runtime instead of stale configuration, expose the real trace total with server pagination, and reduce the visible persona configuration to the confirmed Aemeath identity.

**Architecture:** Keep message lifecycle closure inside `MessageTraceRepository`, and let the AstrBot bridge expose read-only live runtime/persona snapshots to the existing control API. Extend the trace projection with cursor pagination and merge those pages in the existing frontend store. Keep legacy persona settings readable, while changing only the defaults and visible schema for new installations.

**Tech Stack:** Python 3, SQLite, AstrBot plugin adapters, vanilla ES modules, pytest, Node-based frontend contract tests.

---

### Task 1: Close every message in an AMBIENT attention frame

**Files:**
- Modify: `tests/contracts/test_message_traces.py`
- Modify: `groupmate/social_runtime/control/message_traces.py`

- [ ] **Step 1: Write failing lifecycle tests**

Add a helper that creates an AMBIENT `AttentionFrame` with a source event and one earlier context event, then assert that `record_evaluation()` leaves the source with its normal evaluated result and closes the context message as `OBSERVED` with the copy `已纳入同一轮群聊理解` and `作为上下文参与判断`. Add a second test that first moves a context trace to a terminal state and asserts the batch evaluation does not overwrite it.

```python
def test_ambient_evaluation_closes_all_pending_focus_messages(tmp_path):
    repo = MessageTraceRepository(tmp_path / "runtime.db")
    context = _platform_event("context")
    source = _platform_event("source")
    repo.record_received(context, runtime_mode="SHADOW", now=10)
    repo.record_received(source, runtime_mode="SHADOW", now=11)
    evaluation = _evaluation(source, outcome="SILENCE")
    evaluation.frame = replace(
        evaluation.frame,
        focus_event_ids=(context.event_id, source.event_id),
    )
    repo.record_evaluation(evaluation, now=12)
    by_ref = {item["entity_ref"]: item["summary"] for item in repo.query(
        persona_id="groupmate:default", group_id="g-1"
    )["items"]}
    assert by_ref[context.event_id]["delivery"]["status"] == "OBSERVED"
    assert by_ref[context.event_id]["understanding"]["summary"] == "已纳入同一轮群聊理解"
```

- [ ] **Step 2: Run the new lifecycle tests and verify failure**

Run: `pytest -q tests/contracts/test_message_traces.py -k 'ambient_evaluation_closes or terminal_context'`

Expected: FAIL because non-source focus traces remain `RECEIVED`/pending.

- [ ] **Step 3: Implement pending-only context closure**

Add a terminal delivery-status set and a private repository helper that loads each non-source focus trace in the same persona/group scope, skips already terminal rows, and updates only pending rows:

```python
_TERMINAL_DELIVERY_STATUSES = {
    "SENT", "SILENT", "OBSERVED", "BLOCKED_BY_SHADOW",
    "HANDED_OFF", "FAILED", "UNKNOWN",
}

def _close_ambient_context_traces(self, evaluation: object, now: int) -> None:
    frame = getattr(evaluation, "frame", None)
    source = getattr(evaluation, "source_event", None)
    if frame is None or source is None or frame.trigger_kind != "AMBIENT":
        return
    for event_id in frame.focus_event_ids:
        if event_id == source.event_id:
            continue
        self._mutate_pending_context(
            event_id,
            persona_id=source.persona_id,
            group_id=source.group_id,
            now=now,
        )
```

The mutation sets understanding status `READY`, decision outcome `OBSERVE`/`would_reply=False`, delivery status `OBSERVED`, and stages `ATTENDED`, `UNDERSTOOD`, `DECIDED`. Call it from `record_evaluation()` after the source mutation succeeds.

- [ ] **Step 4: Run the lifecycle tests**

Run: `pytest -q tests/contracts/test_message_traces.py -k 'ambient_evaluation_closes or terminal_context or updates_one_message'`

Expected: PASS.

- [ ] **Step 5: Commit the lifecycle fix**

```bash
git add tests/contracts/test_message_traces.py groupmate/social_runtime/control/message_traces.py
git commit -m "fix: close batched ambient message traces"
```

### Task 2: Report live runtime and resolved persona state

**Files:**
- Modify: `tests/contracts/test_trace_web_api.py`
- Modify: `tests/contracts/test_message_trace_bridge.py`
- Modify: `groupmate/adapters/astrbot_bridge.py`
- Modify: `groupmate/adapters/web_api.py`
- Modify: `main.py`
- Modify: `pages/settings/workspaces/runtime.js`
- Modify: `pages/settings/app.js`
- Modify: `tests/page/test_product_ui.py`

- [ ] **Step 1: Write failing dynamic-status tests**

Create a mutable status provider in the control API contract test. Query bootstrap once with effective `SHADOW`, mutate the provider to `SOCIAL_RUNTIME`, query again, and assert the API reports both the configured mode and new effective mode without rebuilding the API. Add a bridge contract assertion for the resolved Aemeath name, aliases, and preset label.

```python
state = {"effective_runtime_mode": "SHADOW", "runtime_state": "RUNNING"}
api = ControlPlaneWebAPI(
    ...,
    runtime_status_provider=lambda _group_id: {**state, "runtime_ready": True, "runtime_blockers": []},
)
assert asyncio.run(api.handle(_get("/bootstrap"))).body["effective_runtime_mode"] == "SHADOW"
state["effective_runtime_mode"] = "SOCIAL_RUNTIME"
assert asyncio.run(api.handle(_get("/bootstrap"))).body["effective_runtime_mode"] == "SOCIAL_RUNTIME"
```

- [ ] **Step 2: Run the status tests and verify failure**

Run: `pytest -q tests/contracts/test_trace_web_api.py tests/contracts/test_message_trace_bridge.py -k 'live_runtime or resolved_persona'`

Expected: FAIL because `ControlPlaneWebAPI` only stores constructor-time mode and the bridge exposes no read-only snapshots.

- [ ] **Step 3: Add bridge status providers and wire them into bootstrap**

Add these read-only bridge interfaces:

```python
def runtime_status(self, group_id: str) -> dict[str, object]:
    if self._manager is None:
        return {
            "effective_runtime_mode": "OFF",
            "runtime_state": "STOPPED",
            "runtime_ready": False,
            "runtime_blockers": ["运行管理器尚未启动"],
        }
    return {
        "effective_runtime_mode": self._manager.group_mode(group_id).value,
        "runtime_state": "RUNNING",
        "runtime_ready": True,
        "runtime_blockers": [],
    }

def resolved_persona_status(self, group_id: str) -> dict[str, object]:
    profile = self._profile_snapshot(group_id)
    return {
        "name": profile["identity"]["name"],
        "aliases": list(profile["identity"].get("aliases", [])),
        "preset": self.settings.persona_preset,
        "preset_label": "爱弥斯（当前剧情）",
    }
```

Extend `ControlPlaneWebAPI.__init__()` with optional `runtime_status_provider` and `persona_status_provider`, merge their current values only when serving `/bootstrap`, retain constructor fields as compatibility fallback, and pass both bridge methods from `main.py`.

- [ ] **Step 4: Update dashboard copy to prefer effective state**

Export a pure helper from `runtime.js`:

```javascript
export function runtimeStatus(bootstrap, runtime) {
  const configured = bootstrap?.configured_runtime_mode || historicMode(runtime);
  const effective = bootstrap?.effective_runtime_mode || configured;
  return {
    configured,
    effective,
    running: bootstrap?.runtime_state === "RUNNING" && effective !== "OFF",
    mismatch: configured !== effective,
  };
}
```

Use `effective` for the banner title and controls. When `mismatch` is true, show `配置已变更，实际运行仍为 …；重载插件后生效`. Display `当前人格：爱弥斯（当前剧情）` from `bootstrap.resolved_persona`. Update the compact header in `app.js` to use the same effective mode and resolved name.

- [ ] **Step 5: Run the status and UI tests**

Run: `pytest -q tests/contracts/test_trace_web_api.py tests/contracts/test_message_trace_bridge.py tests/page/test_product_ui.py -k 'runtime or persona'`

Expected: PASS.

- [ ] **Step 6: Commit live-state reporting**

```bash
git add tests/contracts/test_trace_web_api.py tests/contracts/test_message_trace_bridge.py tests/page/test_product_ui.py groupmate/adapters/astrbot_bridge.py groupmate/adapters/web_api.py main.py pages/settings/workspaces/runtime.js pages/settings/app.js
git commit -m "fix: show effective runtime and persona state"
```

### Task 3: Replace the 200-row view ceiling with cursor pagination and a real total

**Files:**
- Modify: `tests/contracts/test_message_traces.py`
- Modify: `tests/contracts/test_trace_web_api.py`
- Modify: `groupmate/social_runtime/control/message_traces.py`
- Modify: `groupmate/social_runtime/control/queries.py`
- Modify: `groupmate/adapters/web_api.py`
- Modify: `pages/settings/components/store.js`
- Modify: `pages/settings/app.js`
- Modify: `pages/settings/workspaces/runtime.js`
- Modify: `tests/page/test_shadow_console.py`

- [ ] **Step 1: Write failing repository/API pagination tests**

Insert 205 traces. Query with `limit=100`, assert `total_count == 205`, `len(items) == 100`, `has_more is True`, and `next_cursor` is opaque. Query the second page with that cursor and assert no duplicate refs. Add API tests for valid `limit`/`before` and rejection or clamping of out-of-range limits.

```python
first = repo.query(persona_id=PERSONA, group_id=GROUP, limit=100)
second = repo.query(
    persona_id=PERSONA,
    group_id=GROUP,
    limit=100,
    before=first["next_cursor"],
)
assert first["total_count"] == 205
assert first["has_more"] is True
assert {item["entity_ref"] for item in first["items"]}.isdisjoint(
    item["entity_ref"] for item in second["items"]
)
```

- [ ] **Step 2: Run pagination tests and verify failure**

Run: `pytest -q tests/contracts/test_message_traces.py tests/contracts/test_trace_web_api.py -k 'pagination or total_count'`

Expected: FAIL because `query()` accepts no pagination arguments and always executes `LIMIT 200`.

- [ ] **Step 3: Implement scoped cursor pagination**

Change `MessageTraceRepository.query()` and `ProjectionQueries.traces()` signatures to accept `limit: int = 100` and `before: str | None = None`. Encode/decode a URL-safe base64 JSON cursor containing only `received_at` and `entity_ref`. Use a scoped count query and this stable page predicate:

```sql
AND (received_at < ? OR (received_at = ? AND entity_ref < ?))
ORDER BY received_at DESC, entity_ref DESC
LIMIT ?
```

Fetch `limit + 1`, return only `limit`, and publish `total_count`, `has_more`, and `next_cursor`. Special-case `/traces` in `ControlPlaneWebAPI.handle()` to pass `limit` and `before`, defaulting to 100 and capping at 200.

- [ ] **Step 4: Add frontend page merging and honest counts**

Add a trace-page merge method to `pages/settings/components/store.js` that deduplicates by `entity_ref`; head refresh prepends newer items while keeping loaded older pages, and append uses the returned `next_cursor`. Add `loadMoreTraces(before)` in `app.js` and pass it to `renderRuntime()`.

Update `messageBrowser()` so its heading reads `已加载 Y / 共 X`, its search placeholder reads `搜索已加载的成员、消息或处理结果`, and the button calls the server after all locally loaded matches are visible. Change the overview fact from `已收到 items.length` to `累计收到 total_count`.

- [ ] **Step 5: Run pagination and console tests**

Run: `pytest -q tests/contracts/test_message_traces.py tests/contracts/test_trace_web_api.py tests/page/test_shadow_console.py -k 'pagination or total_count or load_more or runtime_console'`

Expected: PASS.

- [ ] **Step 6: Commit pagination**

```bash
git add tests/contracts/test_message_traces.py tests/contracts/test_trace_web_api.py tests/page/test_shadow_console.py groupmate/social_runtime/control/message_traces.py groupmate/social_runtime/control/queries.py groupmate/adapters/web_api.py pages/settings/components/store.js pages/settings/app.js pages/settings/workspaces/runtime.js
git commit -m "fix: paginate runtime message traces"
```

### Task 4: Collapse visible persona configuration to Aemeath

**Files:**
- Modify: `tests/shared/test_plugin_skeleton.py`
- Modify: `groupmate/settings.py`
- Modify: `_conf_schema.json`

- [ ] **Step 1: Write failing settings/schema tests**

Change default assertions to `persona_name == "爱弥斯"` and `persona_aliases == ("小爱",)`. Assert visible schema fields are exactly the deployment settings plus `persona_name` and `persona_aliases`, that their descriptions are `正式名称` and `其他称呼`, and `persona_preset` is invisible. Keep and extend the existing legacy test proving explicit `persona_preset="custom"`, custom names, and explicit aliases are still parsed.

- [ ] **Step 2: Run settings tests and verify failure**

Run: `pytest -q tests/shared/test_plugin_skeleton.py -k 'settings_defaults or config_schema or persona'`

Expected: FAIL on the old `Groupmate`/empty-alias defaults and visible preset field.

- [ ] **Step 3: Update defaults and visible schema**

Set dataclass and missing-key parser defaults to `爱弥斯` and `("小爱",)`, while preserving an explicitly supplied empty alias list. Update `_conf_schema.json`:

```json
"persona_name": {
  "description": "正式名称",
  "default": "爱弥斯",
  "hint": "爱弥斯在群聊中的正式名称，也用于直接呼唤识别。"
},
"persona_aliases": {
  "description": "其他称呼",
  "default": ["小爱"],
  "hint": "群友可以用这些称呼呼唤爱弥斯，每行一个。"
},
"persona_preset": {
  "default": "aemeath_current",
  "invisible": true
}
```

Retain `custom` as a parser-compatible legacy option; do not expose it in new visible configuration.

- [ ] **Step 4: Run settings tests**

Run: `pytest -q tests/shared/test_plugin_skeleton.py -k 'settings_defaults or config_schema or persona'`

Expected: PASS.

- [ ] **Step 5: Commit persona config cleanup**

```bash
git add tests/shared/test_plugin_skeleton.py groupmate/settings.py _conf_schema.json
git commit -m "fix: simplify aemeath persona configuration"
```

### Task 5: Targeted integration verification

**Files:**
- Modify only if a targeted failure proves an integration defect.

- [ ] **Step 1: Run the focused verification set**

Run:

```bash
pytest -q \
  tests/contracts/test_message_traces.py \
  tests/contracts/test_trace_web_api.py \
  tests/contracts/test_message_trace_bridge.py \
  tests/shared/test_plugin_skeleton.py \
  tests/page/test_product_ui.py \
  tests/page/test_shadow_console.py
```

Expected: all focused tests PASS.

- [ ] **Step 2: Check packaging and diff hygiene**

Run: `python -m compileall -q groupmate main.py`

Expected: exit 0.

Run: `git diff --check`

Expected: no output.

- [ ] **Step 3: Review the final diff without touching user-owned files**

Run: `git status --short && git diff --stat HEAD~4..HEAD`

Expected: only planned source/test changes and the pre-existing user-owned `analysis/` remains untouched.

