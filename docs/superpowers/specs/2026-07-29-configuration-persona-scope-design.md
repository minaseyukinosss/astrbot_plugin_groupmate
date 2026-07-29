# Groupmate Configuration And Persona Scope Design（配置与人格状态隔离设计）

## 1. Goal（目标）

重新设计 Groupmate 的配置边界，删除已经不符合当前目标的旧配置、回退路线和隐式默认值，只向 AstrBot 管理员暴露必要的部署设置。同时为未来切换人格建立严格的 `persona_id`（人格标识）命名空间，确保爱弥斯与其他人格的称呼、关系、好感度、情绪、会话和记忆不会混用。

本设计遵循以下已确认原则：

- 目标行为来自用户行为、交互场景、人格和关系状态，不使用总体占比对应的随机概率控制；
- 当前人格继续是爱弥斯，只复现目标小维的行为机制，表达仍由爱弥斯人格决定；
- 管理配置先精简，不提前开放尚未成熟的高级调参；
- 旧机制不作为决策依据，也不保留回退路线；
- 现有爱弥斯好感度、关系和记忆不删除，迁移后归属 `aemeath`（爱弥斯人格标识）；
- 文档中的函数名、类名和配置键使用 `identifier（中文说明）`格式。

本设计取代旧设计文档中与下列内容有关的决策：

- AstrBot 对外配置项；
- `GroupPolicy`（混合群策略）的职责；
- `v3_*`（旧阶段回退开关）；
- `group_brief`（群氛围注入）；
- 旧本地反应素材目录；
- 无人格命名空间的关系、会话和记忆状态。

旧设计文档保留为历史记录，但不得再作为这些部分的当前实现依据。

## 2. Audit Result（残留审计结果）

当前旧配置不是单纯留在界面中，而是贯穿多个运行层：

1. `_conf_schema.json`（AstrBot 配置界面定义）仍展示旧字段；
2. `PluginSettings`（旧插件设置对象）仍解析旧字段并保留旧扁平键兼容；
3. `GroupPolicy`（混合群策略）同时保存部署配置、行为阈值、旧阶段开关和媒体路径；
4. `AstrBotBridge._policy_for`（群策略构建）仍把旧字段注入正式运行时；
5. `GroupActor`（群执行器）仍保留新旧调度分支；
6. `CognitiveWorkflow`（认知工作流）仍保留新旧编排和记忆写入分支；
7. `AemeathPersonaProvider`（爱弥斯人格提供器）仍接受 `group_brief` 并写入系统提示词；
8. `DEFAULT_RELATIONSHIPS`（硬编码默认关系）仍包含固定 QQ 关系；
9. `LocalReactionCatalog`（本地反应素材目录）仍连接正式回复流程；
10. Web 状态页、README、测试和评估适配仍引用旧字段。

`spontaneous_hourly_limit`（主动发言小时上限）和 `spontaneous_cooldown_seconds`（主动发言冷却）还有额外缺陷：当前正式回复流程会记录发送，却没有在开放场景发送前调用 `allow_send`（检查是否允许发送）。因此它们属于看起来可配置、实际没有形成发送门的伪配置。

## 3. Considered Approaches（方案比较）

### 3.1 Keep All Knobs（保留全部调参）

拒绝。它继续允许管理员绕过统一参与决策，通过阈值、阶段开关和全局长度直接改变行为，无法保证目标机制一致。

### 3.2 Basic And Advanced Configuration（基础与高级配置）

暂不采用。行为体系尚未全部稳定前开放高级配置，会让调试结果混入不同部署参数，无法判断差距来自机制还是配置。

### 3.3 Minimal Deployment Configuration（精简部署配置）

采用。AstrBot 只负责群范围、人格称呼与初始关系、模型供应商和视觉能力开关。参与决策、话题管理、回复模式和资源安全额度由代码内的类型化策略负责。

## 4. Public Configuration Contract（对外配置契约）

### 4.1 Exact Surface（精确配置面）

`_conf_schema.json`（AstrBot 配置界面定义）只保留以下 6 项：

| 配置键 | 中文说明 | 数据形态 | 默认语义 |
|---|---|---|---|
| `enabled_groups` | 启用群列表 | QQ 群号列表 | 空列表表示全部群启用 |
| `persona_aliases` | 分人格称呼配置 | `persona_id -> 称呼列表` | `aemeath` 默认含爱弥斯、小爱、飞行雪绒 |
| `relationships` | 分人格初始关系配置 | `persona_id -> 关系记录列表` | `aemeath` 默认为空，新用户从普通关系开始 |
| `generation_provider` | 文本生成模型 | Provider ID 字符串 | 留空跟随当前群聊模型 |
| `vision_enabled` | 图片理解开关 | 布尔值 | 默认启用 |
| `vision_provider` | 图片理解模型 | Provider ID 字符串 | 留空复用最终文本模型 |

当前只注册 `aemeath`（爱弥斯人格标识），配置界面只展示爱弥斯对应的称呼和初始关系。`active_persona`（当前人格选择）尚未具备真实切换能力，因此本阶段不公开。未来实现多人格切换时，可增加人格选择项，但不得改变现有的分人格数据形态。

建议的逻辑数据形态：

```yaml
enabled_groups: []
persona_aliases:
  aemeath:
    - 爱弥斯
    - 小爱
    - 飞行雪绒
relationships:
  aemeath: []
generation_provider: ""
vision_enabled: true
vision_provider: ""
```

`persona_aliases.aemeath`（爱弥斯称呼列表）允许完全编辑，也允许显式设为空。运行时不得偷偷补回“爱弥斯”；显式空列表意味着文本称呼不再触发，只能通过平台真实 @ 或回复机器人触发。状态页必须显示该风险。

### 4.2 Relationship Seed Semantics（初始关系语义）

`relationships`（分人格初始关系配置）只用于当前人格尚未建立关系状态的用户：

- 配置记录包含 `id`（QQ 号）、`relationship`（关系标签）和 `address`（建议称呼）；
- 新状态按关系标签取得初始好感度；
- 已存在的 `relationship_state`（关系状态）不因配置修改而被覆盖；
- 空配置不读取任何硬编码 QQ 默认关系；
- 新用户从普通关系和中性好感度开始。

### 4.3 Provider Resolution（模型解析优先级）

文本模型解析顺序固定为：

1. `generation_provider`（文本生成模型）非空时，强制使用该 Provider；
2. 只有配置为空时，才读取 AstrBot 当前群聊模型；
3. 显式 Provider 不存在或不可用时，本次模型生成失败并记录配置错误，不静默切换其他模型。

视觉模型解析顺序固定为：

1. `vision_enabled=false`（关闭图片理解）时，不发起视觉调用；
2. `vision_provider`（图片理解模型）非空时使用指定视觉 Provider；
3. 配置为空时复用已经解析完成的文本 Provider；
4. 视觉 Provider 不可用时只跳过图片理解，文字处理继续。

固定模板、复制文本 @ 提示和安全降级句不属于 Provider 回退；它们可以在不调用其他模型的前提下继续工作。

## 5. Configuration Boundary（配置职责边界）

### 5.1 `AstrBotConfigParser`（AstrBot 配置解析器）

新增或重命名为 `AstrBotConfigParser`（AstrBot 配置解析器），放在 `host`（宿主适配层）边界。它只负责：

- 从 AstrBot 传入值中提取 6 项白名单配置；
- 转换类型、去除空白、去重并验证 QQ 号；
- 区分“未提供称呼配置”和“显式空称呼列表”；
- 验证关系记录和同一人格下的重复 QQ 号；
- 收集并报告被忽略的旧键和未知键；
- 生成不可变的 `DeploymentSettings`（部署设置）。

它不是第二套配置存储，也不负责内部行为阈值。插件每次加载时仍由 AstrBot 提供原始配置。

### 5.2 `DeploymentSettings`（部署设置）

`DeploymentSettings`（部署设置）是 6 项对外配置的只读类型化结果。`AstrBotBridge`（AstrBot 适配层）和其他模块不得通过 `_setting`（通用动态取值）继续读取任意字段，也不得接收原始 `dict` 作为兼容输入。

### 5.3 Strict Parsing（严格解析）

删除 `flatten_plugin_config`（旧扁平配置兼容）。解析器使用明确的配置组和字段白名单：

- 合法旧字段不作运行时后备；
- 被删除字段只进入 `ignored_legacy_config`（已忽略旧配置）诊断；
- 非法群号、非法 QQ 号、重复关系记录和错误数据结构阻止插件进入可运行状态；
- 空称呼列表允许加载，但产生警告；
- AstrBot 原始配置文件不由插件主动修改或删除。

## 6. Runtime Policy Architecture（运行策略架构）

删除 `GroupPolicy`（混合群策略），用明确职责的内部策略替代：

### 6.1 `ParticipationPolicy`（参与策略）

负责场景参与、直接呼叫压力、好感姿态和开放场景抑制。重复 @ 时间窗口与姿态阈值属于这里，不从 AstrBot 配置读取。

### 6.2 `ConversationPolicy`（对话策略）

负责历史窗口、话题周期、去抖、候选有效期和连续对话许可。续聊时间属于这里，不从 AstrBot 配置读取。

### 6.3 `ReplyPolicy`（回复策略）

负责 `ReplyMode`（回复模式）对应的长度、分段、引用和发送结构。删除全局 `max_reply_chars`（最大回复长度）；不同场景由回复模式决定合适长度。

### 6.4 `ResourcePolicy`（资源安全策略）

负责文本生成、视觉调用和开放场景发送的硬安全额度：

- 场景与人格先完成参与决策；
- 只有 `SPEAK`（发言）决策进入资源检查；
- 开放场景发送前必须调用 `allow_send`（检查发送额度）；
- 直接 @、回复机器人等 `DIRECT_REQUIRED`（明确回应义务）不被开放场景发送额度误伤；
- 资源策略只能否决过密发送或昂贵调用，不能以概率制造参与机会；
- 具体额度是内部实现常量，后续只能依据评估数据调整，不进入本阶段管理配置。

这些策略可由 `BehaviorPolicy`（内部行为策略集合）聚合并注入运行时，但不得重新形成一个可读取任意 AstrBot 配置的混合对象。

## 7. Persona Architecture（人格架构）

### 7.1 `PersonaRegistry`（人格注册表）

`PersonaRegistry`（人格注册表）按稳定 `persona_id` 注册人格资料。当前只包含：

```text
persona_id = aemeath
display_name = 爱弥斯
```

人格正式名称、提示词、表达习惯和参与倾向属于 `PersonaProfile`（人格资料），不是插件全局称呼配置。

### 7.2 `PersonaContext`（当前人格上下文）

每个事件进入 Groupmate 时必须先解析唯一 `PersonaContext`（当前人格上下文），至少包含：

- `persona_id`（人格标识）；
- `display_name`（正式名称）；
- `aliases`（当前人格称呼）；
- `relationship_seeds`（当前人格初始关系）；
- `participation_profile`（人格参与倾向）；
- `prompt_profile`（人格提示词资料）。

后续参与、会话、记忆、回复和投递接口必须显式接收或持有该上下文。缺少 `persona_id` 的有状态接口不得保留隐式默认值。

### 7.3 State Partition（人格状态划分）

以下状态必须按 `persona_id + group_id + user_id` 或相应人格范围隔离：

- `persona_aliases`（称呼）；
- `relationships`（初始关系）；
- `affinity`（好感度）；
- `emotional_pressure`（情绪与边界压力）；
- `continuation_grant`（连续对话许可）；
- `conversation_session`（对话会话）；
- `recent_outputs`（近期回复）；
- `persona_memory`（人格参与形成的记忆）。

运行记录、消息投递凭证和防重复发送记录可以统一保存，但每条记录必须带 `persona_id`（人格来源）。人格上下文检索只能读取当前人格的数据。

切换人格时：

- 清理当前群短期话题窗口和运行中候选；
- 不延续旧人格的续聊许可、近期输出和直接呼叫压力；
- 不读取旧人格的关系、好感度和记忆；
- 切回原人格时恢复该人格自己的长期状态。

## 8. Database V11 Migration（数据库第11版迁移）

### 8.1 Backup And Transaction（备份与事务）

`SCHEMA_VERSION`（数据库结构版本）从 10 升到 11。迁移开始前使用 SQLite backup API 生成：

```text
groupmate.db.pre-migrate-v10-to-v11.<timestamp>
```

正式结构变化在单个 `BEGIN IMMEDIATE`（立即事务）中完成。任何建表、复制、计数验证、索引验证或提交失败都必须回滚。迁移失败后插件停止启动，不允许使用半迁移数据库继续运行。

空数据库直接通过 `_bootstrap_v11`（创建第 11 版新数据库）建立最终结构，不再先创建包含 `favorability`（旧好感度表）等历史结构的 v5 数据库再逐级迁移。已有 v5 至 v10 数据库继续沿用可验证的增量迁移链，最终统一进入 v11；每个非空旧数据库都必须在升级前备份。

### 8.2 Existing Data Ownership（现有数据归属）

所有 v10 现有记录迁移为：

```text
persona_id = "aemeath"
```

该默认值只允许出现在 v10 到 v11 的迁移语句中。v11 正常写入接口不接受空人格，也不得将缺失人格自动解释为爱弥斯。

### 8.3 Persona-Scoped Tables（人格隔离表）

下列表增加非空 `persona_id`，所有人格状态查询必须包含该字段：

- `messages`（消息记录）；
- `profiles`（成员资料）；
- `memories`（已接受记忆）；
- `decisions`（决策轨迹）；
- `outbox`（发送凭证）；
- `topic_epochs`（话题周期）；
- `continuation_grants`（连续对话许可）；
- `social_events`（社交事件）；
- `relationship_state`（关系状态）；
- `memory_candidates`（候选记忆）；
- `memory_tombstones`（记忆删除标记）。

语义自然键和语义唯一约束必须扩展到人格维度，例如：

- `messages` 使用 `persona_id + group_id + message_id`；
- `profiles` 使用 `persona_id + group_id + subject_id`；
- `relationship_state` 使用 `persona_id + group_id + user_id`；
- `social_events` 的来源消息唯一约束包含 `persona_id`；
- `memory_candidates` 和 `memory_tombstones` 的声明唯一约束包含 `persona_id`。

`decisions.id`（决策流水号）、`memory_id`（记忆 UUID）、`decision_id`（决策 UUID）和 `grant_id`（续聊许可 UUID）等全局唯一代理键可以保持原主键形态，但对应表必须保存 `persona_id`，查询索引与读取语句必须包含人格范围。人格隔离不依赖调用方碰巧生成不重复 UUID。

SQLite 不能直接修改已有主键、非空约束和唯一约束，因此使用 `create-copy-verify-swap`（新建、复制、验证、替换）方式重建所有新增人格字段的表，避免永久保留 `DEFAULT 'aemeath'`（默认爱弥斯）并让未来缺失人格的写入悄悄成功：

1. 创建带 v11 约束的新表；
2. 使用 `INSERT ... SELECT 'aemeath', ...` 复制旧数据；
3. 验证旧表与新表行数、关键非空字段和唯一约束；
4. 删除旧表并将新表改为正式名称；
5. 重建人格范围索引；
6. 更新 `schema_meta.version`（数据库版本记录）后提交。

`favorability`（旧好感度表）在确认其内容已经迁入 `relationship_state` 后删除，不再保留第二份好感度来源。

### 8.4 State Access Contract（状态访问契约）

所有状态存储方法需要显式 `persona_id` 参数，例如：

```python
get_relationship_state(persona_id, group_id, user_id)
search_memories(persona_id, group_id, query, ...)
grant_continuation(persona_id=..., group_id=..., sender_id=..., ...)
list_ledger_messages(persona_id, group_id, ...)
```

不得保留不带人格参数的兼容重载。测试替身和评估运行器也必须使用相同契约，防止生产代码完成隔离而测试仍绕过人格范围。

## 9. Legacy Removal（旧路线删除）

### 9.1 Remove Completely（完全删除）

以下配置、字段、分支和对应当前文档说明全部删除：

- `handle_native_wake`（原生唤醒接管开关）；
- `group_brief`（群氛围说明与提示词注入）；
- `max_reply_chars`（全局回复长度配置）；
- `spontaneous_hourly_limit`（主动发言小时上限配置）；
- `spontaneous_cooldown_seconds`（主动发言冷却配置）；
- `continuation_seconds`（连续对话时限配置）；
- `direct_pressure_window_seconds`（直接呼叫压力窗口配置）；
- `direct_pressure_nudge_count`（轻戳阈值配置）；
- `direct_pressure_pester_count`（纠缠阈值配置）；
- `v3_scheduler_enabled`（旧调度回退开关）；
- `v3_memory_writer_enabled`（旧记忆写入回退开关）；
- `v3_composition_enabled`（旧编排回退开关）；
- `reaction_media_enabled`（旧反应素材开关）；
- `reaction_catalog_path`（旧反应素材目录）；
- `DEFAULT_RELATIONSHIPS`（硬编码 QQ 默认关系）；
- `flatten_plugin_config`（旧扁平配置兼容）；
- `GroupPolicy`（混合群策略）；
- 调度器、记忆写入和回复编排的旧执行分支。

`continuation_seconds` 和 `direct_pressure_*` 对应的行为能力不删除，而是改为 `ConversationPolicy`（对话策略）和 `ParticipationPolicy`（参与策略）的内部规则。旧配置键与旧读取路径必须删除。

### 9.2 Reaction Media Boundary（反应素材边界）

删除当前 `LocalReactionCatalog`（本地反应素材目录）、`ReactionPolicy`（旧反应素材策略）及其正式工作流选择逻辑。保留通用的能力结果媒体、图片发送段、投递凭证和稳定媒体 ID 契约，因为它们不等同于旧表情包系统，未来独立开发爱弥斯表情包时仍可复用。

`VISUAL_REACTION`（视觉回应动作）可以继续表示“对图片内容作相关文字回应”，但在新表情包系统完成前不得从旧本地目录选择装饰图片。

### 9.3 Current Documentation（当前文档）

- README 只说明 6 项当前配置；
- Web 状态页删除群氛围、全局字数、旧调度名和主动发言配置展示；
- 历史规格保留，但本设计声明的覆盖关系优先；
- 实施计划和新增测试继续使用 `identifier（中文说明）`格式。

## 10. Runtime Data Flow（运行时数据流）

```text
AstrBotEvent（AstrBot事件）
    -> HostAdapter（宿主适配）
    -> PersonaContext（当前人格上下文）
    -> GroupRuntime（群运行时）
    -> ParticipationPolicy（参与决策）
    -> ConversationPolicy（上下文与续聊）
    -> ReplyPolicy（回复模式与爱弥斯表达）
    -> ResourcePolicy（资源安全检查）
    -> DeliveryService（发送与防重复）
```

运行时硬约束：

- 事件进入 Groupmate 时就确定唯一 `persona_id`；
- 用户行为、场景、人格和关系先决定参与，不使用概率控制不同特征占比；
- 好感度与重复 @ 压力共同影响爱弥斯的回应姿态；
- 复制文本 @ 保持独立固定处理，不进入参与决策、好感度、压力或续聊；
- 当前人格的模型提示词只能读取当前人格状态；
- 新调度、新编排和记忆写入是唯一正式路线。

## 11. Error Handling And Status（错误处理与状态）

### 11.1 Configuration Errors（配置错误）

- 非法群号、非法 QQ 号、重复关系记录、错误映射结构：拒绝进入运行状态，并给出字段路径；
- 空称呼列表：允许加载，状态为警告；
- 被删除的旧键：忽略并记录 `ignored_legacy_config`；
- 真正未知的键：记录 `unknown_config_key`（未知配置键），不注入运行时。

### 11.2 Provider Errors（模型错误）

- 显式文本 Provider 不可用：保持观察能力，生成状态标记为不可用，不切换其他模型；
- 当前群聊 Provider 在跟随模式下不可用：当前群生成失败并记录群级原因；
- 视觉 Provider 不可用：当前视觉任务跳过，文字流程继续。

### 11.3 Status Surface（状态输出）

`groupmate_status`（运行状态命令）和 Web 状态页至少显示：

- `active_persona`（当前人格）；
- `enabled_scope`（全部群或指定群）；
- `alias_count`（当前人格称呼数）；
- `relationship_seed_count`（当前人格初始关系数）；
- `generation_provider_mode`（固定模型或跟随群聊）；
- `vision_status`（视觉配置状态）；
- `database_schema`（数据库版本）；
- `config_health`（配置健康状态）；
- `ignored_legacy_keys`（被忽略旧键）。

状态输出不得回显原始配置字典、敏感 Provider 凭据或用户完整关系内容。

## 12. Verification（验证要求）

### 12.1 Configuration Tests（配置测试）

- `_conf_schema.json` 精确包含 6 项配置；
- `DeploymentSettings` 只包含对应字段；
- 空 `enabled_groups` 表示全部群；
- `persona_aliases` 按人格解析并保留显式空列表；
- `relationships` 按人格解析，拒绝非法和重复 QQ；
- 旧键被诊断但不影响结果；
- 生产代码不再调用 `_setting` 或读取被删除字段。

### 12.2 Provider Tests（模型解析测试）

- 显式文本 Provider 始终优先；
- 空文本 Provider 才跟随群聊；
- 空视觉 Provider 复用最终文本 Provider；
- 文本 Provider 失败不静默回退；
- 视觉失败不阻断文字流程。

### 12.3 Persona Isolation Tests（人格隔离测试）

- 同一群和用户在两个 `persona_id` 下具有独立关系、好感度和边界压力；
- 两个人格不能检索彼此记忆、会话、近期输出和续聊许可；
- 切换人格清除短期窗口，切回后恢复该人格长期状态；
- 所有状态访问缺少 `persona_id` 时在接口层失败。

### 12.4 Migration Tests（迁移测试）

- v10 数据库迁移前生成完整备份；
- 空数据库直接创建 v11，已有 v5 至 v10 数据库均能安全进入 v11；
- 所有旧行被标记为 `aemeath`，行数和关键字段不丢失；
- 主键、唯一约束和索引包含人格范围；
- `favorability` 迁移完成后消失；
- 故意制造复制或验证错误时事务回滚，原数据库仍可用；
- 已是 v11 的数据库重复打开不产生重复迁移。

### 12.5 Behavior Regression（行为回归）

- 真实 @、句首称呼、回复机器人和有效续聊仍可靠处理；
- 复制文本 @ 仍使用爱弥斯固定提示，且不污染好感度、压力和续聊；
- 低好感过度 @ 表现为疏离或边界，高好感过度 @ 可以戏谑提醒；
- 开放场景仍由统一参与引擎按场景决定；
- 开放场景发送前真正执行 `allow_send`，直接回应不被开放限额误伤；
- 旧调度、旧编排、旧记忆关闭路径和旧本地反应素材无法再执行；
- 完整单元测试、集成测试、确定性评估和影子评估通过。

## 13. Non-Goals（本阶段不做）

- 不公开 `active_persona`（人格切换配置）；
- 不开发新爱弥斯表情包系统；
- 不开放行为阈值和资源额度高级配置；
- 不重新调节统一参与引擎的场景规则；
- 不删除已经积累的爱弥斯关系和记忆；
- 不推送远端仓库。

## 14. Acceptance Criteria（验收标准）

完成实施后必须满足：

1. AstrBot 管理界面只有确认的 6 项配置；
2. 旧配置即使仍留在 AstrBot 原始存储中，也不能改变任何运行行为；
3. `GroupPolicy`、旧回退分支、群氛围注入、硬编码 QQ 关系和旧本地反应素材目录从生产路径消失；
4. 配置、人格资料、内部行为策略和持久状态职责清晰分离；
5. 当前 v10 数据安全迁移为 `aemeath` 人格范围的 v11 数据；
6. 新人格状态可以与爱弥斯完全隔离；
7. Provider 优先级和失败行为确定、可诊断且无静默回退；
8. 目标群聊行为回归测试和确定性评估没有退化。
