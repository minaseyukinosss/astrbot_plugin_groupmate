# Groupmate 共享游戏知识认知层与版本时效设计

- 状态：三轮架构审查通过，已进入实施计划
- 初稿日期：2026-08-27
- 修订日期：2026-08-28
- 适用范围：Groupmate Social Runtime v2 的群聊理解、知识积累、时效核验、回复生成和控制面

## 1. 最终结论

Groupmate 增加的不是一个附加在回复 Prompt 末尾的百科检索器，而是一层贯穿群聊理解、参与判断、事实核验和表达审查的知识认知层。

这层系统遵守以下边界：

1. 稳定游戏语义和公开世界事实按安装实例共享，不按 Persona 或群隔离。
2. 群内热门游戏、外号、梗和特殊指代按 group_id 隔离，但仍不按 Persona 重复存储。
3. 成员偏好、经历和关系继续由现有 Member Profile 与关系系统管理。
4. 当前会话中的临时指代只存在于场景或对话租约中，不自动沉淀为公共知识。
5. 群聊只能产生知识观察和群内约定候选，不能凭重复次数把传言变成公共事实。
6. 版本、日期、角色清单、活动和数值等高风险事实必须来自新鲜证据，并通过确定性事实片段进入回复。
7. 搜索失败、来源冲突、场景过期或知识过期时失败关闭，不借用模型内在知识猜测。

首版预装三角洲行动、鸣潮、崩坏：星穹铁道和绝区零的稳定语义包。四款游戏的官方版本状态以每 24 小时完成一次成功核验为目标；失败任务只重试和上报降级，不伪造成功时间。原神不预装，但可以由各群按普通长尾游戏持续学习。用户提到新版本、前瞻、爆料、刚更新等时效表达时，根据参与通道执行即时核验。

## 2. 要解决的问题

当前 Groupmate 可以理解群聊事件、成员画像、关系记忆和 Persona Canon，但缺少外部领域语义与可验证事实。这会产生四种不同问题：

- 不知道某个词属于哪款游戏；
- 知道游戏名称，但听不懂角色简称、机制、抽卡或竞技黑话；
- 理解用户在问版本，却使用旧资料或模型记忆回答；
- 在群里多次看到一种说法后，把群梗、Bot 输出或传言误当成公共事实。

因此，成功标准不是“搜索得到内容”，而是：

- 参与判断前能正确理解高频游戏话题；
- 能积累自己的知识，同时保留来源、范围、时间和纠正链；
- 能区分群内约定、非官方信息和官方事实；
- 时效证据不足时不生成确定性事实；
- 知识系统不会让 Groupmate 更频繁、延迟更高或更爱插话。

## 3. 必须保留的当前约束

1. AMBIENT 参与判断有 8 秒总预算，不能对所有消息联网。
2. 外部插件命令已有明确所有权，不得被知识系统抢答或拿去学习。
3. 成员画像和关系记忆有隐私作用域，不能成为公共百科数据。
4. Governor 仍是是否行动的唯一授权边界，知识命中不能强迫 Bot 参与。
5. ReplyPlan、结构化生成、审查、事务 Outbox 和场景过期语义继续生效。
6. SQLite 是权威持久化，迁移使用现有 Social Runtime 的加法式 schema migration。
7. SHADOW 是新知识能力进入正式发送前的必经阶段。

## 4. 四层知识模型

### 4.1 稳定游戏语义 GameSemanticKnowledge

作用域：安装实例全局。

用途：让 Bot 听懂游戏聊天，而不是回答当期事实。

内容包括：

- 游戏、角色、阵营、模式、地图、装备、资源和机制实体；
- 官方名称、翻译、简称、常见外号及歧义条件；
- 游戏系统之间的基础关系；
- 高频社区术语、典型语境和语气边界；
- 版本无关的普通玩家对话结构；
- 官方来源域名和公告入口。

这层不保存当前卡池、当期活动、最新数值和当前版本结论。

### 4.2 公开事实 PublicWorldKnowledge

作用域：安装实例全局。

用途：支持可外部核对的事实回答。

每条事实必须包含：

- 规范实体；
- 谓词与安全摘要；
- 来源证据；
- 官方、二手或非官方分类；
- 生效与失效时间；
- 适用版本、区服、地区和平台；
- 当前状态与替代关系；
- 最近成功核验时间。

### 4.3 群内约定 GroupConventionKnowledge

作用域：group_id，不带 persona_id。

用途：理解本群的外号、梗、特殊简称和习惯表达。

它可以影响：

- 实体解析；
- 语义消歧；
- 群话题排序；
- 表达时是否自然采用本群叫法。

它不能：

- 改变公共事实真值；
- 跨群复用；
- 被表述为官方名称；
- 因为高频就自动晋升为公共知识。

### 4.4 当前会话理解 ConversationUnderstanding

作用域：当前 AttentionFrame、SocialScene 或 ConversationLease。

用途：解释本轮临时指代，例如：

- “新版本”是刚上线版本还是下一版本；
- “那个角色”指当前对话中的谁；
- “又歪了”是在说抽卡结果还是玩梗；
- 当前是在求证、吐槽、讨论爆料还是询问官方消息。

这层默认不持久化。只有其中的未知实体、未知术语或群内用法会生成后台知识观察。

## 5. TopicUnderstandingFrame

本地解析器在模型参与判断前生成有界 TopicUnderstandingFrame：

    frame_id
    game_ids
    resolved_entities
    resolved_terms
    discourse_referents
    version_reference
    conversation_intent_hint
    ambiguity_codes
    confidence
    supporting_knowledge_ids

约束：

- 只使用有效 seed、已激活全局知识、当前群约定和当前场景文本；
- conversation_intent_hint 只保存由明确词形和已知术语得到的提示，可以为空；最终话语意图仍由现有认知与场景模型判断；
- 不能因为群热度把不相关消息强行归入某个游戏；
- 歧义简称没有足够上下文时保持 unresolved；
- 不携带网页正文、内部评分、成员私密事实或 Persona Prompt；
- Prompt 打包使用固定条目数与字符预算。

TopicUnderstandingFrame 同时进入：

1. CognitiveContext.world_summary，帮助 DirectAmbientWorker 判断是否值得参与；
2. SceneContext，帮助 SocialSceneInterpreter 识别具体话语行为；
3. 知识需求评估，判断本轮是否需要事实核验。

## 6. 总体组件

### 6.1 KnowledgeObservationService

在消息可靠入库并完成交互所有权解析后，异步记录合格知识观察。它沿用 ProfileService 的持久队列、批处理、重试、健康状态和不阻塞主链路模式，但使用独立知识作用域与准入规则。

### 6.2 KnowledgeEntityResolver

使用全局语义包、群内约定和当前会话解析实体与术语，输出 TopicUnderstandingFrame，不直接写事实。

### 6.3 KnowledgeRetriever

从有效知识投影中召回少量相关记录，排序考虑：

- 当前文本与实体匹配；
- 群话题热度；
- 证据等级；
- 版本适用性；
- 时效；
- 冲突和失效状态。

### 6.4 KnowledgeNeedAssessor

输出：

    none
    local_sufficient
    fresh_evidence_required
    background_learning
    unresolvable

它同时输出缺口类型、实体、时效风险、建议查询和核验理由，不生成回复正文。

### 6.5 OfficialSourceProbePort

检查 seed 中登记的官方公告、更新说明和运营来源。每日版本任务优先使用该端口。

### 6.6 DiscoverySearchPort

通过 AstrBot 已配置的网页搜索与页面提取工具发现新来源。Groupmate 不保存厂商 API Key，不使用 AstrBot HTTP 自调用，也不把工具交给最终回复模型。

首个适配器使用 AstrBot 的显式 ToolSet 和专用检索 Agent，只允许当前搜索源与页面提取工具，最大步骤、结果数和超时均固定受限。

### 6.7 KnowledgeAdmissionPolicy

把 seed、群观察、官方页面和搜索结果转换为候选观察，执行：

- 来源分类；
- 实体归一；
- 去重与哈希；
- 时间范围解析；
- 版本绑定；
- 冲突检查；
- 群内或全局作用域判定；
- 激活、争议、替代或拒绝。

### 6.8 KnowledgeEnrichmentCoordinator

在 Governor 已选择 ACT 后执行必要的事实补全。它不运行在当前全局回复锁内部，而是使用：

- 按 group_id 的 enrichment lane；
- 安装实例级搜索并发门；
- 相同实体和查询的 single-flight；
- 小时和每日额度；
- 知识提交单写者；
- 搜索后场景重验证。

### 6.9 GameReleaseStateService

管理版本槽位、发布轨道、官方披露轨道、非官方信息轨道和相对版本解析。

### 6.10 GroundedReplyPipeline

把冻结知识快照转成 SocialMove 可使用的知识事实，区分严格事实片段和可语义转述事实，并在入 Outbox 前完成审查。

## 7. 同步运行链路

    消息可靠入库与交互所有权解析
    → 本地实体、术语和版本指代解析
    → TopicUnderstandingFrame
    → 当前 Cognition / Participation / Governor

    若 OBSERVE：
      → 不即时联网
      → 记录合格实体提及和未知术语
      → 必要时建立后台学习任务

    若 ACT：
      → KnowledgeNeedAssessor
      → 本地知识足够则冻结 KnowledgeSnapshot
      → 时效证据不足则进入 KnowledgeEnrichmentCoordinator
      → 官方来源探测
      → 必要时发现性搜索
      → 证据准入
      → 重验 scene_version、意图到期时间与知识时效
      → SocialScene / Stance / SocialMove
      → ReplyPlan
      → 严格事实片段组装与自然表达
      → 社交审查 + 知识审查 + OutputFirewall
      → Outbox

任何知识步骤都不能绕过 Governor、改变能力所有权或直接发送消息。

## 8. 参与通道与延迟策略

### 8.1 DIRECT_FAST

- 直接询问确定需要回复；
- 稳定知识本地命中时不联网；
- 时效问题允许即时核验，硬超时 5 秒；
- 搜索失败使用固定承认未确认语义；
- 整个过程仍受 ReplyPlan 意图有效期约束。

### 8.2 CONTINUATION

- 复用当前 ConversationLease 的实体与版本指代；
- 可复用 10 分钟内相同实体的成功即时核验结果；
- 允许最长 5 秒即时核验；
- 搜索后必须确认租约仍然有效且目标未变化。

### 8.3 AMBIENT

- 参与判断前只使用本地 TopicUnderstandingFrame，不等待网页；
- 已有新鲜本地知识时可以正常参与；
- 时效知识过期时，只有搜索能在剩余 Attention 和意图时间内完成才允许继续；
- AMBIENT 即时搜索硬超时 2 秒；
- 超时、排队、场景前进或证据不足时沉默，同时保留后台刷新任务。

### 8.4 不参与和外部能力消息

- 普通 OBSERVE 消息不联网；
- 外部插件命令、精确管理命令和 social_eligible=false 消息不搜索、不学习命令正文；
- Groupmate 自身输出不成为知识观察；
- 转发、卡片和其他 Bot 输出默认只能成为搜索线索，不能强化事实或群内约定。

## 9. 持久化模型

### 9.1 knowledge_observations

不可变知识观察账本：

- observation_id
- origin_class：seed、human_chat、own_output、external_bot、unknown_actor、command、forward、official_page、search_result、admin
- scope_kind：global、group
- group_id：仅 group scope 有值
- author_ref：仅合格群观察使用的群内不透明作者引用，不保存平台原始 ID
- source_event_id
- source_id
- entity_hint
- safe_summary
- content_hash
- occurred_at
- recorded_at
- status：pending、admitted、rejected、expired

观察记录不使用 persona_id 作为知识隔离键。来自 Social Runtime 的观察通过 source_event_id 保留因果关系。

### 9.2 knowledge_entities 与 knowledge_aliases

knowledge_entities：

- entity_id
- entity_type
- canonical_name
- canonical_game_id
- status
- created_at
- updated_at

knowledge_aliases：

- alias_id
- entity_id
- normalized_alias
- alias_kind
- ambiguity_level
- source_id
- status

group_knowledge_aliases 额外带 group_id、证据观察、置信度和最后使用时间。

### 9.3 knowledge_claims

- claim_id
- subject_entity_id
- predicate
- safe_summary
- claim_kind：stable_semantic、public_fact、rumor
- evidence_level：bundled、official、corroborated、secondary、unofficial
- status：pending、active、stale、superseded、rejected、disputed
- applies_to_version_slot_id
- region
- platform
- valid_from
- valid_until
- checked_at
- supersedes_claim_id
- created_at
- updated_at

knowledge_sources 保存规范 URL、域名、发布者、来源类别、发布时间、获取时间、内容哈希和短证据片段。knowledge_claim_evidence 建立声明、来源和观察之间的关系。

### 9.4 group_conventions

- convention_id
- group_id
- normalized_expression
- resolved_entity_id
- meaning_summary
- evidence_observation_ids
- distinct_actor_count
- distinct_scene_count
- confidence
- status：candidate、active、stale、rejected
- first_seen_at
- last_seen_at
- updated_at

### 9.5 group_topic_affinity

这是投影，不是事实来源：

- group_id
- entity_id
- qualified_mention_count
- distinct_actor_count
- distinct_scene_count
- salience
- first_seen_at
- last_seen_at
- updated_at

salience 使用 7 天半衰期。单个账号、Bot 输出、命令刷屏和转发不能单独把游戏推成群热门。

### 9.6 game_release_states

- version_slot_id
- game_entity_id
- official_label：可空
- region
- platform
- release_state：future、current、past
- official_state：none、teaser、preview、notice、released
- rumor_state：none_observed、weak、corroborated、conflicted、stale
- announced_at
- release_at
- effective_until
- official_checked_at
- rumor_checked_at
- fresh_until
- status：active、superseded、disputed

### 9.7 knowledge_jobs 与 knowledge_usage

knowledge_jobs 持久化：

- seed 导入；
- 官方日常核验；
- 即时事实补全；
- 未知实体后台学习；
- 群热门预热；
- 时间边界重验证；
- 纠正和失效投影重建。

状态为 pending、running、retry、completed、discarded。进程重启后恢复未完成任务。

knowledge_usage 记录本轮召回和使用的知识 ID、来源域名、耗时、缓存命中、搜索结果类型和诊断码，不保存网页正文、完整查询、Prompt 或模型原始输出。

## 10. 持续学习准入

### 10.1 来源分类

只有 origin_class=human_chat 且满足以下条件的消息可以强化群内约定：

- 不是 Groupmate 自己；
- 不是已知自动化账号或外部 Bot；
- 不是命令或命令结果；
- 不是转发内容；
- 不是纯链接、卡片摘要或媒体自动解析；
- 能绑定当前群和真实场景；
- 证据文本在安全裁剪后仍包含实际用法。

无法确认是否为人类自然发言时，只建立低信任观察，不参与自动激活。

### 10.2 群内约定激活

满足任一条件可以从 candidate 进入 active：

1. 一名成员明确解释其含义，之后另一独立场景出现一致用法；
2. 至少两个不同成员在三个独立场景中使用一致；
3. 管理员基于可见证据明确确认。

90 天没有再次使用的约定进入 stale，但历史和证据保留。

### 10.3 公共知识晋升

- 群聊重复不能直接晋升；
- 一条官方来源可激活它明确支持的声明；
- 非官方稳定事实需要至少两个独立且不冲突的可靠来源；
- 社区术语可以作为 community semantic 激活，但不得表述为官方；
- rumor 始终保留 unofficial 身份；
- 官方后来证实爆料时新建 official claim，并关联旧 rumor，不修改旧来源类别。

### 10.4 纠正与冲突

- 新官方公告可以 supersede 旧官方声明；
- 正式实装优先于测试服和爆料数值；
- 同等级来源冲突时进入 disputed；
- 群约定出现相反定义时降级为 candidate 或 disputed；
- 管理员纠正生成新的审计观察，不物理删除历史。

## 11. 四款预装游戏语义包

首版强制预装：

1. 三角洲行动
2. 鸣潮
3. 崩坏：星穹铁道
4. 绝区零

原神不属于预装范围。已有安装升级时，系统将旧原神 seed、由该 seed 投影出的全局 alias、版本状态和未完成任务标记为退休；群聊观察、群 alias 和后续学习事实不随之删除。

每个包是带 seed_id、seed_version 和 content_hash 的版本化资产，通过幂等导入器写入。

包内包含：

- 官方名称、英文名、简称和歧义规则；
- 核心游戏类型与玩法结构；
- 高频角色、阵营、模式、地图、装备、资源和系统类型；
- 高频社区术语的基础意义、常见语境和非官方标记；
- 版本无关的讨论模式，例如抽卡、配队、强度、活动、地图、竞技和更新；
- 官方公告、版本说明和运营来源注册表。

包内禁止包含：

- 当前卡池和当期角色；
- 当前版本结论；
- 正在进行的活动；
- 最新数值或测试服数值；
- 强度榜和自动攻略结论；
- 未经验证的爆料。

升级 seed 时，较新的官方验证声明优先；seed 只能替代同源旧 seed，不能覆盖后续已验证知识。

## 12. 版本与时效模型

### 12.1 三条并行轨道

版本状态不是一条 unofficial 到 official 的线性链：

    release_state:
    future → current → past

    official_state:
    none → teaser → preview → notice → released

    rumor_state:
    none_observed / weak / corroborated / conflicted / stale

official_state 与 rumor_state 可以同时存在。非官方信息不会因为转发次数增加或后来被证实而变成官方来源。

### 12.2 相对版本解析

以下表达必须经过 GameReleaseStateService：

- 新版本、下版本、下期、这期；
- 最近更新、刚改、现在、已经实装；
- 新角色、新地图、新赛季、新武器、新卡池；
- 爆料、前瞻、测试服、官方公告。

解析使用消息时间、游戏、区服平台、对话时态、当前版本槽位和上下文。

无法唯一解析时：

- DIRECT 或 CONTINUATION 只问一个消除歧义的问题；
- AMBIENT 不自行选择版本，可沉默；
- 不根据当前版本号自动加一推测下一版本标签。

### 12.3 每日刷新

- 四款 seed 游戏始终 baseline_active；
- 每个游戏的官方轨道以每 24 小时完成一次成功核验为目标；
- 使用 Asia/Shanghai 日期边界和有界抖动分散请求；
- 失败不更新 official_checked_at 和成功刷新时间；
- 到达已验证 release_at 时建立时间边界重验证任务；
- 其他游戏只有成为群热门或被再次提及时恢复核验。

日常任务只主动核验官方信息，不主动抓取、整理或传播爆料。

### 12.4 即时核验

用户明确提及新版本、前瞻、爆料、刚更新等时：

- 不使用每日成功状态豁免；
- 先探测官方来源；
- 用户语境涉及爆料时再执行非官方发现搜索；
- 相同实体和意图的成功结果 10 分钟内复用；
- 新官方证据立即使旧 negative search snapshot 失效。

### 12.5 负向搜索语义

空搜索结果不能证明没有信息。只有 Provider 完整成功、官方来源策略成功执行且查询覆盖目标语义时，才能建立 negative_search_snapshot。

它只允许表达：

- “截至刚才，官方渠道还没看到明确消息”；
- “这次暂时没检索到可靠公开爆料”。

它不允许表达：

- “官方确定没有计划”；
- “网上完全没有爆料”；
- “下一版本不存在”。

搜索超时、限流、配置缺失或部分失败只能生成诊断，不能生成负向快照。

## 13. 回复根据与不编造

### 13.1 KnowledgeSnapshot

每次准备回复时冻结：

- snapshot_id
- topic_understanding_frame_id
- allowed_knowledge_facts
- strict_fact_fragments
- source_ids
- checked_at
- expires_at
- version_state_revision

后台更新不能在生成中途改变本轮快照。

### 13.2 SocialMove 与 ReplyPlan 扩展

SocialMovePlan 增加：

- knowledge_policy：none、grounded、strict
- must_use_knowledge_ids
- may_use_knowledge_ids
- prohibited_assertion_classes

ReplyPlan 保存 KnowledgeSnapshot 引用和版本修订。

covered_fact_ids 继续表示社交动作事实；知识 ID 使用独立字段，不与社会证据 ID 混合。

### 13.3 高风险事实确定性表达

以下内容使用 strict：

- 版本号和版本状态；
- 发布日期和时间；
- 角色、武器、地图、活动清单；
- 数值、概率、伤害和改动；
- 官方确认、没有官方消息和爆料状态。

KnowledgeFactRenderer 生成不可由模型改写内容的安全事实片段。生成模型只能选择允许片段并生成不包含新事实的语气连接文本，最终由本地 assembler 组装。

模型不能在片段外新增版本号、专有名词、日期、数量和状态结论。

strict 模式的模型输出不是完整自由文本，而是有界 parts 数组：

    parts:
      - kind: text
        text: 不含外部事实的语气连接文本
      - kind: knowledge_fragment
        fragment_id: 只能复制允许的 strict fragment ID

本地 assembler 校验顺序、必选片段、字符预算和重复引用后再替换 fragment_id。模型看不到可伪造的 URL，也不能把 text part 当成新的事实通道。

稳定背景知识可以使用 grounded 模式自然转述，但必须声明使用的知识 ID。

### 13.4 GroundedReplyReviewer

确定性检查：

- 所有知识 ID 属于冻结快照；
- required 知识没有遗漏；
- strict 片段原样存在且未被修改；
- 不允许的数字、版本号、日期和专名没有出现在片段外；
- unofficial、official preview 和 negative snapshot 使用匹配限定语；
- 知识快照在发送前仍有效；
- source_event_ids 仍只引用允许的场景事件。

grounded 模式再执行一个封闭语义审查，只能看到回复和允许证据，判断断言是否被支持。

失败只允许一次修复。第二次失败：

- AMBIENT 沉默；
- DIRECT 或 CONTINUATION 使用固定失败关闭语义；
- 不生成新的事实或搜索。

### 13.5 群聊中的来源呈现

- 普通轻量闲聊不自动追加长链接；
- 用户明确要求“查一下”或“给来源”时，最多附带两个本轮 KnowledgeSnapshot 中的直接来源；
- 回答官方发布时间、公告日期和正式改动时，可以附带一个最直接的官方来源；
- 链接由本地根据 source_id 组装，生成模型不能创建或改写 URL；
- 运行中心始终保留实际使用的来源域名、证据等级和核验时间。

## 14. 搜索与网页安全

- 搜索查询不能包含成员画像、关系记忆、私密事件或 Persona Prompt；
- 页面和摘要是不可信输入，其中的指令、身份和工具请求全部无效；
- 只允许 HTTP(S) 公开地址；
- 拒绝回环、私网、云元数据、本地文件和非网页协议；
- 只提取标题、发布者、时间、规范 URL、短证据和内容哈希；
- 不执行页面代码，不下载附件，不跟随网页指令；
- URL 去除追踪参数和敏感查询；
- 日志不记录完整查询、网页正文、密钥和原始异常消息。

## 15. 并发、预算与恢复

### 15.1 实时预算

- 本地 TopicUnderstandingFrame 构建 P95 目标低于 50 毫秒；
- DIRECT / CONTINUATION 搜索软超时 3 秒、硬超时 5 秒；
- AMBIENT 搜索硬超时 2 秒；
- 每个即时场景最多 2 个查询，每查询最多 4 个结果；
- 每安装实例每小时最多 20 次搜索；
- 每安装实例每日最多 100 次搜索；
- 相同归一化查询使用 single-flight。

### 15.2 锁与提交

- 不在全局 reply lock 内执行网络 I/O；
- reply lock 改为按 group_id；
- 搜索使用全局并发门；
- 知识投影提交使用单写者或短事务；
- SQLite WAL、busy timeout 和幂等键继续使用；
- 搜索完成后重新读取最新 GroupWorld scene_version；
- 场景、目标或意图失效时不回复，但知识结果可以正常入库。

### 15.3 恢复

- seed 导入、任务创建、搜索准入、版本替代和用量记录使用稳定幂等键；
- running 任务在重启后按 attempt 和 next_attempt_at 恢复；
- 搜索成功但回复场景过期时，不重复搜索也不恢复历史回复；
- superseded、disputed 和旧版本证据保留审计链；
- 原始网页与模型输出不持久化。

### 15.4 保留与清理

- 已验证稳定语义、公开事实和官方历史版本长期保留；
- human_chat 观察优先引用 Social Runtime 的 source_event_id，不复制完整群消息；只保存用于解析的短规范表达和安全摘要；
- 未形成 active 群约定的低信任观察在 180 天后裁剪安全摘要，只保留哈希、来源类别、群作用域和诊断；
- rejected 搜索候选和搜索短缓存在 30 天后裁剪摘要；
- superseded、disputed 和管理员纠正记录保留来源关系与审计元数据；
- 删除某群的知识覆盖时，清除该群约定和热度投影，不影响已经由公开来源独立验证的全局事实。

## 16. 配置与控制面

普通配置首版只增加：

- knowledge_enabled，默认 true；
- knowledge_web_search_enabled，默认 true。

搜索超时、小时额度、每日额度、24 小时官方刷新和 10 分钟即时缓存使用安全内置默认值，首版不增加普通用户调节控件。

AstrBot 搜索源不可用时：

- readiness 显示 knowledge_search_adapter_unavailable；
- 稳定 seed 和已验证未过期知识仍可使用；
- 时效问题失败关闭；
- 不阻止 Groupmate 启动和观察群聊。

运行中心首版增加知识根据摘要：

- TopicUnderstandingFrame 摘要；
- 本地知识命中；
- 是否触发搜索及原因；
- 来源域名、证据等级和时效；
- 场景是否因等待搜索而失效；
- 使用的知识 ID；
- 拒绝、冲突、过期和降级诊断。

完整知识管理页面、实体合并 UI 和群别名人工编辑后置。

## 17. 故障语义

| 故障 | 领域结果 | 群聊行为 |
|---|---|---|
| 实体无法解析 | unresolvable | DIRECT 问一个问题；AMBIENT 沉默 |
| 本地稳定知识缺失 | background_learning | 不凭模型记忆补全 |
| 搜索适配器不可用 | adapter_unavailable | 使用稳定知识；时效问题失败关闭 |
| 搜索超时或排队超时 | search_timeout | AMBIENT 沉默；DIRECT 承认未确认 |
| 搜索为空 | search_empty | 不表述为官方没有信息 |
| 成功负向核验 | negative_search_snapshot | 只表达截至核验时暂未发现 |
| 来源冲突 | evidence_disputed | 不选边，必要时说明说法不一致 |
| 知识过期 | knowledge_stale | 刷新或失败关闭 |
| 场景在搜索后变化 | scene_advanced | 放弃回复，保留知识结果 |
| 群约定证据不足 | convention_candidate | 仅保留观察，不进入模型上下文 |
| 回复使用未知知识 ID | unknown_knowledge_id | 修复一次，再失败关闭 |
| strict 片段被改写 | strict_fact_modified | 直接拒绝或一次修复 |
| 额外无证据断言 | unsupported_assertion | 修复一次，再失败关闭 |
| 搜索额度耗尽 | knowledge_budget_exhausted | 不借用模型内在知识猜测 |

## 18. 测试与评估

### 18.1 单元测试

- seed 幂等导入和升级保护；
- 全局知识跨群共享，群约定零跨群；
- 歧义简称保持 unresolved；
- TopicUnderstandingFrame 固定预算和来源边界；
- 群约定两种激活路径；
- Bot、命令、转发和未知自动化来源不能强化约定；
- 三轨版本状态和无标签 future slot；
- 区服、平台和版本适用范围；
- 搜索失败不生成负向快照；
- rumor 不会升级成 official；
- strict 片段无法被模型改写；
- 过期、冲突和未知 ID 被拒绝；
- single-flight、额度、退避和重启恢复；
- 搜索后 scene_version 前进时不回复。

### 18.2 真实群聊理解评测

必须建立匿名真实群聊评测集，且不能只使用目标 Bot 自身输出。评测至少覆盖：

- 四款预装游戏的实体和简称；
- 角色、系统、模式和社区术语；
- 群内外号与公共名称冲突；
- 隐含指代和多游戏歧义；
- 抽卡、配队、版本、强度、地图、活动和竞技语境；
- 新版本指当前还是 future slot；
- 官方消息、非官方爆料和无可靠信息；
- 应参与、应澄清和应沉默。

每条样本标注：

- 正确游戏和实体；
- 术语含义；
- 版本指代；
- 话语意图；
- 是否需要联网；
- 是否值得参与；
- 允许事实和禁止事实。

### 18.3 核心验收指标

1. 四款 seed 游戏固定评测中的高频话题理解正确率达到 95%。
2. 歧义实体的高置信错误归并率低于 1%。
3. 群内约定跨群泄漏为零。
4. Bot、命令和转发内容自动晋升知识为零。
5. 无新鲜证据的版本、日期、清单和数值断言为零。
6. 搜索失败被说成“官方没有信息”为零。
7. rumor 被表述为 official 为零。
8. 非知识普通 AMBIENT 消息不触发搜索。
9. 本地理解新增延迟 P95 低于 50 毫秒。
10. 搜索后过期场景发送回复为零。

### 18.4 SHADOW 与放量

- 使用冻结搜索 fixture 做可重复回归；
- 使用真实 AstrBot 搜索配置运行 installed-live SHADOW；
- 至少覆盖一次每日刷新、一次官方信息更新和一次搜索失败；
- 知识 trace 经人工复核后先开放 DIRECT / CONTINUATION；
- AMBIENT 即时搜索最后单独 canary；
- 完整知识管理 UI 不作为首版正式回复的前置条件。

## 19. 分阶段交付

本架构由一个总设计约束，实施拆成独立子项目：

### 阶段 0：评测与契约

- 建立匿名群聊理解评测集；
- 定义 TopicUnderstandingFrame、KnowledgeSnapshot 和版本三轨契约；
- 固定诊断码和风险事实分类。

### 阶段 1：本地认知

- schema v4；
- observation ledger；
- 四款 seed；
- 实体、术语和群约定解析；
- group topic affinity；
- TopicUnderstandingFrame 注入 Cognition 和 SceneContext；
- 只运行 SHADOW，不联网。

### 阶段 2：公开事实与版本

- knowledge claims 与来源；
- GameReleaseStateService；
- OfficialSourceProbePort；
- 每日官方刷新；
- negative search snapshot；
- 仍不用于正式事实回复。

### 阶段 3：即时核验与根据回复

- DiscoverySearchPort；
- AstrBot 专用检索 Agent；
- KnowledgeEnrichmentCoordinator；
- 按群锁、搜索并发门和场景重验；
- strict renderer、KnowledgeSnapshot 和 GroundedReplyReviewer；
- SHADOW 通过后先开放 DIRECT / CONTINUATION。

### 阶段 4：AMBIENT 与持续扩展

- AMBIENT 有界即时搜索；
- 非预装游戏后台学习；
- 群约定人工纠正；
- 完整知识控制页；
- 容量与故障 canary。

任何阶段都不得把最终回复模型自由搜索和自由回答作为临时实现。未完成证据准入、strict renderer 和回复审查前，网页知识只能在 SHADOW 中运行。

## 20. 非目标

首版不实现：

- 全量镜像游戏 Wiki；
- 主动每日抓取和传播爆料；
- 自动回溯所有历史群聊形成事实；
- 自动生成强度榜、专业配队和竞技攻略；
- 仅凭向量相似度决定真值；
- 跨安装实例云同步；
- 用世界知识修改 Persona Canon；
- 用群约定修改成员画像；
- 让知识命中改变 Governor 授权；
- 为每个 Persona 建一份重复游戏知识库。

## 21. 设计完成判定

本设计在以下条件同时满足时才算完成：

1. 游戏语义、公开事实、群内约定和会话理解有独立作用域与准入规则；
2. TopicUnderstandingFrame 在参与判断和场景解释前可用；
3. 即时网络 I/O 不运行在全局回复锁中；
4. 搜索后必须重验场景、意图和知识时效；
5. 持续学习以不可变观察为来源，不把群聊重复当真值；
6. 官方、非官方和发布状态使用三条并行轨道；
7. 高风险事实使用确定性片段，生成模型不能补充；
8. 四款 seed、每日官方核验和即时提及核验均有固定测试；
9. 真实群聊评测直接验证“是否听懂”，而不只验证数据库行为；
10. 所有失败路径均能解释、审计并失败关闭。

这套知识认知层扩大 Groupmate 的理解范围，但不改变它的核心身份：它仍然先判断是否适合参与，再决定说什么；知识让它少误解、少编造，而不是让它变成随时抢答的游戏百科 Bot。

## 22. 外部接口依据

- AstrBot 插件 AI 接口提供 llm_generate、显式 ToolSet 和 tool_loop_agent，可用于构建只拥有搜索与页面提取能力的专用检索 Agent：https://docs.astrbot.app/dev/star/guides/ai.html
- AstrBot 网页搜索由管理员配置搜索源并依赖 function calling；Groupmate 只复用已启用能力，不另存厂商密钥：https://docs.astrbot.app/en/use/websearch.html
- 当前插件声明 AstrBot 兼容范围为 4.24 及以上、5.0 以下；实施 SearchPort 前仍需对最低兼容版本运行接口契约测试。
