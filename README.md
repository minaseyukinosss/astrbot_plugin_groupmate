# AstrBot Groupmate Social Runtime v2

Groupmate v2 是 clean-slate 的持久群聊社会运行时。事实事件进入 Durable Inbox/Journal，由唯一 Persona Supervisor 与每群唯一 Group Scene Actor 更新状态；Attention/Cognition、Social Governor、ActionPlan/Task、Transactional Outbox 与 Dispatcher 分离认知、授权和交付。独立 Projection Cursor 从 Journal 构建控制面读模型。

## 配置与数据

配置契约见 `_conf_schema.json`。主要字段包括 `runtime_mode`、`enabled_groups`、`social_runtime_test_groups`、`control_admin_ids`、Provider、Persona、Worker 并发上限和已安装外置插件触发规则。

权威 V2 数据库固定为：

```text
data/plugin_data/astrbot_plugin_groupmate/groupmate-social-runtime-v2.db
```

V2 不读取、升级或迁移旧 `groupmate.db`。旧数据库、旧配置和旧内部 API 均不兼容；请按 V2 schema 重新配置，社会状态从空状态开始。

## 模式与治理

- `OFF`：不处理群事件。
- `SHADOW`：运行认知和评估，但不发送或执行外部副作用。
- `SOCIAL_RUNTIME`：V2 拥有决策与交付；只有 installed-live SHADOW、冻结 holdout、24h 观察、页面/容量/安全、旧实例停止和 rollout gates 全部通过才可启用。

高影响命令必须通过服务端管理员作用域、原因、确认和 Expected Version 校验。Outbox `UNKNOWN` 禁止盲重试。当前候选只有离线证据，生产接管保持 fail closed。

## 运维与验收

- [灾难恢复](docs/operations/social-runtime-disaster-recovery.md)
- [生产放量](docs/operations/social-runtime-rollout.md)
- [发布候选验收](docs/releases/social-runtime-v2-acceptance.md)

离线恢复使用 SQLite online backup、临时恢复、Event/Journal/Snapshot/Outbox 核对和 Projection rebuild。`SENT`/`UNKNOWN` 不重发。离线 `PASS_OFFLINE` 不等于真实 SHADOW、supervised、canary 或平台交付通过。
