# Social Runtime v2 Phase A：Clean-slate 基础运行时实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 清除旧 Groupmate 架构，交付从空白数据库启动、可持久、可回放、可恢复且 Shadow 零副作用的 Social Runtime v2 骨架。

**Architecture:** 新插件只装配 Social Runtime v2。平台刺激先写 Durable Inbox/Journal，再由单一 PersonaSupervisor 和每群单一 GroupSceneActor 串行提交状态；旧 Workflow、Runtime、Store、配置、页面和测试均不进入新依赖图。

**Tech Stack:** Python、`asyncio`、`sqlite3`、不可变 dataclass、pytest、AstrBot `>=4.24,<5`。

**Spec:** `docs/superpowers/specs/2026-08-18-groupmate-social-runtime-v2-design.md`

## Global Constraints

- 遵守总路线图全部 Global Constraints。
- 本阶段只允许 `OFF` 与 `SHADOW`；`SOCIAL_RUNTIME` 必须在 Gate C 前拒绝启动。
- 数据库固定为新文件 `groupmate-social-runtime-v2.db`，不得打开旧 `groupmate.db`。
- V2 表从 Schema v1 开始，不继承旧 Schema v21 编号或迁移函数。
- Actor 只有在 effect 与 Cursor 同事务提交后才确认 Inbox 事件。
- `groupmate/social_runtime/` 不得导入清理前的任何旧领域模块。

---

### Task 1: 创建 Worktree 并清除旧架构

**Files:**
- Delete: `groupmate/`, `tests/`, `eval/`, `pages/settings/`, `docs/analysis/`
- Delete: 除本规格和本组计划外的旧 `docs/superpowers/specs/*.md`、`docs/superpowers/plans/*.md`
- Replace: `main.py`, `_conf_schema.json`
- Create: `pytest.ini`, `groupmate/__init__.py`, `groupmate/social_runtime/__init__.py`
- Create: `tests/__init__.py`, `tests/shared/test_architecture_boundaries.py`, `tests/shared/test_plugin_skeleton.py`

**Interfaces:**
- Consumes: 当前 Git 历史与权威 V2 规格。
- Produces: 无旧领域实现的最小插件树、架构守卫、全新测试目录。

- [x] **Step 1: 创建 Worktree 并记录删除范围**

Run:
```bash
git status --short
git worktree add .worktrees/social-runtime-v2 -b refactor/social-runtime-v2
git -C .worktrees/social-runtime-v2 ls-files groupmate tests eval pages/settings docs/analysis
```
Expected: 工作区干净；Git 历史保留全部旧内容。

- [x] **Step 2: 写架构边界测试并验证 RED**

```python
# tests/shared/test_architecture_boundaries.py
def test_composition_root_only_depends_on_v2_boundaries():
    imports = _internal_imports(ROOT / "main.py")
    assert imports
    assert all(
        module == "groupmate.settings"
        or module.startswith((
            "groupmate.adapters", "groupmate.settings", "groupmate.social_runtime"
        ))
        for module in imports
    ), imports
```

该测试通过 AST 解析真实 Import Boundary，不扫描旧类名，也不把文件布局本身当成行为。RED 结果明确列出 `groupmate.host*` 四个旧依赖。

- [x] **Step 3: 使用 `apply_patch` 删除旧实现并建最小入口**

保留 `metadata.yaml`、`requirements.txt`、根 `__init__.py`、权威规格和本组计划。`main.py` 暂时只提供可导入的 `GroupmatePlugin`，初始化时明确报告 `Social Runtime v2 foundation incomplete`。`_conf_schema.json` 只保留 `enabled_groups`、`runtime_mode`、`generation_provider`、`vision_provider`、`database_path`。

- [x] **Step 4: 配置 Marker 并验证清理**

```ini
[pytest]
markers =
    shared: 跨子系统安全与平台不变量
    social_runtime: Social Runtime v2 领域行为
    scenarios: 多消息社会场景
    contracts: Worker、Capability、Projection 与 Command 契约
    recovery: 崩溃、重复、过期、部分成功和未知结果
    evaluation: Shadow 与目标效果评估
    page: AstrBot 插件页面
```

Run: `pytest tests/shared -q -p no:cacheprovider`

Expected: `3 passed`，Composition Root 只导入 V2 Boundary，默认模式为 `OFF`，基础 Bridge 在完整运行时装配前 fail closed。

- [x] **Step 5: 提交**

```bash
git add -A
git commit -m "refactor: remove legacy groupmate architecture"
```

---

### Task 2: 定义事件、版本和运行模式契约

**Files:**
- Create: `groupmate/social_runtime/contracts.py`
- Create: `tests/social_runtime/test_contracts.py`, `tests/factories.py`

**Interfaces:**
- Consumes: 标准化平台事实。
- Produces: `RuntimeMode`, `SocialEventEnvelope`, `ActorCursor`, `PersonaSnapshot`, `GlobalStateEffect`。

- [x] **Step 1: 写失败测试**

```python
def test_event_freezes_payload():
    source = {"text": "早"}
    event = SocialEventEnvelope.create(
        event_id="evt-1", event_type="platform.message", occurred_at=100,
        received_at=101, persona_id="aemeath", group_id="885617919",
        actor_id="323537051", source_message_id="m1",
        correlation_id="corr-1", causation_id=None, payload=source,
    )
    source["text"] = "被修改"
    assert event.payload["text"] == "早"
    assert tuple(RuntimeMode) == (
        RuntimeMode.OFF, RuntimeMode.SHADOW, RuntimeMode.SOCIAL_RUNTIME,
    )
```

- [x] **Step 2: 运行失败测试**

Run: `pytest tests/social_runtime/test_contracts.py -q`
Expected: FAIL with `ModuleNotFoundError`。

- [x] **Step 3: 实现不可变契约**

```python
class RuntimeMode(str, Enum):
    OFF = "OFF"
    SHADOW = "SHADOW"
    SOCIAL_RUNTIME = "SOCIAL_RUNTIME"

@dataclass(frozen=True)
class SocialEventEnvelope:
    event_id: str
    event_type: str
    occurred_at: int
    received_at: int
    persona_id: str
    group_id: Optional[str]
    actor_id: Optional[str]
    source_message_id: Optional[str]
    correlation_id: str
    causation_id: Optional[str]
    payload: Mapping[str, object]

    @classmethod
    def create(cls, **values):
        for key in ("event_id", "event_type", "persona_id", "correlation_id"):
            if not str(values.get(key) or "").strip():
                raise ValueError("{} must not be empty".format(key))
        values["payload"] = MappingProxyType(dict(values.get("payload") or {}))
        return cls(**values)
```

同文件定义上述其余三个 dataclass；`GlobalStateEffect` 字段固定为 `effect_id/source_event_id/expected_version/kind/amount/evidence_event_ids`。

- [x] **Step 4: 验证校验和 JSON 往返**

补充空 ID、负时间戳、未知模式和不可 JSON payload 测试；实现 `to_dict()`/`from_dict()`，保留 correlation/causation。

- [x] **Step 5: 运行并提交**

Run: `pytest tests/social_runtime/test_contracts.py -q`
```bash
git add groupmate/social_runtime/contracts.py tests/social_runtime/test_contracts.py tests/factories.py
git commit -m "feat: define social runtime contracts"
```

---

### Task 3: 创建独立 Schema v1

**Files:**
- Create: `groupmate/social_runtime/persistence/__init__.py`, `groupmate/social_runtime/persistence/schema.py`
- Create: `tests/social_runtime/test_schema.py`

**Interfaces:**
- Consumes: 新数据库路径。
- Produces: `SCHEMA_VERSION = 1`, `initialize_database(path)`, `verify_schema(db)`。

- [ ] **Step 1: 写空白数据库测试**

```python
REQUIRED = {
    "inbox", "journal", "actor_cursors", "snapshots", "persona_state",
    "group_world", "attention_frames", "cognitive_observations",
    "candidate_intentions", "governor_results", "action_plans", "tasks",
    "task_events", "delivery_bundles", "outbox", "relationship_events",
    "relationship_projection", "impressions", "culture", "memories",
    "memory_tombstones", "config_versions", "governance_actions",
    "projection_cursors", "evaluation_labels",
}
def test_bootstrap_complete_schema(tmp_path):
    path = tmp_path / "groupmate-social-runtime-v2.db"
    initialize_database(path)
    with sqlite3.connect(str(path)) as db:
        names = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert REQUIRED <= names
```

- [ ] **Step 2: 运行失败测试**

Run: `pytest tests/social_runtime/test_schema.py -q`

- [ ] **Step 3: 实现表和约束**

```sql
CREATE TABLE inbox (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
 persona_id TEXT NOT NULL, group_id TEXT, envelope_json TEXT NOT NULL,
 received_at INTEGER NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending','processing','committed','failed')),
 attempt INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE actor_cursors (
 actor_key TEXT PRIMARY KEY, last_sequence INTEGER NOT NULL, version INTEGER NOT NULL
);
CREATE TABLE outbox (
 part_id TEXT PRIMARY KEY, bundle_id TEXT NOT NULL, persona_id TEXT NOT NULL,
 group_id TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,
 status TEXT NOT NULL CHECK(status IN
 ('planned','ready','sending','sent','failed','unknown','expired','suppressed')),
 payload_json TEXT NOT NULL, expires_at INTEGER NOT NULL, receipt_json TEXT
);
```

其余 REQUIRED 表也在首次初始化一次创建；群级唯一键必须包含 `persona_id, group_id`。开启 WAL、foreign_keys、5000ms busy timeout。

- [ ] **Step 4: 拒绝旧数据库**

basename 为 `groupmate.db` 或已有库缺少 `social_runtime_schema` 元数据时抛 `ForeignDatabaseError`，不升级、不读取。

- [ ] **Step 5: 运行并提交**

Run: `pytest tests/social_runtime/test_schema.py -q`
```bash
git add groupmate/social_runtime/persistence tests/social_runtime/test_schema.py
git commit -m "feat: bootstrap clean social runtime database"
```

---

### Task 4: 实现 Durable Event Store

**Files:**
- Create: `groupmate/social_runtime/persistence/event_store.py`
- Create: `tests/social_runtime/test_event_store.py`, `tests/recovery/test_event_atomicity.py`

**Interfaces:**
- Consumes: `SocialEventEnvelope`, Schema v1。
- Produces: `SQLiteSocialEventStore.append/claim/commit/fail/journal/save_snapshot/load_snapshot`。

- [ ] **Step 1: 写幂等和原子性测试**

```python
def test_duplicate_event_has_one_sequence(event_store, event_factory):
    event = event_factory(event_id="evt-1")
    first, second = event_store.append(event), event_store.append(event)
    assert (first.inserted, second.inserted) == (True, False)
    assert first.sequence == second.sequence

def test_effect_and_cursor_commit_together(event_store, event_factory):
    event_store.append(event_factory(event_id="evt-1"))
    claimed = event_store.claim("persona:aemeath", 0, 1)[0]
    cursor = event_store.commit("persona:aemeath", claimed, (
        {"effect_id": "fx-1", "kind": "persona.created"},
    ))
    assert cursor.last_sequence == claimed.sequence
    assert len(event_store.journal("corr-1")) == 1
```

- [ ] **Step 2: 运行失败测试**

Run: `pytest tests/social_runtime/test_event_store.py tests/recovery/test_event_atomicity.py -q`

- [ ] **Step 3: 实现公开 API**

实现 `SQLiteSocialEventStore` 的精确公开方法：`append(event) -> AppendResult`、`claim(actor_key, after_sequence, limit) -> tuple[ClaimedEvent, ...]`、`commit(actor_key, claimed, effects) -> ActorCursor`、`fail(actor_key, sequence, code) -> None`、`cursor(actor_key) -> ActorCursor`、`journal(correlation_id) -> tuple[JournalEffect, ...]`、`save_snapshot(actor_key, version, payload) -> None`、`load_snapshot(actor_key) -> StoredSnapshot | None`。

- [ ] **Step 4: 注入崩溃**

在 Journal insert 后抛异常；断言 Cursor 未前进、Journal 无 effect、Inbox 可再次 claim。重复 `commit()` 不重复 effect。

- [ ] **Step 5: 运行并提交**

Run: `pytest tests/social_runtime/test_event_store.py tests/recovery/test_event_atomicity.py -q`
```bash
git add groupmate/social_runtime/persistence/event_store.py tests/social_runtime/test_event_store.py tests/recovery/test_event_atomicity.py
git commit -m "feat: persist social events atomically"
```

---

### Task 5: 实现 PersonaSupervisor

**Files:**
- Create: `groupmate/social_runtime/persistence/repositories.py`, `groupmate/social_runtime/supervisor.py`
- Create: `tests/social_runtime/test_persona_supervisor.py`, `tests/recovery/test_supervisor_recovery.py`

**Interfaces:**
- Consumes: `GlobalStateEffect`, Event Store。
- Produces: `PersonaSupervisor.start/snapshot/apply_effect/close`。

- [ ] **Step 1: 写版本与去重测试**

```python
async def test_effect_applies_once(supervisor):
    before = await supervisor.snapshot(config_version=3)
    effect = GlobalStateEffect("fx-1", "evt-1", before.state_version,
                               "energy_delta", -5, ("evt-1",))
    after = await supervisor.apply_effect(effect)
    assert (await supervisor.apply_effect(effect)) == after
    assert after.energy == 95
```

- [ ] **Step 2: 运行失败测试**

Run: `pytest tests/social_runtime/test_persona_supervisor.py -q`

- [ ] **Step 3: 实现 mailbox 单写者**

实现 `PersonaSupervisor.start() -> None`、`snapshot(config_version) -> PersonaSnapshot`、`apply_effect(effect) -> PersonaSnapshot` 和 `close() -> None` 四个异步方法。公开调用统一进入 mailbox，Repository 只在 Actor loop 内写入。

energy/cognitive_load 限幅 `0..100`，valence/arousal/irritation 限幅 `-100..100`；过期 expected_version 抛 `StateVersionConflict`。

- [ ] **Step 4: 验证并发恢复**

并发提交 20 个 Effect，版本严格递增且无 lost update；关闭重建后 Snapshot hash 完全一致。

- [ ] **Step 5: 运行并提交**

Run: `pytest tests/social_runtime/test_persona_supervisor.py tests/recovery/test_supervisor_recovery.py -q`
```bash
git add groupmate/social_runtime/persistence/repositories.py groupmate/social_runtime/supervisor.py tests/social_runtime/test_persona_supervisor.py tests/recovery/test_supervisor_recovery.py
git commit -m "feat: add persona supervisor single writer"
```

---

### Task 6: 实现 GroupWorldState 与 GroupSceneActor

**Files:**
- Create: `groupmate/social_runtime/world.py`, `groupmate/social_runtime/scene_actor.py`
- Create: `tests/social_runtime/test_group_world.py`, `tests/social_runtime/test_scene_actor.py`
- Create: `tests/recovery/test_scene_actor_recovery.py`

**Interfaces:**
- Consumes: Event、Persona Snapshot、Event Store。
- Produces: `GroupWorldState`, `SceneWorkRequest`, `GroupSceneActor.submit/accept_result`。

- [ ] **Step 1: 写多话题和过期测试**

```python
def test_reply_chain_keeps_parallel_topics(world_projector, event_factory):
    state = world_projector.empty("g1")
    for event in (
        event_factory("m1", text="项目怎么样", sender="u1"),
        event_factory("m2", text="今晚吃啥", sender="u2"),
        event_factory("m3", text="做到一半", sender="u3", reply_to="m1"),
    ):
        state = world_projector.apply(state, event)
    assert len(state.active_topics) == 2
    assert state.topic_for_message("m3").root_event_id == "m1"
```

- [ ] **Step 2: 运行失败测试**

Run: `pytest tests/social_runtime/test_group_world.py tests/social_runtime/test_scene_actor.py -q`

- [ ] **Step 3: 实现不可变世界与 Actor**

`GroupWorldState` 字段固定为 spec 第 8 节；`GroupSceneActor` 提供 `submit()`, `drain()`, `accept_result()`, `snapshot()`。明确 reply/@ 事实优先于模型话题建议；外部工作不阻塞 mailbox。

- [ ] **Step 4: 实现 Snapshot 恢复**

每 100 个事件或关闭时保存 Snapshot，从 Cursor 下一 sequence 回放；过期 scene_version 的外部结果返回 `False` 且不改变版本。

- [ ] **Step 5: 运行并提交**

Run: `pytest tests/social_runtime/test_group_world.py tests/social_runtime/test_scene_actor.py tests/recovery/test_scene_actor_recovery.py -q`
```bash
git add groupmate/social_runtime/world.py groupmate/social_runtime/scene_actor.py tests/social_runtime tests/recovery/test_scene_actor_recovery.py
git commit -m "feat: add recoverable group scene actors"
```

---

### Task 7: 接入 Event Fabric 与 AstrBot

**Files:**
- Create: `groupmate/social_runtime/event_fabric.py`, `groupmate/social_runtime/manager.py`
- Create: `groupmate/adapters/astrbot_events.py`, `groupmate/adapters/astrbot_bridge.py`
- Create: `groupmate/settings.py`
- Replace: `main.py`, `_conf_schema.json`
- Create: `tests/contracts/test_astrbot_events.py`, `tests/social_runtime/test_event_fabric.py`
- Create: `tests/shared/test_shadow_side_effects.py`

**Interfaces:**
- Consumes: AstrBot 原始事件和 Phase A 核心。
- Produces: `AstrBotEventTranslator.translate`, `SocialRuntimeManager.ingest`, `AstrBotSocialRuntimeBridge.handle_event`。

- [ ] **Step 1: 写翻译与 Shadow 测试**

```python
async def test_shadow_persists_without_sending(harness):
    await harness.bridge.handle_event(group_message("m1", "大家早"))
    await harness.manager.drain()
    assert harness.store.event_ids() == ("qq:m1",)
    assert harness.execution.calls == ()
```

- [ ] **Step 2: 运行失败测试**

Run: `pytest tests/contracts/test_astrbot_events.py tests/shared/test_shadow_side_effects.py -q`

- [ ] **Step 3: 实现纯事实 Translator**

保留 reply、mentions、media、sender、group、timestamp 和平台 ID；不分类场景、不判断回复、不写记忆。缺失稳定 ID 时使用平台/群/发送者/时间/规范化 segment 的 SHA-256 指纹。

- [ ] **Step 4: 实现 Manager 与 Bridge**

```python
class SocialRuntimeManager:
    async def ingest(self, envelope):
        appended = self.event_store.append(envelope)
        if appended.inserted:
            await self.fabric.notify(envelope.persona_id, envelope.group_id)
        return appended
    async def drain(self):
        await asyncio.gather(*(actor.drain() for actor in self._actors.values()))

    async def close(self):
        await asyncio.gather(*(actor.close() for actor in self._actors.values()))
        self._actors.clear()
```

`OFF` 直接返回；`SHADOW` 注入 `NoSideEffectExecutionPort`。`main.py` 只构造新 Bridge。

- [ ] **Step 5: 验证并提交**

Run: `pytest tests/contracts/test_astrbot_events.py tests/social_runtime/test_event_fabric.py tests/shared/test_shadow_side_effects.py -q && python -m tests.architecture_guard`
```bash
git add groupmate main.py _conf_schema.json tests
git commit -m "feat: connect astrbot to social runtime shadow"
```

---

### Task 8: Gate A 恢复验收

**Files:**
- Create: `tests/recovery/test_phase_a_replay.py`
- Create: `docs/operations/social-runtime-v2-recovery.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: Phase A 全部接口。
- Produces: 崩溃恢复证据和新数据库手册。

- [ ] **Step 1: 注入 30 个事件，在第 17 个 effect 提交前崩溃**

重建 Manager 后断言 Journal、Supervisor version、GroupWorldState 和 Cursor 与无崩溃运行一致，Execution 调用始终为零。

- [ ] **Step 2: 运行恢复测试**

Run: `pytest tests/recovery/test_phase_a_replay.py -q`

- [ ] **Step 3: 写恢复手册**

包含停止事件、备份新 DB、检查 Inbox、dry replay、比较 Snapshot hash、恢复消费、验证 Outbox 为空；不涉及旧数据库。

- [ ] **Step 4: 执行 Gate A**

Run: `pytest -m 'shared or social_runtime or recovery or contracts' -q && python -m tests.architecture_guard && git diff --check`

- [ ] **Step 5: 提交并评审**

```bash
git add tests/recovery/test_phase_a_replay.py docs/operations/social-runtime-v2-recovery.md README.md
git commit -m "test: verify clean runtime replay recovery"
```

使用 `superpowers:requesting-code-review`，通过后执行 Phase B。
