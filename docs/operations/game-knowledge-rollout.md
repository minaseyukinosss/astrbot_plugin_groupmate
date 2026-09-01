# 游戏知识上线与回滚手册

## 发布顺序

上线固定按以下顺序推进，禁止跨级：

1. fixture SHADOW：运行冻结语料、知识安全与 AMBIENT 预算测试。
2. installed-live SHADOW：在真实 AstrBot、真实消息流和已配置搜索源下观察至少 24 小时，保持不发送。
3. DIRECT/CONTINUATION：只开放明确提问与会话续接，AMBIENT 仍关闭。
4. 单群 AMBIENT canary：只选一个已明确授权的群，观察一个完整的上海时区日界周期。
5. 逐群扩大：每次只新增少量群，每批重复同一 Gate；共享知识库不按群复制，群热门、别名和 canary 状态继续按群隔离。

真实 24 小时 installed-live 和单群日界观察属于部署门禁，自动测试不能代替，也不得补写或伪造证据。

## 自动预检

```bash
.venv/bin/python -m pytest -q \
  tests/social_runtime/knowledge \
  tests/contracts/test_knowledge_admin.py \
  tests/contracts/test_knowledge_web_api.py \
  tests/page/test_knowledge_workspace.py \
  tests/scenarios/test_ambient_game_knowledge.py \
  tests/evaluation/test_knowledge_capacity.py
```

预检必须全部通过。非知识 AMBIENT search、cross-group leak、stale scene send 和 unsupported claim 必须均为 0。随后运行一次 `.venv/bin/python -m pytest -q`，确认没有全局回归。

## installed-live SHADOW 检查单

环境必须启用知识能力、配置可用的官方来源与搜索 Provider，并保持 runtime 为 SHADOW。观察窗口从一个上海时区日界开始，至少跨过下一个日界。

每项只保存以下安全字段：`check_id`、`completed_at`、`trace_ref`、`source_class`、`freshness`、`fragment_status`、`diagnostic_code`、`result`。不得保存群消息全文、成员标识、原始查询、完整 URL 或 Provider excerpt。

| 检查项 | 通过条件 |
| --- | --- |
| seed import | 三角洲行动、鸣潮、崩坏：星穹铁道、绝区零实体与官方来源注册成功；不要求预装原神 |
| 四款 daily jobs | 四款游戏各完成一次日更任务；成功或有明确安全失败码，不得静默丢任务 |
| 官方更新 | 一次官方更新形成可追溯 source class、freshness 与事实 fragment |
| valid negative | 完整覆盖必要官方来源后，形成带范围和过期时间的限定性 negative，不得表达成“网上没有” |
| rumor | 未获官方确认时保持 rumor/unconfirmed；不得升级为 public official fact |
| provider timeout | 回复失败关闭、无 outbox；后台任务可按现有重试策略恢复 |
| 场景过期 | scene/target/intention 变化后无发送，trace 有固定失效码 |
| 长尾游戏 | 达到群热门阈值后只后台学习稳定公开语义，不生成当前版本结论 |
| 群 alias 纠正 | 纠正只影响当前群约定，保留旧 revision 和管理员审计，不产生全局 alias |

任一项缺 trace、语义不明确或出现发送副作用，installed-live Gate 不通过。修复后重新开始完整 24 小时窗口。

## 单群 canary Gate

基线取同一个群 canary 开启前最近连续 7 天，canary 取开启后的一个完整日界周期。只保存匿名聚合：

- `observation_hours`、`opportunities`、`ambient_actions`、`ambient_searches`
- `p95_latency_ms`、固定类别的 `silence_reasons`
- `unsupported_claims`、`stale_scene_sends`、`cross_group_leaks`
- `nonknowledge_ambient_searches`、`provider_quota_anomalies`

`eval.knowledge.evaluate_canary_rollout(baseline, canary)` 返回 `expand`、`hold` 或 `rollback`，同时给出参与率、AMBIENT 搜索率、P95 和沉默原因对比。以下任一条件立即 rollback：

- unsupported claim、stale scene send、cross-group leak 或非知识 AMBIENT search 大于 0；
- Provider quota 出现异常；正常额度拒绝也必须人工复核，不得通过提额掩盖；
- 参与率相对基线上升超过 10%，或绝对上升超过 2 个百分点。

不足 7 天基线、不足 24 小时 canary 或任一窗口没有 opportunity 时返回 `hold`，不得扩大。

## 开启与回滚

开启时在管理端进入“本群认知”，确认目标群和当前 revision，只提交一次 `knowledge.ambient_canary_enabled=true`。其他群保持 false。DIRECT/CONTINUATION 不受该开关影响。

触发 rollback 后立即为同一群提交 `knowledge.ambient_canary_enabled=false`，确认新 revision 和审计记录，然后：

1. 保持 DIRECT/CONTINUATION 与本地已核验知识可用，不删除共享事实或群约定。
2. 保存失败窗口的匿名聚合和相关 trace ref，停止新增 canary 群。
3. 检查运行保障中的 quota、queue/job lag、freshness、scene invalidation 和 grounding reject。
4. 修复后重新跑自动预检、24 小时 SHADOW 与一个完整 canary 日界，不沿用失败窗口凑时长。

只有自动 Gate、installed-live 人工门和单群日界 Gate 全部通过，才允许逐群扩大。
