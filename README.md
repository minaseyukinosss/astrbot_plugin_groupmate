# AstrBot Groupmate Social Runtime v2

Groupmate v2 是 clean-slate 的持久群聊社会运行时。事实事件进入 Durable Inbox/Journal，由唯一 Persona Supervisor 与每群唯一 Group Scene Actor 更新状态；Attention/Cognition、Social Governor、ActionPlan/Task、Transactional Outbox 与 Dispatcher 分离认知、授权和交付。独立 Projection Cursor 从 Journal 构建控制面读模型。

## 配置与数据

普通安装配置只包含启用群、运行模式、Groupmate 独立文本模型、可选视觉模型和 AstrBot Persona。模型与人格使用 AstrBot 原生选择器；Bot 身份从消息事件的 `self_id` 自动取得。管理员授权、外置插件兼容规则和 Worker 并发属于内部治理状态，不在首次安装配置中展示。

权威 V2 数据库固定为：

```text
data/plugin_data/astrbot_plugin_groupmate/groupmate-social-runtime-v2.db
```

V2 不读取、升级或迁移旧 `groupmate.db`。旧数据库、旧配置和旧内部 API 均不兼容；请按 V2 schema 重新配置，社会状态从空状态开始。

数据库文件名和路径由插件内部固定管理，不能在配置页选择或覆盖。首次配置默认进入无发送的 `SHADOW`；需要正常参与群聊时，可在 AstrBot 插件配置中明确切换为“正式运行”。视觉模型可以留空。

## 模式与治理

- `OFF`：不处理群事件。
- `SHADOW`：运行认知和评估，但不发送或执行外部副作用。
- `SOCIAL_RUNTIME`：V2 在配置中所列的启用群内拥有决策与交付；由管理员在 AstrBot 原生插件配置中明确选择。

高影响命令仍必须通过服务端管理员作用域、原因、确认和 Expected Version 校验。Outbox `UNKNOWN` 禁止盲重试，外置插件拥有的请求不会被 Groupmate 抢答。

## 运维与验收

- [灾难恢复](docs/operations/social-runtime-disaster-recovery.md)
- [生产放量](docs/operations/social-runtime-rollout.md)
- [发布候选验收](docs/releases/social-runtime-v2-acceptance.md)

离线恢复使用 SQLite online backup、临时恢复、Event/Journal/Snapshot/Outbox 核对和 Projection rebuild。`SENT`/`UNKNOWN` 不重发。离线 `PASS_OFFLINE` 不等于真实 SHADOW、supervised、canary 或平台交付通过。
