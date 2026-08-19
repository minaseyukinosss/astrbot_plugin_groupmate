# Social Runtime v2 Phase C 交付运行手册

## 适用范围

本文只覆盖 Phase C 的 ActionPlan、TaskRuntime、Delivery Outbox、OneBot Dispatcher、Temporal Opportunity 和 Gate C 测试发布。它不授权生产 QQ 群发送。

Gate C 当前仅允许明确列入 `social_runtime_test_groups` 的 fake/test group。任何真实群在完成后续发布审批前都必须保持 `SHADOW` 或 `OFF`。

## Gate C 配置

```json
{
  "runtime_mode": "SOCIAL_RUNTIME",
  "enabled_groups": ["fake-onebot-group", "shadow-comparison-group"],
  "social_runtime_test_groups": ["fake-onebot-group"]
}
```

每群的有效模式由代码确定：

- 同时位于 `enabled_groups` 和 `social_runtime_test_groups`：`SOCIAL_RUNTIME`；
- 只位于 `enabled_groups`：`SHADOW`；
- 不在 `enabled_groups`：`OFF`。

`SOCIAL_RUNTIME` 缺少测试群白名单、白名单为空，或白名单包含未启用群时，Manager 必须在创建数据库和任何 I/O 前拒绝启动。白名单名称不能被理解为生产授权；运维人员仍须确认其中只有隔离的 fake OneBot 群。

## 权威交付线路

```text
SocialEvent
→ GroupSceneActor / Attention
→ Cognition / CandidateIntention
→ GovernorResult(ACT)
→ ActionPlanner
→ ActionPlanValidator
→ ExecutionCoordinator
→ SafeTextGeneration / OutputFirewall
→ DeliveryBundle
→ Transactional Outbox(sending 已持久化)
→ OneBot Dispatcher
→ DeliveryReceipt
→ ExecutionCoordinator
→ Event Fabric / 原 GroupSceneActor
```

外部动作入口必须先调用 Manager 的 Gate C 群校验，再通过 `submit_plan` 提交已经验证并与同一 Plan digest 绑定的计划。Dispatcher 只能发送 Outbox 中精确处于 `sending` 的 Part。receipt handler 必须先持久化结果并返回同一个 Part，随后由 Coordinator 将 `delivery.*` 事件写回 Fabric。

## 因果与审计

每个可见 Part 必须能够沿以下字段回溯：

1. Bot ledger 的 `part_id`、`bundle_id`、`correlation_id` 和平台消息 ID；
2. Outbox Part 的独立 `idempotency_key`、payload、顺序和 receipt；
3. DeliveryBundle 的 Persona、群、话题和 correlation；
4. ActionPlan 的 `plan_id`、`intention_ids`、版本与约束；
5. Governor projection 中实际选中的 intention；
6. 最初 SocialEvent 的 correlation/causation/evidence。

任一环缺失时不得把消息视为通过 Gate C。内部 Plan、Task、事件 ID 和推理信息不得进入可见文本；文本仍须经过 StyleDirector 与 OutputFirewall。

## 故障矩阵与处置

| 故障 | 权威行为 | 禁止行为 |
| --- | --- | --- |
| Generator failure | 必要回应使用固定安全 fallback；可选参与 silence | 发送未审查草稿或虚构成功 |
| Provider timeout | 到期 Task 转为 `EXPIRED`，不重启 Provider 副作用 | 制造“处理中”进度或解析聊天文本猜状态 |
| Duplicate progress | 相同 ProviderEvent ID 幂等返回原状态 | 重复制造进度消息 |
| Cancel race | 第一个持久终态获胜，晚到相冲突事件拒绝 | 覆盖已确认的终态 |
| Partial send | 准确保留已发送 Part；后续 Part 按顺序阻塞 | 重发已确认 Part |
| Unknown receipt | Part 保持 `UNKNOWN`，后续发送阻塞并进入人工调查 | 盲目自动重试 |
| Restart | 从 Task、Plan node、Outbox 和 receipt 的持久状态恢复 | 重做不可确认的副作用 |
| Expired result | 结果可准确完成，但非直接义务失去交付价值 | 用过期结果打断无关场景 |
| Projection failure | Inbox/Journal/Actor/Task 继续；Snapshot 可由事件重放恢复 | 因页面、Projection 或 Snapshot 故障回滚任务 |

Provider 状态只接受注册 Provider Contract 产生的结构化 `ProviderEvent`。关系温度、亲密度、群文化或模型判断都不能增加权限、跳过确认或改变 receipt 重试分类。

## Unknown receipt 调查

1. 停止该 Bundle 后续 Part 的自动派发；
2. 记录 Part、Bundle、idempotency key、调用时间和 OneBot 错误；
3. 使用平台提供的确定性查询能力确认消息是否存在；
4. 无法确认时保持 `UNKNOWN`，不得改回 `READY`；
5. 只有管理员基于平台证据执行新的显式动作，才可创建新的计划和新的幂等身份。

## 发布前检查

- Phase C 故障矩阵全部通过；
- `architecture_guard` 和 `git diff --check` 通过；
- fake OneBot 调用记录证明平台调用前已有 `sending` 状态；
- receipt 已经回到 Event Fabric 和原 GroupSceneActor；
- Shadow 回归保持 Outbox、execution port 和外部调用为零；
- allowlist 只包含隔离 fake/test group；
- `unknown`、过期和部分发送均未触发盲目重试；
- 所有可见 Part 都完成 Decision→Plan→Bundle→Part→Receipt 因果核验。

未满足任一项时保持 `SHADOW/OFF`。Gate C 不等于生产发布许可。
