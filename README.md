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

如果一次升级明确决定让当前 V2 画像与社会状态也从头开始，插件代码不会自动清库。必须先停止 AstrBot、导出当前插件配置、使用 SQLite `.backup` 制作一致性备份，再把 `groupmate-social-runtime-v2.db`、`-wal`、`-shm` 三个文件一起移入可恢复的归档目录。完整切换和回滚步骤见[控制面运维手册](docs/operations/social-runtime-control-plane.md#全新数据库切换)。

数据库文件名和路径由插件内部固定管理，不能在配置页选择或覆盖。首次配置默认进入无发送的 `SHADOW`；需要正常参与群聊时，可在 AstrBot 插件配置中明确切换为“正式运行”。视觉模型可以留空。

## 群聊游戏知识

Groupmate 默认预装原神、三角洲行动、鸣潮、崩坏：星穹铁道和绝区零的稳定语义、常用术语与实体别名，并在消息可靠入库后异步学习当前群自己的称呼约定。群约定按群隔离，不绑定 Persona，也不会覆盖预装公共语义；Bot 自身输出、外部 Bot、命令和转发内容不会晋升为群知识。

本地解析在参与判断前冻结为同一份话题理解快照。涉及“新版本、下版本、前瞻、爆料、测试服、刚更新”等时效性问题时，只标记为“需要新鲜证据”，不会使用稳定语义猜测版本事实。当前阶段不执行网络搜索，也不把知识事实直接写入正式回复方案；`knowledge_web_search_enabled` 只是后续公开事实检索的预留开关。运行中心的 SHADOW trace 仅显示规范游戏名、实体/术语标签、版本指代、知识需要和限定诊断码，不显示置信分、作者引用或原始证据。

若需要立即停用整条知识链，将 `knowledge_enabled` 设为 `false` 并重启插件；此时 resolver、群知识观察服务和安装盐文件均不会启动或新建。`knowledge_web_search_enabled=false` 仅关闭后续联网能力，不影响当前本地预装语义。

人格档案按群独立发布，并与其他行为校准共用不可变 Config Version。认知周期开始时会冻结当时已发布的人格版本，运行过程中不会混用新旧设定。未发布自定义人格时使用插件自带的群聊伙伴默认档案。

## 群成员画像

Groupmate 会在消息可靠入库后，把画像观察放入独立后台队列；画像模型失败、超时或重试不会阻塞闲聊主链路。模型只提出结构化候选，本地证据策略决定事实是否确认并可用于回复。自述可以成为已确认事实，第三方转述默认不注入；行为模式和群友关系需要多次独立证据。旧认知可以被纠正、标记过时或失效，历史与审计仍保留。

成员可在群内使用以下精确指令，结果仅返回给指令发起者的当前上下文，不提供公开群排名：

- `查看我的画像`

成员侧画像目前仅开放查看。画像事实由有证据的后台流程更新；纠正、失效和后续画像卡片能力保留在受治理的管理入口，不开放群内成员自行修改。

管理员可在“群成员画像”页面查看每位成员的一句话画像、个体特征、偏好边界、代表经历与群友关系，并在折叠的“证据与审计”区域纠正或使错误事实失效。页面和 API 只返回作用域内的不透明 `member_ref`；原始 QQ/平台 ID、模型原始输出和跨群画像不会暴露。

### 群友说话风格与临时模仿

每位群友的风格蒸馏开关默认关闭，只能由平台管理员在“群成员画像”后台逐人开启。开启后只收集未来的合格群聊消息；至少需要 40 条合格消息、5 个活跃日和 3 类场景，才会调用独立模型形成定性风格版本。命令、转发、链接/媒体正文、复读、敏感或攻击内容和第三方话语不进入证据。

配置项 `control_admin_ids` 中的管理员可在当前群使用真实 `@爱弥斯` 发出明确请求，例如“`@爱弥斯 @阿甲 开始模仿到明晚八点`”。目标优先取同一消息里的真实 @；只有当前群的已确认昵称唯一时才允许用名字。每群同时最多一个会话，必须给出未来结束时间，单次最长 3 天；新请求会替换当前会话。被模仿者可以真实 `@爱弥斯` 说“别学我了”退出当前模仿，但不能更改蒸馏开关。

模仿只是爱弥斯的临时表达附层：不替换 Persona，不改变她的立场、关系、权限、能力与事实。精确命令结果、权限/安全提示和精确复读绕过该附层。启动确认本身是第一次试演，会明确目标、截止时间和“我还是爱弥斯”；模型不可用或身份检查失败时自动回退到普通爱弥斯表达。

## 模式与治理

- `OFF`：不处理群事件。
- `SHADOW`：运行认知和评估，但不发送或执行外部副作用。
- `SOCIAL_RUNTIME`：V2 在配置中所列的启用群内拥有决策与交付；由管理员在 AstrBot 原生插件配置中明确选择。

高影响命令仍必须通过服务端管理员作用域、原因、确认和 Expected Version 校验。Outbox `UNKNOWN` 禁止盲重试，外置插件拥有的请求不会被 Groupmate 抢答。

## 运维与验收

- [灾难恢复](docs/operations/social-runtime-disaster-recovery.md)
- [生产放量](docs/operations/social-runtime-rollout.md)
- [发布候选验收](docs/releases/social-runtime-v2-acceptance.md)
- [全新数据库切换与回滚](docs/operations/social-runtime-control-plane.md#全新数据库切换)

离线恢复使用 SQLite online backup、临时恢复、Event/Journal/Snapshot/Outbox 核对和 Projection rebuild。`SENT`/`UNKNOWN` 不重发。离线 `PASS_OFFLINE` 不等于真实 SHADOW、supervised、canary 或平台交付通过。
