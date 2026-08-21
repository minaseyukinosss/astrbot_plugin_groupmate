# Groupmate Social Runtime v2

Groupmate v2 让同一个 Persona 以克制、连续、可纠正的方式参与群聊。系统先判断是否应关注和参与，再决定是否说话或行动；沉默、拒绝、过期和人工暂停都是正式结果。

平台事实事件写入 Durable Inbox/Journal。Persona Supervisor 维护全局自我，每群 Group Scene Actor 维护 Group World；Attention/Cognition 产生观察，Social Governor 执行硬约束，ActionPlan/Task 管理工作，Transactional Outbox 与单一 Dispatcher 管理交付。Projection Consumer 构建隐私裁剪控制面，页面不直接写领域表或显示 Chain-of-Thought。

AstrBot 原生配置只声明群 allowlist、运行模式、Provider 和 Persona；控制权限、Worker 上限和外置插件规则由运行时内部治理。数据仅存于 `data/plugin_data/astrbot_plugin_groupmate/groupmate-social-runtime-v2.db`。V2 不读取或迁移旧 `groupmate.db`，旧数据库、旧配置及旧内部 API 不兼容。

运行模式为 `OFF`、no-send `SHADOW` 和 `SOCIAL_RUNTIME`。首次配置默认使用 SHADOW；管理员可在 AstrBot 原生插件配置中明确选择正式运行，选择结果只应用于群 allowlist。治理命令仍要求服务端权限、作用域、Expected Version、原因与确认；安全、双发送或 UNKNOWN spike 必须暂停。

灾难恢复先 pause，再做 V2 SQLite 一致备份、临时恢复、replay/Snapshot/Journal/Outbox 核对和 Projection rebuild。`SENT`/`UNKNOWN` 不得重发。synthetic/bootstrap/fake evidence 仍只能作为离线质量证据，不能冒充真实群聊运行结果。

详见 `docs/operations/social-runtime-disaster-recovery.md` 与 `docs/releases/social-runtime-v2-acceptance.md`。
