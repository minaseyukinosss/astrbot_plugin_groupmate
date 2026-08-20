# Social Runtime v2 生产接管与放量手册

## 当前边界

Task 5 只交付 fail-closed Readiness、所有权交接命令和可审计放量状态机。仓库中没有真实部署的 installed-live SHADOW 数据，因此当前 `ReadinessGate.evaluate(group_id)` 必须返回失败报告；本任务不会连接或发送真实 QQ 消息。

历史 bootstrap 只用于回归，`evidence_kind=synthetic_preflight` 只用于容量预检。两者都不能替代 installed-live SHADOW 的冻结 holdout、场景覆盖、质量、安全和真实容量证据。

## Readiness 报告

每个显式 allowlist 群逐项检查：

- Gate A–D；
- 至少 100 条人工复核、时间冻结且与当前 Config Version 一致的 installed-live SHADOW；
- holdout 阈值和规格场景覆盖；
- safety event、UNKNOWN Outbox 和过期 SHADOW backlog 均为零；
- Governance 页面暂停、检查、发布、纠正和恢复工作流；
- 来源为 installed-live SHADOW 且全部 applicable/pass 的容量预算；
- 连续至少 24 小时 SHADOW；
- 管理员明确确认旧实例已停止，且没有遗留进行中的外部副作用。

缺少任何证据时仍返回结构化失败报告和 `failed_checks`，不得补默认值。通过报告写入同一个 v2 SQLite 数据库，以 SHA-256 hash 标识，并绑定 Persona、群、证据摘要、当前控制版本、当前 Config Version 和旧实例确认引用；同一 hash 不可覆盖。

## 所有权交接

旧实例停止是人工部署动作。系统不得自动停止旧实例，也不得从进程、端口或消息文本猜测“已经停止”。管理员先调用 `ReadinessGate.confirm_old_instance_stopped(...)` 登记一个高熵、单次、作用域明确的确认 token；确认必须同时声明没有旧实例遗留的进行中外部副作用。

随后通过现有 Command API 发布：

```json
{
  "type": "runtime_mode_set",
  "expected_version": 0,
  "reason": "explicit production ownership handoff",
  "confirmed": true,
  "payload": {
    "runtime_mode": "SOCIAL_RUNTIME",
    "readiness_report_hash": "<immutable sha256>",
    "old_instance_confirmation_token": "<one-time secret>"
  }
}
```

服务端在同一个 `BEGIN IMMEDIATE` 事务中重新校验管理员、Persona/群作用域、reason、Expected Version、报告、证据摘要、Config Version、operator 与 token，然后消费 token 并写 Governance Action。不同 command 重放 token、跨 Persona/群/operator 使用、报告后变更证据或版本都会拒绝。相同 command ID 的原样重放只返回既有幂等结果。

首轮只允许 allowlist 中明确排在第一位的测试群进入 `SUPERVISED`。命令成功本身不创建 DeliveryBundle、不写 Outbox、不调用 OneBot；生产组合根只有在完整交接成功后才能使用该审计状态启用相应发送边界。

## 时间硬门与扩展

放量顺序固定为：

1. 单一测试群 `SHADOW` 至少 24 小时；
2. 管理员完成旧实例停止确认和所有权交接；
3. `SUPERVISED` 至少 2 小时；
4. `CANARY` 至少 24 小时；
5. `EXPANSION_1 → EXPANSION_3 → EXPANSION_10 → EXPANSION_ALL`，每档至少观察 24 小时。

推进使用 `rollout_advance` Command。时间只取服务端 clock；客户端不能提交观察时间。每次 Governance Action 和 rollout audit 都记录 report hash、operator、reason、Expected Version、阶段和开始时间。时间不足、阶段已暂停、hash 不同或版本冲突时拒绝推进。

## 自动暂停与恢复原则

运行监控把真实计数交给 `ReadinessGate.observe_runtime_health(...)`。任何 safety event、dual-sender event 或 UNKNOWN Outbox 新增都会立即把 rollout audit 状态写为 `PAUSED`，并通过注入的现有 RuntimeGovernance pause 边界设置 `RuntimeGovernanceState(paused=True)`。

暂停后：

- 禁止自动推进或继续扩大群数；
- UNKNOWN 发送不得盲目重试；
- 不自动启动旧实例，不自动切换旧代码，不把 V2 数据写回旧数据库；
- 管理员调查 Outbox、平台回执和双发送来源，重新收集 live evidence，并以新的版本、报告和明确命令恢复。

旧版本恢复属于人工灾难恢复部署，不属于 Task 5 rollout 状态机。

## 聚焦验证

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/social_runtime/test_readiness.py \
  tests/recovery/test_no_dual_sender.py \
  tests/contracts/test_commands.py \
  tests/contracts/test_web_api.py
.venv/bin/python -m tests.architecture_guard
git diff --check
```
