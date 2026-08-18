# Social Runtime v2：Shadow 运行手册

## 当前边界

Phase B 仅允许 `SHADOW`。系统可以接收群事件、投影现场、运行认知与社会治理并记录“如果允许行动会怎么做”，但不会创建 `ActionPlan`、不会写入 Outbox，也不会调用任何 QQ 发送端口。

`SOCIAL_RUNTIME` 在进入数据库 I/O 前仍然拒绝启动；这不是配置缺失，而是发布门禁。

## 主链路

```text
SocialEventEnvelope
  → GroupWorld projection
  → AttentionFrame (FAST / AMBIENT / TEMPORAL)
  → stateless Cognition Workers
  → cycle Blackboard
  → CandidateIntention
  → deterministic SocialGovernor
  → GovernorResult
  → Journal + governor_results (Shadow only)
```

GroupSceneActor 是单群单人格的现场写入者。Worker 在 Actor 邮箱之外运行，不能访问 Repository、Execution Port 或 Actor mutation。

## 周期冻结与过期结果

每个 AttentionFrame 同时冻结：

- `scene_version`
- `config_version`
- `persona_state_version`

Worker 返回后，Actor 会再次对照当前现场、当前配置与当前人格状态。任一版本不一致，工作项都会解析为 `stale`；即使 Governor 曾给出 `ACT`，也不会记录为有效 Shadow 决策，更不会产生外部动作。

AMBIENT 通道需要安静窗口到期后显式推进时钟：

```python
await manager.drain(now=current_timestamp)
```

不传 `now` 时只处理已经随事件生成的 FAST/TEMPORAL 帧，不会提前关闭“说完再答”的窗口。

## 持久化白名单

有效 GovernorResult 会在同一事务内：

1. 将 `scene_work_requests` 标记为 `accepted`；
2. 写入 `journal`，类型为 `shadow.governor_evaluated`；
3. 写入 `governor_results` 投影。

只保存以下治理摘要：outcome、selected intention IDs、rejected candidates、reason codes、reconsider time、active constraints，以及三类冻结版本。不会保存 Worker proposition、原始提示词或 Chain-of-Thought。

过期结果只把工作项标记为 `stale`，不会写入 Governor Journal/投影。

## 核查命令

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest \
  tests/social_runtime tests/scenarios tests/contracts tests/shared tests/recovery \
  -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m tests.architecture_guard
git diff --check
```

运行中还应核查：

- `outbox_count() == 0`
- `execution_port.calls == ()`
- `journal(correlation_id)` 只出现 world projection 与 Shadow evaluation
- stale 周期不存在 `shadow.governor_evaluated`

## 故障语义

- Worker 超时、异常或非法 JSON：记录诊断并降级为 OBSERVE/SILENCE，不绕过 Governor。
- Cognition 预算耗尽：Blackboard 标记 degraded，Governor 强制 OBSERVE。
- 场景、配置或人格状态变化：丢弃旧周期结果。
- 进程在结果前退出：pending SceneWorkRequest 可在重启后重发；已提交事件通过 Cursor/Snapshot 重放。
- Shadow evaluation 事务失败：工作项不会被部分接受，Journal 与投影不会出现半写状态。

## 进入 Phase C 前的硬条件

只有在固定场景回放、恢复测试、架构依赖守卫和 Shadow 零副作用检查全部通过后，才能设计 `ActionPlan → DeliveryBundle → Outbox`。Phase B 的 Governor `ACT` 不能被当作发送授权。
