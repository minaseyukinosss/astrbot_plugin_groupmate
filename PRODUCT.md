# Groupmate Social Runtime v2

Groupmate v2 让同一个 Persona 以克制、连续、可纠正的方式参与群聊。系统先判断是否应关注和参与，再决定是否说话或行动；沉默、拒绝、过期和人工暂停都是正式结果。

平台事实事件写入 Durable Inbox/Journal。Persona Supervisor 维护全局自我，每群 Group Scene Actor 维护 Group World；Attention/Cognition 产生观察，Social Governor 执行硬约束，ActionPlan/Task 管理工作，Transactional Outbox 与单一 Dispatcher 管理交付。Projection Consumer 构建隐私裁剪控制面，页面不直接写领域表或显示 Chain-of-Thought。

配置按 V2 schema 显式声明群 allowlist、模式、控制面管理员、Provider、Persona、Worker 上限和外置插件规则。数据仅存于 `data/plugin_data/astrbot_plugin_groupmate/groupmate-social-runtime-v2.db`。V2 不读取或迁移旧 `groupmate.db`，旧数据库、旧配置及旧内部 API 不兼容。

运行模式为 `OFF`、no-send `SHADOW` 和受完整 readiness 门控的 `SOCIAL_RUNTIME`。治理命令要求服务端权限、作用域、Expected Version、原因与确认；真实接管依次要求 24h installed-live SHADOW、2h supervised、24h canary 和 1→3→10→all 放量。安全、双发送或 UNKNOWN spike 必须暂停。

灾难恢复先 pause，再做 V2 SQLite 一致备份、临时恢复、replay/Snapshot/Journal/Outbox 核对和 Projection rebuild。`SENT`/`UNKNOWN` 不得重发。synthetic/bootstrap/fake evidence 只能记为 `PASS_OFFLINE`；live SHADOW、24h、supervised、canary 与真实平台交付未采集时，总体必须保持 `BLOCKED_NOT_COLLECTED`，不得启用真实 QQ、发布 release 或宣称 Phase E 完成。

详见 `docs/operations/social-runtime-disaster-recovery.md` 与 `docs/releases/social-runtime-v2-acceptance.md`。
