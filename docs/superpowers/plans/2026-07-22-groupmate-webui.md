# Groupmate WebUI Control Center Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (recommended) to implement this plan task-by-task with checkpoints.

**Goal:** Add an AstrBot Plugin Page control center with overview-first navigation, shadow decision review, runtime pause/resume, and privacy-safe dataset export.

**Architecture:** Keep SQLite querying in `SQLiteMemoryStore`, expose a small pure/async Web API adapter in `groupmate/web_api.py`, and register it from `main.py`. Serve a dependency-free static page from `pages/control-center/` that talks only through `window.AstrBotPluginPage`.

**Tech Stack:** Python 3.7-compatible code, sqlite3, AstrBot `context.register_web_api()`, Quart-compatible fallback for AstrBot 4.24, native HTML/CSS/JavaScript, pytest.

---

### Task 1: Add paginated shadow decision storage queries

**Files:**
- Modify: `groupmate/memory.py:456-532`
- Test: `tests/test_shadow_storage.py`

- [ ] **Step 1: Write failing storage tests**

Add tests covering the public storage contract:

```python
def test_shadow_decision_page_filters_and_returns_opaque_cursor(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    for row in (
        record(decision_id="respond", action="respond", created_at=30),
        record(decision_id="ignored", action="ignore", created_at=20),
        record(decision_id="labeled", action="ignore", created_at=10),
    ):
        store.save_shadow_decision(row)
    store.label_shadow_decision("labeled", "must_silence", 40)

    page = store.shadow_decision_page(label="unlabeled", action="all", limit=1)

    assert [row["decision_id"] for row in page["items"]] == ["respond"]
    assert page["has_more"] is True
    assert page["next_cursor"]
    assert page["items"][0]["context_json"] is None
    store.close()


def test_shadow_decision_page_cursor_continues_newest_first(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    for index in range(3):
        store.save_shadow_decision(record(decision_id="d{}".format(index), created_at=index))

    first = store.shadow_decision_page(label="all", action="all", limit=2)
    second = store.shadow_decision_page(
        label="all", action="all", limit=2, cursor=first["next_cursor"]
    )

    assert [row["decision_id"] for row in first["items"]] == ["d2", "d1"]
    assert [row["decision_id"] for row in second["items"]] == ["d0"]
    assert second["has_more"] is False
    store.close()
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `pytest -q tests/test_shadow_storage.py -k 'decision_page'`

Expected: FAIL with `AttributeError: 'SQLiteMemoryStore' object has no attribute 'shadow_decision_page'`.

- [ ] **Step 3: Implement bounded storage paging**

Add `shadow_decision_page(self, label, action, limit=20, cursor=None)` to `SQLiteMemoryStore`.

Implementation requirements:

- Clamp `limit` to `1..50`.
- Accept only `label` values `all`, `unlabeled`, `must_respond`, `may_respond`, `must_silence`, `skipped`; accept only `action` values `all`, `respond`, `ignore`, `bypass`.
- Decode an opaque cursor with URL-safe base64 JSON containing `created_at` and internal `row_id`; invalid cursors raise `ValueError`.
- Build SQL only from fixed clauses, never interpolate client values as identifiers.
- Query `id`, all page fields needed by the serializer, and `context_json`, sorted by `created_at DESC, id DESC`, with `limit + 1` rows.
- Return `{"items": rows, "next_cursor": cursor_or_none, "has_more": bool}`. Keep `id` internal to the store result; the Web API must remove it before serialization.

Use a private `_encode_shadow_cursor`/`_decode_shadow_cursor` pair in `memory.py` so tests can exercise deterministic cursor behavior without a web framework.

- [ ] **Step 4: Run storage tests and the existing shadow suite**

Run: `pytest -q tests/test_shadow_storage.py`

Expected: PASS.

- [ ] **Step 5: Commit the storage boundary**

```bash
git add groupmate/memory.py tests/test_shadow_storage.py
git commit -m "feat: add paginated shadow decision queries"
```

### Task 2: Build the privacy-safe Web API adapter

**Files:**
- Create: `groupmate/web_api.py`
- Modify: `groupmate/astrbot_adapter.py:390-407`
- Test: `tests/test_web_api.py`

- [ ] **Step 1: Write failing serializer and validation tests**

Create tests using a real `SQLiteMemoryStore` and a minimal bridge stub. Cover:

```python
def test_overview_payload_excludes_identity_fields(tmp_path):
    bridge = build_bridge_with_shadow_rows(tmp_path)
    payload = build_overview_payload(bridge)

    text = json.dumps(payload, ensure_ascii=False)
    assert "group_hash" not in text
    assert "sender_hash" not in text
    assert "context_json" not in text
    assert payload["pending_count"] == 1
    assert payload["runtime"]["initialized_group_count"] == 0


def test_decision_serializer_hides_context_when_disabled():
    row = row_with_context()
    hidden = serialize_shadow_decision(row, include_context=False)
    visible = serialize_shadow_decision(row, include_context=True)

    assert hidden["message_preview"] == "未保存文本"
    assert "context" not in hidden
    assert visible["context"][0]["sender"] == "成员1"


def test_filter_parser_rejects_invalid_values():
    with pytest.raises(ValueError):
        parse_shadow_filters({"label": "drop-table"})
    with pytest.raises(ValueError):
        parse_shadow_filters({"limit": "999"})
```

The test module should also verify `pause`/`resume` only change `bridge.paused`, and that a repeated label update returns a successful state without changing the decision action.

- [ ] **Step 2: Run the new tests and verify failure**

Run: `pytest -q tests/test_web_api.py`

Expected: FAIL with missing imports/functions from `groupmate.web_api`.

- [ ] **Step 3: Implement pure API helpers**

Implement in `groupmate/web_api.py`:

- `PLUGIN_NAME = "astrbot_plugin_groupmate"`.
- `parse_shadow_filters(params)` returning `(label, action, limit, cursor)` with enum/range checks.
- `serialize_shadow_decision(row, include_context)` that exposes only `decision_id`, `trigger`, `action`, `confidence`, `reason_code`, `would_rate_limit`, `label`, `created_at`, `labeled_at`, `model_id`, `policy_version`, `latency_ms`, `error_code`, `message_preview`, and optional sanitized `context`; omit `id`, `group_hash`, `sender_hash`, `features_json`, and `context_json`.
- `build_overview_payload(bridge)` using `bridge.status()`, `bridge.memory.shadow_stats()`, and a three-item unlabeled page. Include `runtime`, `pending_count`, `recent`, `actions`, `labels`, `reasons`, and `data_policy`. Derive `sample_sufficient` from labeled count `>= 100`; do not invent evaluation percentages.
- `normalize_label(label)` accepting the four storage labels only.

For context serialization, parse JSON defensively and preserve only the collector fields (`index`, `sender`, `text`, `seconds_from_start`, `reply`, `mentions_bot`, `reply_to_bot`, `is_command`, `is_bot`, `segment_types`). If text is absent or malformed, return the “未保存文本” preview and no context.

- [ ] **Step 4: Implement async AstrBot handlers with version fallback**

Add `GroupmateWebAPI(bridge, data_dir)` with `register(context)` and handlers:

```python
context.register_web_api(f"/{PLUGIN_NAME}/dashboard/overview", self.overview, ["GET"], "Groupmate overview")
context.register_web_api(f"/{PLUGIN_NAME}/shadow/decisions", self.decisions, ["GET"], "List shadow decisions")
context.register_web_api(f"/{PLUGIN_NAME}/shadow/decisions/<decision_id>/label", self.label, ["POST"], "Label a shadow decision")
context.register_web_api(f"/{PLUGIN_NAME}/runtime/pause", self.pause, ["POST"], "Pause Groupmate")
context.register_web_api(f"/{PLUGIN_NAME}/runtime/resume", self.resume, ["POST"], "Resume Groupmate")
context.register_web_api(f"/{PLUGIN_NAME}/shadow/export", self.export, ["GET"], "Export reviewed shadow data")
```

Import `astrbot.api.web` lazily inside handlers; when unavailable, use `quart.request`, `quart.jsonify`, `quart.send_file`, and a small `error_response` fallback. The handlers must:

- Read query/body values only through the request proxy.
- Return JSON errors with 400 for invalid filters/labels, 404 for unknown decisions, and 500 with a generic message for storage/export failures.
- Use `bridge.memory.shadow_decision_page` and the pure serializers.
- Return the updated decision state from `label` and never mutate prediction fields.
- Write export output only under `data_dir / "exports"`, call `export_labeled_shadow_dataset`, and return a file response with `shadow_reviewed.jsonl`.

- [ ] **Step 5: Add bridge convenience methods and run tests**

Add to `AstrBotBridge`:

- `web_overview()` delegating to `build_overview_payload(self)`.
- `web_shadow_decisions(...)` delegating to `self.memory.shadow_decision_page(...)` and serialization.
- `web_label_shadow_decision(decision_id, label)` validating through the existing normalizer and returning the updated row or `None`.

Run: `pytest -q tests/test_web_api.py tests/test_shadow_storage.py tests/test_shadow_bridge.py`

Expected: PASS.

- [ ] **Step 6: Commit the Web API boundary**

```bash
git add groupmate/web_api.py groupmate/astrbot_adapter.py tests/test_web_api.py
git commit -m "feat: expose privacy-safe groupmate web api"
```

### Task 3: Register the Web API from the AstrBot plugin

**Files:**
- Modify: `main.py:8-25`
- Modify: `tests/test_plugin_loading.py`

- [ ] **Step 1: Add a fake registration test**

Extend the plugin-loading stub with a `Context` that records `register_web_api` calls, instantiate `GroupmatePlugin` with a minimal config, and assert all six route suffixes are registered. Keep the existing import-only test unchanged.

- [ ] **Step 2: Run the loading tests and verify failure**

Run: `pytest -q tests/test_plugin_loading.py`

Expected: FAIL because `GroupmatePlugin` does not create/register `GroupmateWebAPI`.

- [ ] **Step 3: Register routes after bridge construction**

Import `GroupmateWebAPI`, create `self.web_api = GroupmateWebAPI(self.bridge, data_dir)`, and call `self.web_api.register(context)` immediately after constructing `self.bridge`. Do not change existing command handlers or runtime shutdown behavior.

- [ ] **Step 4: Run loading and regression tests**

Run: `pytest -q tests/test_plugin_loading.py tests/test_shadow_bridge.py`

Expected: PASS.

- [ ] **Step 5: Commit plugin registration**

```bash
git add main.py tests/test_plugin_loading.py
git commit -m "feat: register groupmate plugin pages api"
```

### Task 4: Add the dependency-free Plugin Page

**Files:**
- Create: `pages/control-center/index.html`
- Create: `pages/control-center/app.js`
- Create: `pages/control-center/style.css`
- Test: `tests/test_plugin_page_assets.py`

- [ ] **Step 1: Write static asset smoke tests**

Add tests that assert the page files exist, `index.html` references `./style.css` and `./app.js`, and `app.js` references only `window.AstrBotPluginPage` endpoints (`dashboard/overview`, `shadow/decisions`, `runtime/pause`, `runtime/resume`, `shadow/export`). Reject absolute API URLs and external script tags.

- [ ] **Step 2: Run the asset tests and verify failure**

Run: `pytest -q tests/test_plugin_page_assets.py`

Expected: FAIL because `pages/control-center/` does not exist.

- [ ] **Step 3: Implement the page shell**

Create a semantic HTML shell with:

- `aria-live` status and error regions.
- Overview and Review navigation driven by `location.hash`.
- Status strip, pending review CTA, recent decisions list, action/label summaries, privacy note, refresh, pause/resume, and export controls.
- Review filters, paginated list, selected decision detail, context block, four label buttons, and next-unlabeled behavior.

Use native controls and CSS variables for AstrBot light/dark themes. Keep text and actions visible at 320px width; use one accent color for primary actions and status, with text labels for all decision states.

- [ ] **Step 4: Implement the bridge client**

In `app.js`, await `bridge.ready()`, then implement:

- `loadOverview()` -> `bridge.apiGet("dashboard/overview")`.
- `loadDecisions()` -> `bridge.apiGet("shadow/decisions", filters)`.
- `labelDecision(id, label)` -> `bridge.apiPost("shadow/decisions/" + encodeURIComponent(id) + "/label", {label})`.
- `setPaused(paused)` -> `bridge.apiPost("runtime/" + (paused ? "pause" : "resume"))`.
- `downloadExport()` -> `bridge.download("shadow/export", {}, "shadow_reviewed.jsonl")`.

Keep the last successful overview in memory, show retry text on failures, update pending counts after a label, and never render raw API errors or identity fields.

- [ ] **Step 5: Run asset and syntax checks**

Run: `pytest -q tests/test_plugin_page_assets.py` and `python -m compileall -q main.py groupmate tests`.

Expected: PASS.

- [ ] **Step 6: Commit the page**

```bash
git add pages/control-center tests/test_plugin_page_assets.py
git commit -m "feat: add groupmate control center page"
```

### Task 5: Verify the full feature and compatibility boundaries

**Files:**
- Modify: `README.md` (document Page entry and supported actions)
- Test: existing `tests/` suite

- [ ] **Step 1: Add concise README usage documentation**

Document that AstrBot administrators open the Groupmate plugin detail page, then use Control Center for runtime status and shadow review; retain `_conf_schema.json` as the configuration source of truth and explain that pause/resume is runtime-only.

- [ ] **Step 2: Run the complete test suite and schema checks**

Run:

```bash
pytest -q
python -m compileall -q main.py groupmate tests
python -m json.tool _conf_schema.json >/dev/null
git diff --check
```

Expected: all tests pass, compile and JSON commands exit 0, and `git diff --check` prints nothing.

- [ ] **Step 3: Review the final diff for privacy and scope**

Confirm no endpoint serializes `group_id`, `group_hash`, `sender_hash`, raw OneBot metadata, database paths, or API keys; confirm no new dependency or server port was added; confirm existing unrelated worktree changes remain untouched.

- [ ] **Step 4: Commit documentation and final verification**

```bash
git add README.md
git commit -m "docs: document groupmate control center"
```

