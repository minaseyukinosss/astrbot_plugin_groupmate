# Groupmate Shadow Recent Command Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an admin-only `/groupmate_shadow_recent [limit]` command that lists recent shadow decisions for the current group without exposing raw identities or unredacted context.

**Architecture:** `SQLiteMemoryStore` performs the bounded, group-scoped query and extracts only the final valid context message. `HmacIdentityHasher` and `AstrBotBridge` reuse an existing HMAC key to map the current group without creating a key during read-only access. A framework-free `shadow_admin` module owns limit normalization and safe Chinese text rendering, while `main.py` remains a thin AstrBot command wrapper.

**Tech Stack:** Python 3, SQLite, AstrBot plugin decorators, pytest

---

### Task 1: Group-scoped storage query

**Files:**
- Modify: `groupmate/memory.py`
- Modify: `tests/test_shadow_storage.py`

- [ ] **Step 1: Write failing storage tests**

Add tests that insert records for two group hashes, include valid and malformed `context_json`, and assert `recent_shadow_decisions("group-a", limit)` returns only group A, newest first, with the last valid text message exposed as `latest_message`. Assert limits are clamped to `1..10` and sensitive columns such as `group_hash`, `sender_hash`, `features_json`, and `model_id` are absent.

```python
def test_recent_shadow_decisions_are_group_scoped_and_newest_first(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    store.save_shadow_decision(record(decision_id="a-old", group_hash="a", created_at=10))
    store.save_shadow_decision(record(decision_id="b", group_hash="b", created_at=30))
    store.save_shadow_decision(record(
        decision_id="a-new",
        group_hash="a",
        created_at=20,
        context=[{"sender": "成员1", "text": "旧"}, {"sender": "成员2", "text": "最新"}],
    ))

    rows = store.recent_shadow_decisions("a", 10)

    assert [row["decision_id"] for row in rows] == ["a-new", "a-old"]
    assert rows[0]["latest_message"] == {"sender": "成员2", "text": "最新"}
    assert not ({"group_hash", "sender_hash", "features_json", "model_id"} & rows[0].keys())
    store.close()
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `pytest tests/test_shadow_storage.py -q`

Expected: failure because `SQLiteMemoryStore.recent_shadow_decisions` does not exist.

- [ ] **Step 3: Implement the minimal storage API**

Add `recent_shadow_decisions(group_hash: str, limit: int = 5) -> List[Dict[str, Any]]`. Clamp `limit`, select only render fields, order by `created_at DESC, id DESC`, parse `context_json` defensively, accept only a JSON list of dictionaries, and scan backward for the last item whose `text` is a non-empty string.

- [ ] **Step 4: Run storage tests and verify GREEN**

Run: `pytest tests/test_shadow_storage.py -q`

Expected: all storage tests pass.

### Task 2: Existing HMAC key loading and bridge lookup

**Files:**
- Modify: `groupmate/evaluation/shadow.py`
- Modify: `groupmate/astrbot_adapter.py`
- Modify: `tests/test_shadow_workflow.py`
- Modify: `tests/test_shadow_bridge.py`

- [ ] **Step 1: Write failing key-loading and bridge tests**

Cover `HmacIdentityHasher.load_existing(path)` returning `None` for a missing key, returning a hasher with the same digest for a valid existing 32-byte key, and raising the existing `ValueError` for a malformed key. In the bridge test, save records for two HMAC-derived group hashes and assert `recent_shadow_decisions("group-a", 5)` returns only group A. Also assert a bridge with shadow mode disabled and no key returns an empty list without creating `shadow_hmac.key`.

```python
def test_load_existing_hasher_does_not_create_missing_key(tmp_path):
    path = tmp_path / "shadow.key"
    assert HmacIdentityHasher.load_existing(path) is None
    assert not path.exists()

def test_bridge_reads_only_current_group_shadow_records(tmp_path):
    bridge = AstrBotBridge(FakeContext(), PluginSettings(shadow_mode=True), tmp_path)
    bridge.memory.save_shadow_decision(record_for(bridge._shadow_hasher.digest("g1"), "d1"))
    bridge.memory.save_shadow_decision(record_for(bridge._shadow_hasher.digest("g2"), "d2"))
    assert [row["decision_id"] for row in bridge.recent_shadow_decisions("g1", 5)] == ["d1"]
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `pytest tests/test_shadow_workflow.py tests/test_shadow_bridge.py -q`

Expected: failures because `load_existing` and the bridge query method do not exist.

- [ ] **Step 3: Implement read-only key loading and bridge mapping**

Construct a hasher without invoking `_load_or_create` when a valid key already exists. Add `AstrBotBridge.recent_shadow_decisions(group_id, limit=5)` that reuses `_shadow_hasher`, otherwise calls `load_existing`, returns `[]` when the key is missing, hashes the group ID, and delegates to the store.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `pytest tests/test_shadow_workflow.py tests/test_shadow_bridge.py -q`

Expected: all key-loading and bridge tests pass.

### Task 3: Safe framework-free command rendering

**Files:**
- Create: `groupmate/shadow_admin.py`
- Create: `tests/test_shadow_admin.py`

- [ ] **Step 1: Write failing renderer tests**

Test default and bounded limits, the empty result, Chinese action/label mappings, two-decimal confidence, local timestamp formatting, rate-limit marker, complete decision ID, the final valid redacted message, sender validation against `^成员\\d+$`, whitespace compression, and an 80-character maximum ending in `...`.

```python
def test_render_shadow_decisions_is_private_and_actionable():
    text = render_shadow_decisions([{
        "decision_id": "complete-id",
        "trigger": "candidate",
        "action": "respond",
        "confidence": 0.836,
        "reason_code": "useful_contribution",
        "would_rate_limit": True,
        "label": "unlabeled",
        "created_at": 0,
        "latest_message": {"sender": "raw-user", "text": "  hello\nworld  "},
    }])
    assert "ID: complete-id" in text
    assert "判断: 回复 0.84 | 原因: useful_contribution | 标签: 未标注 | 会被限流" in text
    assert "消息: hello world" in text
    assert "raw-user" not in text
    assert "标注：/groupmate_shadow_label" in text
```

- [ ] **Step 2: Run the renderer tests and verify RED**

Run: `pytest tests/test_shadow_admin.py -q`

Expected: import failure because `groupmate.shadow_admin` does not exist.

- [ ] **Step 3: Implement minimal normalization and rendering helpers**

Create `normalize_recent_limit(value, default=5)`, `render_shadow_decisions(rows)`, and private helpers for safe short values, summaries, and local timestamps. Unknown action/label values may be shown only after whitespace removal and bounded truncation; invalid or missing text renders as `未保存文本`.

- [ ] **Step 4: Run renderer tests and verify GREEN**

Run: `pytest tests/test_shadow_admin.py -q`

Expected: all renderer tests pass.

### Task 4: AstrBot command entry and documentation

**Files:**
- Modify: `main.py`
- Modify: `README.md`
- Test: `tests/test_shadow_admin.py`

- [ ] **Step 1: Add a failing command-handler helper test**

Keep AstrBot decorators outside offline tests. Add a framework-free helper in `shadow_admin.py` that takes `group_id`, requested limit, and a lookup callable, returning the non-group, empty, success, or generic read-error message. Test that the lookup receives the normalized limit and exceptions become `影子决策记录暂时无法读取。`.

- [ ] **Step 2: Run the helper tests and verify RED**

Run: `pytest tests/test_shadow_admin.py -q`

Expected: failure because the handler helper does not exist.

- [ ] **Step 3: Implement and wire `/groupmate_shadow_recent`**

Add the admin-only command with `limit: int = 5`. Obtain `event.get_group_id()`, reject a missing group ID, normalize to `1..10`, call `bridge.recent_shadow_decisions`, log query exceptions with `logger.exception`, and yield `event.plain_result(...)`. Add the command to the README administrator table.

- [ ] **Step 4: Run focused feature tests and verify GREEN**

Run: `pytest tests/test_shadow_admin.py tests/test_shadow_storage.py tests/test_shadow_bridge.py tests/test_shadow_workflow.py -q`

Expected: all focused feature tests pass.

### Task 5: Full regression verification

**Files:**
- Modify only if a regression is uncovered by a failing test.

- [ ] **Step 1: Run the complete test suite**

Run: `pytest -q`

Expected: the entire suite passes with no warnings or errors.

- [ ] **Step 2: Inspect the final diff and privacy surface**

Run: `git diff --check`

Expected: no whitespace errors.

Run: `git diff -- groupmate/memory.py groupmate/evaluation/shadow.py groupmate/astrbot_adapter.py groupmate/shadow_admin.py main.py README.md tests`

Expected: no output path exposes `group_hash`, `sender_hash`, full `context_json`, model IDs, latency, errors, or feature JSON.

- [ ] **Step 3: Record manual AstrBot checks still required**

After deployment to a test group, run `/groupmate_shadow_recent`, `/groupmate_shadow_recent 1`, `/groupmate_shadow_recent 10`, and `/groupmate_shadow_recent 50`; verify group isolation, administrator permissions, WebUI command registration, and that a displayed full ID works with `/groupmate_shadow_label`.
