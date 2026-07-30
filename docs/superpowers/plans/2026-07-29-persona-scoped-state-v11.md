# Persona-Scoped State V11 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the SQLite store to schema v11 and require explicit `persona_id`（人格标识） on every state read/write so Aemeath and future personas cannot share relationship, memory, conversation, or delivery state.

**Architecture:** Back up every non-empty legacy database, rebuild persona-bearing tables inside one transaction, and backfill all existing rows to `aemeath`. Thread the resolved persona ID through store APIs, runtime actors, projections, social state, memory writing, delivery, evaluation, and status without any normal-runtime default persona.

**Tech Stack:** Python 3, sqlite3, transactional `create-copy-verify-swap` migrations, asyncio write worker, pytest, deterministic evaluation and shadow projection.

**Design Spec:** `docs/superpowers/specs/2026-07-29-configuration-persona-scope-design.md`

**Execution Status:** Completed on 2026-07-30.

**Delivered By:** `181bf14`（schema v11、全链路人格隔离、真实 shadow 回归与恢复文档）.

**Completion Evidence:** `pytest` 601 passed; deterministic baseline 120/120 passed; Phase 2 behavior evaluation 10/10 passed; real export shadow processed 11,304 records with zero safety violations and no reply/silence regression; `git diff --check` passed.

**Prerequisite:** `docs/superpowers/plans/2026-07-29-minimal-config-policy-cleanup.md` is complete and all tests pass.

---

## File Map（文件职责）

- Modify `groupmate/memory/migrations.py`: direct v11 bootstrap, v5-v10 upgrade chain, v10-v11 rebuild, verification, backup and rollback.
- Modify `groupmate/memory/store.py`: explicit persona scope on every state API and SQL predicate.
- Modify `groupmate/memory/{memory_writer,arbiter}.py`: pass persona scope through candidate and accepted-memory operations.
- Modify `groupmate/engine/{runtime,workflow,direct_pressure,delivery}.py`: persona-scoped actors, pressure, state lookup, decisions and delivery.
- Modify `groupmate/core/projections.py`: rebuild only current-persona state.
- Modify `groupmate/social/projector.py`: keep projection pure while callers provide persona-specific event streams.
- Modify `groupmate/host/{bridge,web_api}.py`: pass `PersonaContext.persona_id` and report schema v11.
- Modify `eval/{runner,shadow_projector,shadow_export}.py`: use explicit persona scope in isolated stores and projections.
- Create `tests/test_persona_scope_migrations.py`: bootstrap, backfill, constraint and rollback coverage.
- Create `tests/test_persona_scope_store.py`: cross-persona read/write isolation.
- Update all existing migration, memory, delivery, runtime, projection, social, evaluation and shadow tests.

### Task 1: Direct V11 Bootstrap And Transactional Migration（直接创建 v11 与事务迁移）

**Files:**
- Modify: `groupmate/memory/migrations.py`
- Create: `tests/test_persona_scope_migrations.py`
- Modify: `tests/test_phase1_migrations.py`
- Modify: `tests/test_phase2_migrations.py`
- Modify: `tests/test_phase3_migrations.py`
- Modify: `tests/test_phase5_migrations.py`
- Modify: `tests/test_phase6_migrations.py`

- [x] **Step 1: Write v11 bootstrap and migration tests**

```python
PERSONA_TABLES = {
    "messages",
    "profiles",
    "memories",
    "decisions",
    "outbox",
    "topic_epochs",
    "continuation_grants",
    "social_events",
    "relationship_state",
    "memory_candidates",
    "memory_tombstones",
}


def _columns(db, table):
    return {row[1]: row for row in db.execute(f"PRAGMA table_info({table})")}


def test_empty_database_bootstraps_directly_to_v11(tmp_path):
    path = tmp_path / "new.db"
    migrate_database(path)
    db = sqlite3.connect(str(path))
    assert db.execute("SELECT value FROM schema_meta WHERE key='version'").fetchone()[0] == "11"
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "favorability" not in tables
    for table in PERSONA_TABLES:
        column = _columns(db, table)["persona_id"]
        assert column[3] == 1
        assert column[4] is None
    db.close()


def test_v10_rows_are_backfilled_to_aemeath_and_backup_is_created(tmp_path):
    path = build_v10_fixture_with_one_row_per_table(tmp_path)
    before = table_counts(path, PERSONA_TABLES)
    migrate_database(path)
    db = sqlite3.connect(str(path))
    for table, count in before.items():
        assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == count
        assert db.execute(f"SELECT COUNT(*) FROM {table} WHERE persona_id='aemeath'").fetchone()[0] == count
    assert "favorability" not in {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    db.close()
    assert list(tmp_path.glob("*.pre-migrate-v10-to-v11.*"))


def test_failed_v11_verification_rolls_back_v10_database(tmp_path, monkeypatch):
    path = build_v10_fixture_with_one_row_per_table(tmp_path)
    monkeypatch.setattr(migrations, "_verify_v11", lambda db: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError, match="boom"):
        migrate_database(path)
    db = sqlite3.connect(str(path))
    assert db.execute("SELECT value FROM schema_meta WHERE key='version'").fetchone()[0] == "10"
    assert "persona_id" not in _columns(db, "relationship_state")
    db.close()
```

`build_v10_fixture_with_one_row_per_table`（构造 v10 完整夹具） must call existing `_bootstrap_v5` through `_v9_to_v10`, insert valid linked sample rows into all 11 tables, and insert a legacy `favorability` row already represented in `relationship_state`.

- [x] **Step 2: Run the migration tests and verify RED**

Run: `./.venv/bin/python -m pytest tests/test_persona_scope_migrations.py -q`

Expected: FAIL because schema version is still 10 and `persona_id` columns do not exist.

- [x] **Step 3: Add `_bootstrap_v11`（直接创建 v11）**

Set `SCHEMA_VERSION = 11`. Create the complete current schema directly for empty databases. Every listed table has `persona_id TEXT NOT NULL` without a default. Use these semantic constraints:

```sql
PRIMARY KEY (persona_id, group_id, message_id)                  -- messages
PRIMARY KEY (persona_id, group_id, subject_id)                  -- profiles
PRIMARY KEY (persona_id, group_id, user_id)                     -- relationship_state
UNIQUE (persona_id, group_id, source_message_id, kind)          -- social_events
UNIQUE (persona_id, group_id, subject_id, claim_hash)            -- memory_candidates
UNIQUE (persona_id, group_id, subject_id, claim_hash)            -- memory_tombstones
```

Keep globally unique surrogate keys (`memory_id`, `candidate_id`, `decision_id`, `grant_id`, tombstone ID, decision row ID) in their current primary-key form. Add persona-leading indexes for every query pattern, including messages by ingest time, open topics, continuation by sender/time, memories by subject/status, decisions by decision ID, and outbox by status/time.

- [x] **Step 4: Implement `_v10_to_v11` with table rebuilds**

For each persona table, create `<table>_v11` with the exact v11 definition, copy every old column while inserting `'aemeath'`, verify row count and non-empty persona IDs, then swap names. Use one helper with explicit SQL supplied by the caller:

```python
def _rebuild_with_persona(db, *, table, create_sql, insert_columns, select_columns):
    target = table + "_v11"
    db.execute(create_sql)
    db.execute(
        "INSERT INTO {}(persona_id,{}) SELECT 'aemeath',{} FROM {}".format(
            target, insert_columns, select_columns, table
        )
    )
    old_count = db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
    new_count = db.execute("SELECT COUNT(*) FROM " + target).fetchone()[0]
    if old_count != new_count:
        raise SchemaMigrationError("row-count mismatch for " + table)
    db.execute("DROP TABLE " + table)
    db.execute("ALTER TABLE {} RENAME TO {}".format(target, table))
```

All table names and column lists passed to this internal helper are compile-time constants from the migration module; never accept user input. Drop `favorability` only after relationship row counts and affinity backfill are verified. Recreate indexes after all swaps, call `_verify_v11`, update the schema version, then commit.

- [x] **Step 5: Route empty and legacy databases correctly**

In `migrate_database`（迁移数据库）:

```python
if current == 0:
    with db:
        _bootstrap_v11(db)
    current = 11
else:
    # Existing v5-v10 chain remains, then _v10_to_v11 runs.
```

Create a backup for every non-empty database whose version is below 11. Update old migration tests to expect final version 11 and backup names ending in `to-v11`.

- [x] **Step 6: Verify GREEN**

Run: `./.venv/bin/python -m pytest tests/test_persona_scope_migrations.py tests/test_phase1_migrations.py tests/test_phase2_migrations.py tests/test_phase3_migrations.py tests/test_phase5_migrations.py tests/test_phase6_migrations.py -q`

Expected: all migration tests pass.

- [x] **Step 7: Commit**

```bash
git add groupmate/memory/migrations.py tests/test_persona_scope_migrations.py tests/test_phase1_migrations.py tests/test_phase2_migrations.py tests/test_phase3_migrations.py tests/test_phase5_migrations.py tests/test_phase6_migrations.py
git commit -m "feat: migrate state database to persona-scoped v11"
```

### Task 2: Persona-Scoped Ledger, Profiles, Decisions, And Outbox（消息账本、资料、决策与发送凭证）

**Files:**
- Modify: `groupmate/memory/store.py`
- Create: `tests/test_persona_scope_store.py`
- Modify: `tests/test_memory.py`
- Modify: `tests/test_delivery_service.py`
- Modify: `tests/test_phase2_projections.py`

- [x] **Step 1: Add cross-persona ledger and outbox tests**

```python
def test_same_platform_message_is_isolated_by_persona(store, message):
    assert store.save_message("aemeath", message)
    assert store.save_message("future", message)
    assert [m.message_id for m in store.recent_messages("aemeath", "g1", 10)] == [message.message_id]
    assert [m.message_id for m in store.recent_messages("future", "g1", 10)] == [message.message_id]


def test_outbox_and_decisions_require_matching_persona(store):
    store.enqueue_outbox("aemeath", "d1", "g1", "在呢。", 100)
    assert store.outbox_record("aemeath", "d1") is not None
    assert store.outbox_record("future", "d1") is None
    store.record_transition("aemeath", "d1", "g1", "END", "sent", 101)
    assert store.recent_decision_ends("future", "g1") == []


def test_empty_persona_id_is_rejected_before_sql(store, message):
    with pytest.raises(ValueError, match="persona_id"):
        store.save_message("", message)
```

- [x] **Step 2: Verify RED**

Run: `./.venv/bin/python -m pytest tests/test_persona_scope_store.py -q`

Expected: FAIL because store methods do not accept persona IDs.

- [x] **Step 3: Add one strict persona validator**

```python
def _require_persona_id(value: str) -> str:
    persona_id = str(value or "").strip()
    if not persona_id:
        raise ValueError("persona_id is required")
    return persona_id
```

Every public state method calls this before SQL. Do not default missing values to `aemeath`.

- [x] **Step 4: Scope foundational store APIs**

Change message/profile/decision/outbox methods to begin with `persona_id`, insert it, and include it in every predicate:

```python
save_message(persona_id, message)
save_message_async(persona_id, message)
recent_messages(persona_id, group_id, limit)
list_ledger_messages(persona_id, group_id, limit=100)
list_bot_deliveries(persona_id, group_id, limit=20)
upsert_profile(persona_id, group_id, subject_id, display_name, relationship, authority, updated_at=0)
get_profile(persona_id, group_id, subject_id)
record_transition(persona_id, decision_id, group_id, state, reason, timestamp)
recent_decision_ends(persona_id, group_id, limit=3)
enqueue_outbox(persona_id, decision_id, group_id, text, created_at, expires_at=None, quote_message_id=None, segments=(), outbound=(), kind="reply")
outbox_record(persona_id, decision_id)
transition_outbox_async(persona_id, decision_id, expected, status, failure_code="", failure_detail="", increment_attempt=False)
finalize_delivery_async(persona_id, decision_id, sent_at, bot_message, reason="sent")
pending_outbox(persona_id, now)
```

Pass persona ID through `_message_params`, `_insert_message`, and asynchronous static helpers. Startup/shutdown recovery may update all personas only in an explicitly named `mark_all_sending_unknown_async` operational method; persona runtime methods must stay scoped.

- [x] **Step 5: Verify GREEN**

Run: `./.venv/bin/python -m pytest tests/test_persona_scope_store.py tests/test_memory.py tests/test_delivery_service.py -q`

Expected: selected tests pass after callers supply `"aemeath"` explicitly.

- [x] **Step 6: Commit**

```bash
git add groupmate/memory/store.py tests/test_persona_scope_store.py tests/test_memory.py tests/test_delivery_service.py
git commit -m "refactor: scope ledger and outbox by persona"
```

### Task 3: Persona-Scoped Memories And Tombstones（记忆、候选与删除标记）

**Files:**
- Modify: `groupmate/memory/store.py`
- Modify: `groupmate/memory/memory_writer.py`
- Modify: `groupmate/memory/arbiter.py`
- Modify: `tests/test_memory.py`
- Modify: `tests/test_memory_writer.py`
- Modify: `tests/test_memory_arbiter.py`
- Modify: `tests/test_persona_scope_store.py`

- [x] **Step 1: Add memory-isolation tests**

```python
def test_memories_with_same_group_and_subject_do_not_cross_personas(store, memory):
    store.add_memory("aemeath", memory)
    assert store.search_memories("aemeath", "g1", "喜欢", now=100, limit=8)
    assert store.search_memories("future", "g1", "喜欢", now=100, limit=8) == []


def test_same_claim_can_be_candidate_for_two_personas(store, candidate):
    assert store.append_memory_candidate("aemeath", candidate) is not None
    assert store.append_memory_candidate("future", replace(candidate, candidate_id="c2")) is not None
    assert len(store.list_memory_candidates("aemeath", "g1")) == 1
    assert len(store.list_memory_candidates("future", "g1")) == 1
```

- [x] **Step 2: Verify RED**

Run: `./.venv/bin/python -m pytest tests/test_persona_scope_store.py -q`

Expected: memory isolation fails because queries are only group-scoped.

- [x] **Step 3: Scope every memory API**

Use explicit signatures and `WHERE persona_id=?` in every query/update:

```python
add_memory(persona_id, memory)
list_memories(persona_id, group_id, kind=None, now=now, limit=20, subject_id=None, status_accepted_only=True, statuses=None)
get_memory(persona_id, memory_id)
search_memories(persona_id, group_id, query, now, limit, subject_id=None, subject_ids=None, include_user_in_group=True)
append_memory_candidate(persona_id, candidate)
get_memory_candidate(persona_id, candidate_id)
list_memory_candidates(persona_id, group_id, status=None, limit=50)
decide_candidate(persona_id, candidate_id, status, reason="", decided_at=now)
accept_candidate_memory(persona_id, candidate_id, memory, reason=reason, decided_at=now, superseded_memory_id=None)
correct_memory(persona_id, memory_id, new_text, authority=authority, now=now, source_message_ids=None)
delete_memory(persona_id, memory_id, reason, *, now)
is_tombstoned(persona_id, group_id, subject_id, claim_hash)
```

Supersede and delete updates must include both persona ID and memory ID. Candidate conflict lookup includes `persona_id + group_id + subject_id + claim_hash`.

- [x] **Step 4: Thread persona through writer and arbiter**

`MemoryWriter`（记忆写入器） receives `persona_id` in its constructor and passes it to every candidate, conflict, accept, and memory lookup. Keep `MemoryItem` and `MemoryCandidate` as content values; the store method remains the mandatory ownership boundary.

```python
MemoryWriter(
    store,
    persona_id=persona_context.persona_id,
    privacy=privacy,
    arbiter=arbiter,
    bot_id=bot_id,
    on_error=on_error,
)
```

- [x] **Step 5: Verify GREEN**

Run: `./.venv/bin/python -m pytest tests/test_persona_scope_store.py tests/test_memory.py tests/test_memory_writer.py tests/test_memory_arbiter.py -q`

Expected: all selected tests pass.

- [x] **Step 6: Commit**

```bash
git add groupmate/memory/store.py groupmate/memory/memory_writer.py groupmate/memory/arbiter.py tests/test_persona_scope_store.py tests/test_memory.py tests/test_memory_writer.py tests/test_memory_arbiter.py
git commit -m "refactor: isolate memories by persona"
```

### Task 4: Persona-Scoped Social Events And Relationship State（社交事件与关系状态）

**Files:**
- Modify: `groupmate/memory/store.py`
- Modify: `groupmate/engine/workflow.py`
- Modify: `tests/test_social_events.py`
- Modify: `tests/test_affinity.py`
- Modify: `tests/test_persona_scope_store.py`

- [x] **Step 1: Add relationship-isolation and seed tests**

```python
def test_same_user_has_independent_affinity_per_persona(store, social_event):
    aemeath = store.record_social_interaction(
        "aemeath", social_event, configured_relationship="闺蜜", now=100
    )
    future = store.get_relationship_state("future", "g1", "u1")
    assert aemeath.affinity >= 50
    assert future is None


def test_existing_state_is_not_overwritten_by_changed_seed(store, social_event):
    first = store.record_social_interaction(
        "aemeath", social_event, configured_relationship="普通群友", now=100
    )
    current = store.get_relationship_state("aemeath", "g1", "u1")
    assert current == first
```

- [x] **Step 2: Verify RED**

Run: `./.venv/bin/python -m pytest tests/test_persona_scope_store.py tests/test_social_events.py -q`

Expected: FAIL because events and relationship state are not persona-scoped.

- [x] **Step 3: Scope the social APIs**

```python
append_social_event(persona_id, event)
list_social_events(persona_id, group_id, user_id=None, limit=100)
get_relationship_state(persona_id, group_id, user_id)
upsert_relationship_state(persona_id, state)
rebuild_relationship_state(persona_id, group_id, user_id, configured_relationship=None, seed_affinity=0, now=0)
record_social_interaction(persona_id, event, *, configured_relationship=None, now=0)
```

All event deduplication and relationship upserts include persona ID. Initial affinity is applied only when no state exists in the current persona scope. Once a row exists, later configuration changes do not replace it.

- [x] **Step 4: Pass workflow persona ID to social reads and writes**

Use `self.persona_context.persona_id` for relationship lookup and interaction recording. The relationship seed lookup uses `self.persona_context.relationship_seeds`; no global or hard-coded fallback is allowed.

- [x] **Step 5: Verify GREEN**

Run: `./.venv/bin/python -m pytest tests/test_persona_scope_store.py tests/test_social_events.py tests/test_affinity.py tests/test_participation_decision.py -q`

Expected: all selected tests pass.

- [x] **Step 6: Commit**

```bash
git add groupmate/memory/store.py groupmate/engine/workflow.py tests/test_persona_scope_store.py tests/test_social_events.py tests/test_affinity.py tests/test_participation_decision.py
git commit -m "refactor: isolate relationship state by persona"
```

### Task 5: Persona-Scoped Topics, Continuations, Pressure, And Actors（话题、续聊、压力与执行器）

**Files:**
- Modify: `groupmate/memory/store.py`
- Modify: `groupmate/engine/direct_pressure.py`
- Modify: `groupmate/engine/runtime.py`
- Modify: `groupmate/core/projections.py`
- Modify: `tests/test_direct_pressure.py`
- Modify: `tests/test_runtime.py`
- Modify: `tests/test_phase2_projections.py`
- Modify: `tests/test_persona_scope_store.py`

- [x] **Step 1: Add conversation-state isolation tests**

```python
def test_continuation_grants_are_persona_scoped(store):
    store.grant_continuation(
        persona_id="aemeath", grant_id="g", group_id="g1", sender_id="u1",
        opened_by_decision_id="d", opened_by_message_id="m", trigger_kind="alias_direct",
        granted_at=100, expires_at=190, max_total_seconds=300,
    )
    assert store.latest_continuation_grant("aemeath", "g1", 120, "u1") is not None
    assert store.latest_continuation_grant("future", "g1", 120, "u1") is None


def test_pressure_key_includes_persona(message):
    tracker = DirectAddressPressureTracker()
    tracker.observe("aemeath", message, TriggerKind.NATIVE_DIRECT, now=100, aliases=("爱弥斯",))
    future = tracker.observe("future", message, TriggerKind.NATIVE_DIRECT, now=101, aliases=("新人格",))
    assert future.level is DirectAddressPressureLevel.NORMAL


async def test_runtime_actors_are_separate_per_persona(manager, contexts):
    aemeath = await manager.actor_for("g1", contexts["aemeath"])
    future = await manager.actor_for("g1", contexts["future"])
    assert aemeath is not future
    assert future.window.snapshot().messages == ()
```

- [x] **Step 2: Verify RED**

Run: `./.venv/bin/python -m pytest tests/test_direct_pressure.py tests/test_runtime.py tests/test_phase2_projections.py tests/test_persona_scope_store.py -q`

Expected: isolation tests fail because keys only contain group/user.

- [x] **Step 3: Scope topic and continuation store methods**

Add `persona_id` to `latest_open_topic_epoch`, `open_topic_epoch`, `close_topic_epoch`, async helpers, `grant_continuation`, `latest_continuation_grant`, and `list_active_continuation_grants`. Every open-topic close/update and sender lookup includes persona ID.

- [x] **Step 4: Scope in-memory pressure and actors**

`DirectAddressPressureTracker.observe`（记录直接呼叫压力） takes persona ID and keys counters by `(persona_id, group_id, sender_id)`. `GroupRuntimeManager` keys actors by `(persona_id, group_id)` and accepts a resolved `PersonaContext`:

```python
async def actor_for(self, group_id: str, persona_context: PersonaContext) -> GroupActor:
    key = (persona_context.persona_id, str(group_id))
```

Different persona contexts always create different actors and fresh short-term windows. Returning to the same key restores that persona actor while it remains alive.

- [x] **Step 5: Scope projections**

Add `persona_id` to `ProjectionSnapshot`; change `StateProjector.rebuild(persona_id, group_id, now=now, policy=behavior.conversation)` to call only persona-scoped ledger, topic, continuation, bot-delivery and spontaneous-send methods. Remove recent decorative media projection if it survived Plan 1.

- [x] **Step 6: Verify GREEN**

Run: `./.venv/bin/python -m pytest tests/test_direct_pressure.py tests/test_runtime.py tests/test_phase2_projections.py tests/test_persona_scope_store.py -q`

Expected: all selected tests pass.

- [x] **Step 7: Commit**

```bash
git add groupmate/memory/store.py groupmate/engine/direct_pressure.py groupmate/engine/runtime.py groupmate/core/projections.py tests/test_direct_pressure.py tests/test_runtime.py tests/test_phase2_projections.py tests/test_persona_scope_store.py
git commit -m "refactor: isolate conversation state by persona"
```

### Task 6: Thread Persona Through Workflow, Delivery, Bridge, And Status（贯穿工作流、投递、宿主与状态）

**Files:**
- Modify: `groupmate/engine/workflow.py`
- Modify: `groupmate/engine/delivery.py`
- Modify: `groupmate/host/bridge.py`
- Modify: `groupmate/host/web_api.py`
- Modify: `main.py`
- Modify: `tests/test_workflow.py`
- Modify: `tests/test_delivery_service.py`
- Modify: `tests/test_plugin_loading.py`

- [x] **Step 1: Add end-to-end persona forwarding tests**

```python
async def test_workflow_reads_only_current_persona_memory(workflows, store, topic):
    store.add_memory("aemeath", memory_item(text="只属于爱弥斯"))
    await workflows["future"].evaluate(topic, TriggerKind.ALIAS_DIRECT, BehaviorPolicy())
    assert "只属于爱弥斯" not in workflows["future"].generation_model.last_prompt


async def test_delivery_finalization_stamps_current_persona(delivery, store, plan):
    await delivery.send(plan)
    assert store.outbox_record("aemeath", plan.decision_id)["status"] == "sent"
    assert store.outbox_record("future", plan.decision_id) is None


def test_status_reports_schema_and_active_persona(bridge):
    status = bridge.status()
    assert status["active_persona"] == "aemeath"
    assert status["database_schema"] == 11
```

- [x] **Step 2: Verify RED**

Run: `./.venv/bin/python -m pytest tests/test_workflow.py tests/test_delivery_service.py tests/test_plugin_loading.py -q`

Expected: failures identify unscoped store calls.

- [x] **Step 3: Make workflow ownership explicit**

Store `persona_context` on `CognitiveWorkflow`. Pass `persona_context.persona_id` to memory search, relationship lookup, social writes, memory writer, decision records and session projection. Replace any `try/except TypeError` compatibility calls around store APIs with one exact v11 signature.

- [x] **Step 4: Scope delivery operations**

`DeliveryService` receives `persona_id` at construction and passes it through enqueue, transition, finalize and record methods. Bot delivery messages remain ordinary `ChatMessage` values; the store operation supplies their ownership.

```python
DeliveryService(
    persona_id=persona_context.persona_id,
    platform=platform,
    memory=memory,
    clock=clock,
    character_name=persona_context.display_name,
)
```

- [x] **Step 5: Scope bridge bootstrap and shutdown**

Bridge calls `runtime.actor_for(group_id, self.persona_context)`, `StateProjector.rebuild(self.persona_context.persona_id, group_id, now=now, policy=self.behavior.conversation)`, persona-scoped status queries, and explicit all-persona recovery only for process-level unknown sends. No bridge or store call may inject `"aemeath"` directly; only the registry supplies the current ID.

- [x] **Step 6: Verify GREEN**

Run: `./.venv/bin/python -m pytest tests/test_workflow.py tests/test_delivery_service.py tests/test_plugin_loading.py tests/test_phase2_projections.py -q`

Expected: all selected tests pass.

- [x] **Step 7: Commit**

```bash
git add groupmate/engine/workflow.py groupmate/engine/delivery.py groupmate/host/bridge.py groupmate/host/web_api.py main.py tests/test_workflow.py tests/test_delivery_service.py tests/test_plugin_loading.py tests/test_phase2_projections.py
git commit -m "refactor: thread persona scope through runtime"
```

### Task 7: Evaluation, Residual Audit, And Full Verification（评估、残留审计与完整验证）

**Files:**
- Modify: `eval/runner.py`
- Modify: `eval/shadow_projector.py`
- Modify: `eval/shadow_export.py`
- Modify: `tests/test_eval_runner.py`
- Modify: `tests/test_shadow_projector.py`
- Modify: `tests/test_shadow_export.py`
- Modify: `README.md`

- [x] **Step 1: Add explicit-persona evaluation tests**

```python
def test_shadow_projector_requires_persona_context():
    with pytest.raises(TypeError):
        ShadowProjector(behavior=BehaviorPolicy())


def test_shadow_projection_state_is_scoped_to_aemeath(projector):
    assert projector.persona_context.persona_id == "aemeath"
    assert projector.persona_context.aliases == ("爱弥斯",)
```

- [x] **Step 2: Verify RED**

Run: `./.venv/bin/python -m pytest tests/test_eval_runner.py tests/test_shadow_projector.py tests/test_shadow_export.py -q`

Expected: evaluation adapters still call old unscoped store/runtime signatures.

- [x] **Step 3: Update evaluation adapters**

Every deterministic and shadow run resolves an explicit Aemeath context and creates isolated temporary v11 storage. Pass the context to workflow/runtime/projector constructors. The exported report may include public `persona_id="aemeath"`, but must not expose relationship details or raw QQ IDs.

- [x] **Step 4: Add a state-API residual scan**

Create a test that inspects `SQLiteMemoryStore` signatures and fails if any state method lacks `persona_id`:

```python
STATE_METHODS = (
    "save_message", "recent_messages", "upsert_profile", "get_profile",
    "add_memory", "search_memories", "append_memory_candidate",
    "get_relationship_state", "record_social_interaction",
    "latest_open_topic_epoch", "grant_continuation",
    "record_transition", "enqueue_outbox", "outbox_record",
)

for name in STATE_METHODS:
    assert "persona_id" in signature(getattr(SQLiteMemoryStore, name)).parameters
```

Also search SQL strings in `store.py` and verify every query against a persona table contains `persona_id`, except the explicitly named all-persona startup/shutdown recovery method and schema inspection.

- [x] **Step 5: Run focused persona verification**

Run:

```bash
./.venv/bin/python -m pytest tests/test_persona_scope_migrations.py tests/test_persona_scope_store.py tests/test_memory.py tests/test_memory_writer.py tests/test_social_events.py tests/test_direct_pressure.py tests/test_runtime.py tests/test_phase2_projections.py tests/test_delivery_service.py -q
```

Expected: all persona migration and isolation tests pass.

- [x] **Step 6: Run full verification**

Run:

```bash
./.venv/bin/python -m pytest -q
./.venv/bin/python -m eval.runner --mode deterministic --enforce
./.venv/bin/python -m eval.runner --mode deterministic --enforce --scenarios eval/scenarios/phase2_behavior.jsonl --output /tmp/groupmate-phase2-behavior-v11.json
git diff --check
```

Expected: full pytest passes, both deterministic evaluations report every run passed, and `git diff --check` emits no output.

- [x] **Step 7: Run the real export shadow regression**

Run:

```bash
./.venv/bin/python -m eval.shadow_export --export-dir /export --target-uin 323537051 --target-alias 小维 --current-alias 爱弥斯 --id-salt-file eval/results/.shadow-id-salt --output eval/results/config-v11-shadow.json --markdown-output eval/results/config-v11-shadow.md --review-output eval/results/config-v11-review.jsonl
```

Expected: command exits 0, safety violations remain 0, copied-text @ remains excluded, and the reply/silence confusion matrix does not regress from the last accepted shadow baseline without a documented scene-level reason.

- [x] **Step 8: Update README and commit**

Document schema v11 backup behavior, existing-data ownership by `aemeath`, future-persona isolation, and recovery location using `identifier（中文说明）` wording.

```bash
git add eval/runner.py eval/shadow_projector.py eval/shadow_export.py tests/test_eval_runner.py tests/test_shadow_projector.py tests/test_shadow_export.py tests/test_persona_scope_store.py README.md
git commit -m "test: verify persona-scoped state isolation"
```
