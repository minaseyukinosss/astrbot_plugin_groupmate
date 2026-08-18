# Social Runtime v2 Phase E：Shadow 评估与生产接管实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用完整群聊历史、人工标签、故障注入和逐群 Allowlist 证明 Social Runtime v2 达到目标群聊伙伴效果并可安全接管生产。

**Architecture:** 全新 Evaluation Harness 将聊天导出标准化为平台事件，对固定 Worker outputs 和真实模型分别运行 Shadow；质量、安全、成本、延迟和恢复门槛全部通过后，才允许配置发布将群从 `SHADOW` 切到 `SOCIAL_RUNTIME`。

**Tech Stack:** Python JSONL、现有事件回放、pytest、结构化评估器、插件页面 Evaluation Projection。

**Spec:** `docs/superpowers/specs/2026-08-18-groupmate-social-runtime-v2-design.md`

## Global Constraints

- 旧插件代码、旧数据库和旧测试不进入评估；目标群聊导出只作为只读语料。
- Bot-only 数据只能评估风格/分段/媒体；参与时机必须用完整成员消息上下文。
- 评估集分 calibration 与 holdout，场景 ID 固定；不得为通过 holdout 手工改标签。
- 内部 ID、CoT、跨群隐私和未授权工具事件门槛为绝对零。
- 没有明确旧实例停止确认时，不允许生产 V2 发送。

---

### Task 1: 新建导出 Ingest、标签 Schema 与语料集

**Files:** Create `eval/schema.py`, `export_ingest.py`, `build_corpus.py`, `scenarios/target_calibration.jsonl`, `scenarios/target_holdout.jsonl`; create `tests/evaluation/test_ingest.py`, `test_labels.py`.

**Interfaces:** `ingest_export(path) -> Iterator[SocialEventEnvelope]`; label fields `attention/action/target/acceptable_intents/unacceptable_intents/modalities/sensitivity/expires_after_ms`.

- [ ] 测试 QQ `323537051` 正确识别为目标 bot；成员、回复链、@、媒体、撤回、重复和时间顺序保持；未知字段不丢失到 raw evidence hash。
- [ ] 从用户提供的 chunked JSONL 生成去标识评估 Corpus；真实 QQ 号只保存在访问受限 mapping，不写 Fixture。
- [ ] 至少标注 calibration 200 个机会、holdout 200 个机会，覆盖规格 22.3 全部场景；每类至少 15 个正例和 15 个正确沉默例。
- [ ] Run: `pytest tests/evaluation/test_ingest.py tests/evaluation/test_labels.py -q`; commit `test: build target behavior evaluation corpus`。

---

### Task 2: 指标、Runner 与安全扫描

**Files:** Create `eval/runner.py`, `metrics.py`, `safety.py`, `report.py`; create `tests/evaluation/test_metrics.py`, `test_runner.py`, `test_safety.py`.

**Interfaces:** `EvaluationRunner.run(corpus, runtime, worker_mode) -> EvaluationReport`; report includes per-group/per-scene confusion matrices and latency/cost.

- [ ] 实现 attention/action/target precision-recall、open participation precision、miss rate、interrupt/monopoly/repetition/target concentration、autonomy value/expiry、persona/relationship/culture、task/delivery/recovery、style/media 指标。
- [ ] 安全扫描每个 event/observation/plan/outbox/page projection，检测 internal ID、CoT marker、跨群 evidence、unauthorized capability 和 duplicate delivery。
- [ ] 固定 Worker mode 必须 bit-for-bit deterministic；真实模型 mode 记录 provider/model/config/token/latency，不把自由模型解释当标签。
- [ ] Run: `pytest tests/evaluation -q`; commit `feat: evaluate target social behavior`。

---

### Task 3: Shadow 复核与校准治理

**Files:** Create `eval/shadow.py`, `eval/rubrics/social_judge.md`; modify `control/projections.py`, Governance workspace; create `tests/evaluation/test_shadow_review.py`.

**Interfaces:** Shadow result → Evaluation Projection → admin label/correction/calibration approval command.

- [ ] 页面逐条展示上下文、Attention、对象、候选、Governor outcome、理由和有效期；不展示 CoT。
- [ ] 校准仅可改变群级 attention window、reply length tendency、media preference、participation weights 的管理员范围内值；minimum reviewed samples=100，误判率上限由发布配置显式设定。
- [ ] 每次校准运行 calibration + holdout diff；任一安全指标恶化或 holdout 不达门槛即拒绝，批准后生成新 Config Version，可回滚。
- [ ] Run focused tests; commit `feat: govern shadow review and calibration`。

---

### Task 4: 故障、负载和背压验证

**Files:** Create `tests/recovery/test_production_fault_matrix.py`, `tests/evaluation/test_load_budget.py`; create `eval/load_runner.py`, `docs/operations/social-runtime-capacity.md`.

**Interfaces:** 公开 budgets：Actor backlog、Worker concurrency/cost、P50/P95/P99 decision latency、Projection lag、Outbox unknown rate。

- [ ] 注入 DB busy、Worker timeout/invalid JSON、Provider duplicate/out-of-order、OneBot timeout/unknown、SSE outage、projection corruption、process crash、clock jump。
- [ ] 以 50 群、每群 5 msg/s、10 个并发长任务运行 30 分钟 fake load；Actor 不丢事件，硬直接事件不被 Ambient backlog 饿死。
- [ ] 预算初值：Fast P95 ≤2.5s（不含外部任务）、Ambient 决策 P95 ≤8s、Projection lag P95 ≤5s、unknown delivery <0.1%、单群 backlog 告警 100、全局 Worker concurrency 可配置硬限。
- [ ] Run recovery/load tests; commit `test: verify social runtime production resilience`。

---

### Task 5: Allowlist 接管与停止旧实例检查

**Files:** Create `groupmate/social_runtime/readiness.py`, `docs/operations/social-runtime-rollout.md`; create `tests/social_runtime/test_readiness.py`, `tests/recovery/test_no_dual_sender.py`.

**Interfaces:** `ReadinessGate.evaluate(group_id) -> ReadinessReport`; publish command `SetRuntimeMode(SOCIAL_RUNTIME)` requires passing report and old-instance confirmation token.

- [ ] Readiness 检查 Gate A–D、holdout 阈值、安全零事件、无未知 Outbox、无过期 Shadow backlog、页面工作流、capacity budget、旧实例停止确认。
- [ ] 首轮只开放一个测试群：24h Shadow → 管理员确认 → 2h supervised send → 24h canary；任何安全/双发送/unknown spike 自动 pause，不自动切回旧代码。
- [ ] 扩展节奏 1→3→10→全部 allowlist，每一档至少观察 24h；新档位发布必须记录报告 hash、操作者、原因和 expected version。
- [ ] Run focused tests; commit `feat: gate production social runtime ownership`。

---

### Task 6: 最终验收、灾难恢复与发布

**Files:** Create `docs/operations/social-runtime-disaster-recovery.md`, `docs/releases/social-runtime-v2-acceptance.md`; modify `README.md`, `PRODUCT.md`, `metadata.yaml`.

**Interfaces:** 正式发布产物与可审计验收报告。

- [ ] 演练 pause、备份新 DB、恢复到临时目录、replay、核对 Snapshot/Journal/Outbox、重新连接 Projection、恢复一个 allowlist 群；已 sent 内容不重发。
- [ ] 执行 `pytest -q`, `python -m tests.architecture_guard`, Shadow holdout Runner、fault matrix、load runner、页面工作流和 `git diff --check`；把命令、时间、版本和结果写入 acceptance 文档。
- [ ] README 只描述 V2 架构、配置、数据路径、运行模式、治理和恢复；metadata 发布 major version，明确不兼容旧数据库和旧配置。
- [ ] 使用 `superpowers:verification-before-completion` 核实所有结果，再用 `superpowers:requesting-code-review` 做最终评审。
- [ ] 评审通过后使用 `superpowers:finishing-a-development-branch` 决定合并/PR；不得在这些证据之前宣称完成。
