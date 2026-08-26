# AstrBot Groupmate Social Runtime v2

Groupmate v2 是 clean-slate 的持久群聊社会运行时。事实事件进入 Durable Inbox/Journal，由唯一 Persona Supervisor 与每群唯一 Group Scene Actor 更新状态；Attention/Cognition、Social Governor、ActionPlan/Task、Transactional Outbox 与 Dispatcher 分离认知、授权和交付。独立 Projection Cursor 从 Journal 构建控制面读模型。

## 配置与数据

普通安装配置只包含启用群、运行模式、Groupmate 独立文本模型和可选视觉模型。模型使用 AstrBot 原生 Provider 选择器；Bot 身份从消息事件的 `self_id` 自动取得。Groupmate 不复用 AstrBot 普通会话 Persona，而是使用自己独立、版本化的群聊人格配置。插件页面现阶段提供“运行中心”和“群成员画像”：前者查看 NapCat → AstrBot → Groupmate 的逐消息链路、SHADOW 判断、外部插件移交和最终发送结果；后者供管理员查看按群隔离的成员认知、代表经历与群友关系。

运行中心会按消息段顺序展示文本、图片、语音、视频、文件、QQ 表情、商城表情、合并转发与卡片消息。公开 trace 只包含裁剪后的类型、名称、大小和不透明媒体引用；原始 NapCat URL、文件路径与平台标识不会直接返回给浏览器。图片、语音和视频预览由服务端执行作用域、类型与大小校验后按需提供。

权威 V2 数据库固定为：

```text
data/plugin_data/astrbot_plugin_groupmate/groupmate-social-runtime-v2.db
```

V2 不读取、升级或迁移旧 `groupmate.db`。旧数据库、旧配置和旧内部 API 均不兼容；请按 V2 schema 重新配置，社会状态从空状态开始。

数据库文件名和路径由插件内部固定管理，不能在配置页选择或覆盖。首次配置默认进入无发送的 `SHADOW`；需要正常参与群聊时，可在 AstrBot 插件配置中明确切换为“正式运行”。视觉模型可以留空。

人格档案按群独立发布，并与其他行为校准共用不可变 Config Version。认知周期开始时会冻结当时已发布的人格版本，运行过程中不会混用新旧设定。未发布自定义人格时使用插件自带的群聊伙伴默认档案。

## 群成员画像

Groupmate 会在消息可靠入库后，把画像观察放入独立后台队列；画像模型失败、超时或重试不会阻塞闲聊主链路。模型只提出结构化候选，本地证据策略决定事实是否确认并可用于回复。自述可以成为已确认事实，第三方转述默认不注入；行为模式和群友关系需要多次独立证据。旧认知可以被纠正、标记过时或失效，历史与审计仍保留。

成员可在群内使用以下精确指令，结果仅返回给指令发起者的当前上下文，不提供公开群排名：

- `查看我的画像` 或 `我的画像`
- `纠正画像 <编号> <正确内容>`
- `删除画像 <编号>`
- `停止画像个性化` / `恢复画像个性化`

管理员可在“群成员画像”页面查看每位成员的一句话画像、个体特征、偏好边界、代表经历与群友关系，并在折叠的“证据与审计”区域纠正或使错误事实失效。页面和 API 只返回作用域内的不透明 `member_ref`；原始 QQ/平台 ID、模型原始输出和跨群画像不会暴露。

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
