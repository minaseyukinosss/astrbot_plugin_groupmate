# Social Runtime v2 Phase B：认知、人格与社会状态实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Shadow 模式交付多话题注意力、受约束认知、候选意图、确定性 Social Governor，以及全新人格、关系、文化和记忆体系。

**Architecture:** GroupSceneActor 从冻结的 Scene/Persona/Config 生成 AttentionFrame；无状态 Worker 只向单周期 Blackboard 提交观察；IntentionEngine 提出候选，SocialGovernor 以硬约束和版本化效用返回 `ACT/DEFER/OBSERVE/SILENCE`。所有长期社会状态由证据事件 Projection 得出。

**Tech Stack:** Phase A 核心、AstrBot Model Adapter、不可变 dataclass、SQLite repository、pytest。

**Spec:** `docs/superpowers/specs/2026-08-18-groupmate-social-runtime-v2-design.md`

## Global Constraints

- 模型输出永远不能直接调用 Repository、Execution Port 或 Actor mutation。
- 每个 Observation 必须携带 evidence IDs、scene version、confidence、expiry 和 uncertainty。
- 平台 reply/@ 事实高于 Worker 推断；过期 Observation 不进入意图。
- 关系、印象、文化、记忆的唯一作用域为 `(persona_id, group_id, subject)`。
- 本阶段保持 Shadow，Governor 的 `ACT` 只写评估记录，不产生 ActionPlan 或发送。

---

### Task 1: 三路 Attention Scheduler

**Files:** Create `groupmate/social_runtime/attention.py`; create `tests/social_runtime/test_attention.py`, `tests/scenarios/test_attention_windows.py`.

**Interfaces:** Consumes `GroupWorldState`, `PersonaSnapshot`, `SocialEventEnvelope`; produces `AttentionFrame(frame_id, group_id, scene_version, trigger_kind, focus_topic_ids, focus_event_ids, candidate_audiences, urgency, deadline, requested_workers)`.

- [x] 写失败测试：直接 @ 立即产生 `FAST` Frame；普通连续消息在 2–4 秒动态窗口合并；已过期承诺只产生 `TEMPORAL` 候选而不授权行动。
- [x] Run: `pytest tests/social_runtime/test_attention.py tests/scenarios/test_attention_windows.py -q`；Expected: import failure。
- [x] 实现 `AttentionScheduler.on_event(event, world, persona, now)` 和 `flush_due(now)`。窗口按群速率选择 1–2/2–4/3–6 秒，direct/boundary/capability result 的 deadline 不等待 Ambient。
- [x] 增加场景版本变化测试：Frame 尚未派发时更新到新版本；已派发结果由下游按原版本拒绝。
- [x] Run: `pytest tests/social_runtime/test_attention.py tests/scenarios/test_attention_windows.py -q`; commit `feat: add three-lane attention scheduler`。

关键断言：
```python
frame = scheduler.on_event(direct_event, world, persona, now=100)[0]
assert frame.trigger_kind == "FAST"
assert frame.scene_version == world.scene_version
assert frame.candidate_audiences == ("u1",)
```

---

### Task 2: Worker Contract、Blackboard 与成本分级

**Files:** Create `groupmate/social_runtime/cognition/contracts.py`, `blackboard.py`, `service.py`, `astrbot_workers.py`; create `tests/contracts/test_cognitive_worker.py`, `tests/social_runtime/test_blackboard.py`.

**Interfaces:** Consumes `AttentionFrame` and bounded `CognitiveContext`; produces `CognitiveObservation`; `CognitionService.evaluate(frame, context)` returns immutable `BlackboardSnapshot`.

- [x] 写失败测试：证据为空、confidence 超出 `0..1`、scene version 不符或过期的 Observation 被拒绝；冲突命题同时保留且标记 conflict。
- [x] Run: `pytest tests/contracts/test_cognitive_worker.py tests/social_runtime/test_blackboard.py -q`。
- [x] 实现 `CognitiveWorker` Protocol：`async observe(frame, context) -> tuple[CognitiveObservation, ...]`。Model Adapter 只接受 JSON Schema，解析失败返回带错误码的空结果，不返回自由文本权威值。
- [x] 实现 Level 0 规则、Level 1 单 Worker、Level 2 多 Worker、Level 3 critic；预算超限时降级为 `OBSERVE`，不能跳过硬安全。
- [x] Run focused tests; commit `feat: add governed cognition blackboard`。

```python
@dataclass(frozen=True)
class CognitiveObservation:
    worker: str
    kind: str
    proposition: Mapping[str, object]
    confidence: float
    evidence_event_ids: tuple[str, ...]
    scene_version: int
    expires_at: int
    uncertainty: tuple[str, ...]
```

---

### Task 3: Persona Goals、CandidateIntentions 与 SocialGovernor

**Files:** Create `groupmate/social_runtime/intentions.py`, `governor.py`; create `tests/social_runtime/test_intentions.py`, `test_governor.py`; create `tests/scenarios/test_parallel_topic_governance.py`.

**Interfaces:** Consumes Blackboard, frozen Scene/Persona/Config; produces `CandidateIntention` and `GovernorResult` exactly matching spec sections 11–12.

- [x] 写失败测试：privacy/boundary/expired/wrong-target 硬阻断必须压倒 `999` 效用；兼容 `CARE + HELP` 可组合；不同对象不合并；低价值开放参与返回 `SILENCE`。
- [x] Run: `pytest tests/social_runtime/test_intentions.py tests/social_runtime/test_governor.py -q`。
- [x] 实现固定目标目录与显式特征计算。效用公式只排序通过硬门控的候选，不读取随机数。
- [x] 将每个拒绝项写入 `RejectedIntention(intention_id, reason_codes)`；`DEFER` 必须有 `reconsider_at`，`ACT` 必须有 selected ID，`SILENCE` 不得带 selected ID。
- [x] Run focused + scenario tests; commit `feat: govern social intentions deterministically`。

```python
result = governor.decide(candidates, context)
assert result.outcome == "SILENCE"
assert "boundary_active" in result.reason_codes
assert result.selected_intention_ids == ()
```

---

### Task 4: Constitution、GlobalSelfState 与 Mode Director

**Files:** Create `groupmate/social_runtime/persona/constitution.py`, `self_state.py`, `modes.py`; create `tests/social_runtime/test_constitution.py`, `test_self_state.py`, `test_modes.py`.

**Interfaces:** Produces immutable `ConstitutionVersion`, `GlobalSelfState`, `PersonaModeState`; Supervisor is the only caller that commits `StateEffect`.

- [x] 写失败测试：模型不能发布 Constitution；单一表情不能形成长期状态；无回复不降低关系/心境；focused task + drowsy 合法，boundary + playful 非法。
- [x] 实现管理员签名的 Constitution publish，字段包含 identity/values/boundaries/preferences/expression/safety/autonomy；hash 相同的版本幂等。
- [x] 实现代码所有的 effect clamp、cooldown、decay、causal dedupe；Mode 只由 event/time/load/admin command 转换。
- [x] 验证重启后状态和 mode 原因链一致。
- [x] Run: `pytest tests/social_runtime/test_constitution.py tests/social_runtime/test_self_state.py tests/social_runtime/test_modes.py -q`; commit `feat: add versioned persona kernel`。

---

### Task 5: 多维关系、印象与群文化

**Files:** Create `groupmate/social_runtime/society/relationships.py`, `impressions.py`, `culture.py`; extend `persistence/repositories.py`; create `tests/social_runtime/test_relationships.py`, `test_impressions.py`, `test_culture.py`, `tests/shared/test_group_scope_privacy.py`.

**Interfaces:** Consumes evidence events; produces versioned relationship projection, scoped impressions and culture artifacts.

- [x] 写失败测试：同一成员在 g1/g2 状态隔离；关系不授予 capability permission；单次梗不晋升文化；墓碑 impression 不自动重建。
- [x] 实现关系维度 `familiarity/warmth/trust/reciprocity/play_acceptance/reliability/care_permission/boundary_pressure`，每维 `-100..100`，只应用白名单 evidence kind。
- [x] 实现 Impression 的 evidence/status/expiry/use_scope；Culture 需要三次独立事件或管理员确认才从 candidate 晋升 active，并按 30 天无证据衰减。
- [x] Repository 查询必须要求 persona_id 和 group_id，缺一抛 `ScopeRequiredError`。
- [x] Run focused + privacy tests; commit `feat: model scoped social relationships and culture`。

---

### Task 6: 记忆写入、召回、墓碑与整合

**Files:** Create `groupmate/social_runtime/memory/pipeline.py`, `retrieval.py`, `consolidation.py`; create `tests/social_runtime/test_memory_pipeline.py`, `test_memory_retrieval.py`, `test_consolidation.py`, `tests/shared/test_memory_privacy.py`.

**Interfaces:** Produces `MemoryDecision(ACCEPT/REVIEW/REJECT)`, `MemoryContextBlock`, `ConsolidationReport`; consumes only real evidence events and scoped query.

- [ ] 写失败测试：bot 自己生成的回复不能证明用户事实；敏感候选默认 REVIEW；冲突事实并存且标记；删除后等价文本被 tombstone 阻断；召回不跨群。
- [x] 实现写入流水线：entity → scope/privacy → conflict → persistence/importance → authority → decision；摘要必须保存 evidence IDs。
- [x] 实现召回排序 `relevance + recency + confidence + diversity`，再做 sensitivity、conflict 和 token budget 裁剪；返回结构化块而非原始行。
- [x] Consolidation 合并重复 episode、衰减 impression/culture、关闭完成 loop，只产生 calibration candidate，不直接改安全/隐私/Constitution。
- [x] Run all memory tests; commit `feat: add governed social memory`。

---

### Task 7: Actor 认知集成与 Gate B

**Files:** Modify `groupmate/social_runtime/scene_actor.py`, `manager.py`; create `tests/scenarios/test_social_runtime_shadow.py`, `tests/recovery/test_stale_cognition.py`; create `docs/operations/social-runtime-shadow.md`.

**Interfaces:** Event → World → Attention → Cognition → Intention → GovernorResult → Shadow record.

- [ ] 写端到端场景：直接互动、说完再答、多话题、公开求助、接梗、关心、边界、正确沉默；固定 Worker outputs 以保证重放确定性。
- [ ] Actor 为每周期冻结 `scene_version/config_version/persona_state_version`；结果返回后任一版本不兼容则丢弃或重新排队，不得直接 ACT。
- [ ] 每个 GovernorResult 写 Journal 和 Shadow evaluation projection，包含 reason codes 与 rejected candidates，不保存 Chain-of-Thought。
- [ ] Run: `pytest tests/social_runtime tests/scenarios tests/contracts tests/shared -q && python -m tests.architecture_guard && git diff --check`。
- [ ] Commit `feat: complete social cognition shadow runtime`，使用 `superpowers:requesting-code-review`；通过后进入 Phase C。
