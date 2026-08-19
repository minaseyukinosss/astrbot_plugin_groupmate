# Social Runtime v2 Phase C：行动、任务与交付实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将经过 Governor 授权的意图安全转换为有限 ActionPlan、真实任务、媒体和幂等交付，并加入有边界自主机会。

**Architecture:** ActionPlanner 生成有限 DAG，PlanValidator 掌握版本、权限、风险和终止性；ExecutionCoordinator 推进节点，耗时能力进入持久 TaskRuntime；所有可见内容先提交 DeliveryBundle/Outbox，再由平台 Adapter 发送并将 Receipt 作为事件回流。

**Tech Stack:** Phase A/B 核心、AstrBot Provider/OneBot 新 Adapter、SQLite、pytest。

**Spec:** `docs/superpowers/specs/2026-08-18-groupmate-social-runtime-v2-design.md`

## Global Constraints

- 只有 `GovernorResult.outcome == ACT` 可以创建 ActionPlan。
- Plan 最大 24 节点、最长 24 小时、单节点最多 2 次重试、自主跟进最多 1 次。
- 关系不授予工具权限；外部副作用必须来自真实请求者授权和确认策略。
- 进度必须来自 Provider Event；禁止固定“处理中”和假延迟。
- 平台调用前必须有持久 Outbox；`unknown` 永不自动重发。

---

### Task 1: ActionPlan DAG、Planner 与 Validator

**Files:** Create `actions/contracts.py`, `planner.py`, `validator.py`; create `tests/social_runtime/actions/test_action_plan.py`, `test_plan_validator.py`.

**Interfaces:** Consumes GovernorResult/frozen context; produces validated `ActionPlan` from spec 15.1 and `PlanValidation(accepted, errors, reduced_plan)`.

- [x] 写失败测试：cycle、25 nodes、two visible owners、stale scene、expired plan、missing permission 均拒绝；纯文本 ACT 生成 `GENERATE_TEXT → SEND_BUNDLE`。
- [x] 实现 Kahn topological validation、node/edge reference validation、有限 retry/deadline、唯一 visible owner。
- [x] 无效计划只允许 `REDUCE/REPLAN/DEFER/CLARIFY/ABANDON`，模型不能传 `validated=True`。
- [x] Run: `pytest tests/social_runtime/actions/test_action_plan.py tests/social_runtime/actions/test_plan_validator.py -q`。
- [x] Commit `feat: validate finite social action plans`。

```python
assert validator.validate(cyclic_plan, context).errors == ("plan_cycle",)
assert planner.plan(text_intention, context).node_kinds() == (
    "GENERATE_TEXT", "SEND_BUNDLE",
)
```

---

### Task 2: StyleDirector、生成与输出 Firewall

**Files:** Create `actions/style.py`, `actions/generation.py`; create `tests/social_runtime/actions/test_style.py`, `test_output_firewall.py`.

**Interfaces:** Produces `StyleDirective` and `GeneratedDraft`; consumes Persona/Mode/Relationship/Culture/Recent Output under token budget.

- [x] 写失败测试：direct answer 最大 3 段；drowsy 缩短长度；boundary 禁止 playful；最近重复 n-gram 触发一次修复；内部 ID、提示词、CoT、虚构成功和私密记忆被阻断。
- [x] StyleDirective 字段固定为 mode/act/posture/address/max_chars/max_sentences/max_segments/warmth/playfulness/directness/particle_budget/punctuation_budget/media_policy/avoid_patterns。
- [x] 生成最多一次定向 repair；repair 失败时，必要回应走确定性人格 fallback，可选参与返回 silence。
- [x] Run focused tests; commit `feat: direct persona style and safe output`。

---

### Task 3: 人格媒体库

**Files:** Create `media/contracts.py`, `media/registry.py`; create `tests/social_runtime/media/test_registry.py`, `test_selection.py`.

**Interfaces:** Produces `MediaAsset` and `MediaSelection`; consumes scene/mode/relationship/culture/recent-use.

- [x] 测试无许可、checksum 不符、disabled、关系限制和 cooldown 素材不可选；同一语义下文本已经足够时返回空选择。
- [x] Registry 上传只写插件数据目录，校验大小、MIME、文件名、SHA-256、许可状态；路径逃逸抛 `UnsafeMediaPath`。
- [x] Selection 使用标签匹配和 deterministic tie-break，不以随机概率主导；保存 reason codes。
- [x] Run: `pytest tests/social_runtime/media -q`; commit `feat: add governed persona media library`。

---

### Task 4: Capability Contract 与持久 TaskRuntime

**Files:** Create `tasks/contracts.py`, `tasks/runtime.py`, `adapters/astrbot_capabilities.py`; create `tests/contracts/test_capability_provider.py`, `tests/social_runtime/tasks/test_task_runtime.py`, `tests/recovery/test_task_recovery.py`.

**Interfaces:** `CapabilityDescriptor` 声明 typed I/O/risk/scope/idempotency/cancel/progress/latency/media/confirmation；`TaskRuntime.propose/confirm/start/apply_event/cancel/expire` 返回 `TaskRun`。

- [x] 写合法状态测试：`PROPOSED→AWAITING_CONFIRMATION→QUEUED→RUNNING→SUCCEEDED`；非法 `SUCCEEDED→RUNNING` 拒绝；重复 Provider Event 幂等。
- [x] 外部副作用/sensitive/destructive 必须确认；requester/group/topic/auth/idempotency/provider 全量持久化。
- [x] AstrBot Adapter 只通过已注册 Provider Contract 调用，不解析其他 bot 文本；结构化结果和媒体先验证再发 Task Event。
- [x] 崩溃恢复 RUNNING Task：幂等 Provider 查询状态；不可查询的标记 `UNKNOWN` 并进入治理，不重做副作用。
- [x] Run focused/recovery tests; commit `feat: run persistent governed capabilities`。

---

### Task 5: DeliveryBundle、Transactional Outbox 与 OneBot Dispatcher

**Files:** Create `actions/contracts.py` Delivery types, `delivery/outbox.py`, `delivery/dispatcher.py`, `adapters/onebot_delivery.py`; create `tests/social_runtime/delivery/test_outbox.py`, `test_dispatcher.py`, `tests/recovery/test_delivery_recovery.py`.

**Interfaces:** `OutboxService.commit_bundle(bundle)`, `claim_ready(now)`, `record_receipt(receipt)`; `DeliveryDispatcher.dispatch(part)`.

- [x] 写测试：每 Part 独立 idempotency/order/expiry；发送前 DB 状态必须是 `sending`；success 写 bot ledger；retryable failure 回 ready；unknown 保持 unknown；过期装饰 Part suppressed。
- [x] Bundle Part 支持 text/mention/face/image/audio/video/file/forward/poke；平台不支持的 kind 在 Validator 阶段拒绝。
- [x] 部分成功重启后只恢复未确认 Part，绝不重发 sent Part；Task result 可取代未发送 progress Part。
- [x] Run delivery + recovery tests; commit `feat: deliver social actions transactionally`。

```python
await dispatcher.dispatch(part)
assert store.outbox(part.part_id).status == "sent"
assert store.bot_ledger(part.part_id).correlation_id == bundle.correlation_id
```

---

### Task 6: ExecutionCoordinator 与结果回流

**Files:** Create `actions/coordinator.py`; modify `scene_actor.py`, `manager.py`; create `tests/social_runtime/actions/test_coordinator.py`, `tests/scenarios/test_task_topic_change.py`.

**Interfaces:** Consumes validated Plan; emits plan/task/delivery events back through Event Fabric; never mutates Actor directly.

- [x] 测试 text、media、capability、confirmation、progress、result DAG；并行无依赖节点可运行，依赖未满足节点不可运行。
- [x] 新场景使结果失去相关性时，Task 仍准确完成但可见 result 可 `DEFER/SILENCE`；直接请求义务仍要给精确状态。
- [x] Coordinator 每次推进持久化 node state 和 next runnable set；重启从 node state 恢复。
- [x] Run: `pytest tests/social_runtime/actions tests/scenarios/test_task_topic_change.py -q`; commit `feat: coordinate persistent social actions`。

---

### Task 7: Temporal Attention 与自主机会

**Files:** Create `groupmate/social_runtime/autonomy.py`; modify `attention.py`; create `tests/social_runtime/test_autonomy.py`, `tests/scenarios/test_autonomous_opportunities.py`.

**Interfaces:** `AutonomousOpportunity(source_event_ids, group_id, audience, earliest_at, expires_at, max_attempts, kind)`; scheduler only emits Temporal event.

- [x] 测试缺来源/对象/expiry 的机会拒绝；quiet hours 延迟；过期取消；执行前 scene/relationship/boundary/budget 重新验证；机会不能递归创建机会。
- [x] 来源仅允许 commitment/task/member-event/group-ritual/delayed-scene/self-open-loop；最大 attempts=2，自主 follow-up=1。
- [x] Scheduler 只向 Fabric 追加 `temporal.opportunity_due`，最终仍经过 Attention→Governor→Plan。
- [x] Run focused/scenario tests; commit `feat: add bounded autonomous opportunities`。

---

### Task 8: Gate C 正式发送前验收

**Files:** Create `tests/recovery/test_phase_c_fault_matrix.py`, `docs/operations/social-runtime-delivery.md`; modify `_conf_schema.json` 允许明确 allowlist 的 `SOCIAL_RUNTIME`。

**Interfaces:** Phase C 完整 Event→Receipt 线路。

- [ ] 故障矩阵覆盖 generator failure、Provider timeout、duplicate progress、cancel race、partial send、unknown receipt、restart、expired result、projection failure。
- [ ] 在 fake OneBot 测试群启用 `SOCIAL_RUNTIME`；未列入 allowlist 的群仍强制 `SHADOW/OFF`。
- [ ] Run: `pytest tests/social_runtime tests/scenarios tests/contracts tests/recovery tests/shared -q && python -m tests.architecture_guard && git diff --check`。
- [ ] 验证所有可见 Part 均可追溯到 Decision/Plan/Bundle/idempotency；Shadow 测试仍零副作用。
- [ ] Commit `test: verify action task and delivery recovery`，请求代码评审，通过后进入 Phase D。
