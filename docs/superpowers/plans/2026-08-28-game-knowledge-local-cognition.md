# Groupmate 本地游戏知识认知 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不联网、不改变 Governor 授权和不正式输出知识事实的前提下，让 Groupmate 在认知判断前理解五款预装游戏、常见术语、版本指代和每个群自己的约定，并形成可持续积累的知识观察账本。

**Architecture:** 新建纯领域 `social_runtime/knowledge` 包，schema v4 一次性建立总设计需要的知识表。本阶段只激活 seed、本地实体解析、群观察/约定/热度投影和 `TopicUnderstandingFrame` 注入；Bridge 负责过滤合格来源和生命周期编排，Manager 只消费一个只读 resolver Port。所有结果进入 SHADOW trace，不进入 ReplyPlan 的事实内容。

**Tech Stack:** Python 3.11+、asyncio、SQLite/WAL、版本化 JSON seed、pytest、现有 Social Runtime cognition/scene/trace 管线

**Spec:** `docs/superpowers/specs/2026-08-27-world-knowledge-and-game-version-grounding-design.md`

## Global Constraints

- 本计划依赖路线图，无前置业务计划；完成后仍禁止联网和知识事实正式回复。
- `knowledge_entities`、公开 claims 和 seed 是安装实例全局；`group_conventions`、`group_knowledge_aliases`、`group_topic_affinity` 必须按 `group_id` 查询。
- 任何知识表都不能用 `persona_id` 作为业务隔离键；`source_event_id` 只用于因果审计。
- 观察链路必须在 durable ingest 与 interaction ownership 解析后发生，且不能阻塞消息回复。
- 自身输出、`social_eligible=false`、`EXTERNAL_PLUGIN`、命令、转发、已知 Bot 与未知自动化来源不参与自动激活。
- 群聊观察只能生成 `candidate` 群约定和热度；不能生成 active public fact。
- `TopicUnderstandingFrame` 有固定条目与字符预算，歧义不足时必须 unresolved。
- 本阶段所有知识影响必须可在 SHADOW trace 看到，但不得改变发送、参与阈值或 SocialMove。

---

## 文件结构

### 新建

- `groupmate/social_runtime/knowledge/__init__.py`：公开导出。
- `groupmate/social_runtime/knowledge/contracts.py`：frame、实体、版本指代、观察、诊断和 Port 契约。
- `groupmate/social_runtime/knowledge/repository.py`：schema v4 知识账本与本地投影仓储。
- `groupmate/social_runtime/knowledge/seeds.py`：版本化 seed 校验和幂等导入器。
- `groupmate/social_runtime/knowledge/observation.py`：来源分类、异步队列、准入与群投影编排。
- `groupmate/social_runtime/knowledge/resolver.py`：确定性实体/术语/相对版本本地解析。
- `groupmate/social_runtime/knowledge/retrieval.py`：有界只读召回和 `KnowledgeNeedAssessor`。
- `groupmate/social_runtime/knowledge/assets/genshin-impact.v1.json`
- `groupmate/social_runtime/knowledge/assets/delta-force.v1.json`
- `groupmate/social_runtime/knowledge/assets/wuthering-waves.v1.json`
- `groupmate/social_runtime/knowledge/assets/honkai-star-rail.v1.json`
- `groupmate/social_runtime/knowledge/assets/zenless-zone-zero.v1.json`
- `scenarios/game_knowledge_understanding.jsonl`：匿名、冻结的理解评测集。
- `eval/knowledge.py`：理解评测聚合指标。
- `tests/social_runtime/knowledge/test_contracts.py`
- `tests/social_runtime/knowledge/test_repository.py`
- `tests/social_runtime/knowledge/test_seeds.py`
- `tests/social_runtime/knowledge/test_observation.py`
- `tests/social_runtime/knowledge/test_resolver.py`
- `tests/social_runtime/knowledge/test_retrieval.py`
- `tests/evaluation/test_game_knowledge.py`
- `tests/scenarios/test_game_knowledge_shadow.py`

### 修改

- `groupmate/social_runtime/persistence/schema.py`：v3→v4 加法迁移与知识表验证。
- `groupmate/settings.py`：增加两个布尔配置。
- `_conf_schema.json`：公开两个知识开关。
- `groupmate/social_runtime/cognition/contracts.py`：保持 `world_summary` JSON 边界并记录 frame 摘要契约测试。
- `groupmate/social_runtime/social_context.py`：显式接收同一 frame 的场景事实。
- `groupmate/social_runtime/manager.py`：在 cognition 前调用 resolver，并把 frame 附到 `ShadowEvaluation`。
- `groupmate/adapters/astrbot_bridge.py`：知识仓储、seed、观察服务的组合根与生命周期。
- `groupmate/adapters/astrbot_events.py`：保留来源分类所需的平台事实，不做知识判断。
- `groupmate/social_runtime/control/message_traces.py`：投影安全的知识理解摘要。
- `tests/social_runtime/test_schema.py`
- `tests/contracts/test_astrbot_events.py`
- `tests/contracts/test_message_traces.py`
- `tests/social_runtime/test_social_context.py`
- `tests/scenarios/test_chat_mainline.py`

---

### Task 1: 固定知识契约、风险分类与评测口径

**Files:**
- Create: `groupmate/social_runtime/knowledge/__init__.py`
- Create: `groupmate/social_runtime/knowledge/contracts.py`
- Create: `eval/knowledge.py`
- Create: `tests/social_runtime/knowledge/test_contracts.py`
- Create: `tests/evaluation/test_game_knowledge.py`

**Interfaces:**
- Produces: `TopicUnderstandingFrame.create(**values) -> TopicUnderstandingFrame`。
- Produces: `KnowledgeObservation.create(**values) -> KnowledgeObservation`。
- Produces: `KnowledgeNeed.create(**values) -> KnowledgeNeed`，assessor 在 Task 5 实现。
- Produces: `game_knowledge_metrics(records) -> Mapping[str, float | int]`。

- [ ] **Step 1: 写失败的不可变契约测试**

覆盖：空 ID、未知枚举、confidence 越界、全局观察携带 group、群观察缺 group、version reference 缺游戏、超过 8 个实体/12 个术语/16 个 supporting ID、不可 JSON 序列化值、输入列表在构造后被外部修改。

```python
def test_topic_frame_is_bounded_and_immutable():
    frame = TopicUnderstandingFrame.create(
        frame_id="knowledge-frame:1",
        game_ids=("game:genshin-impact",),
        resolved_entities=(),
        resolved_terms=(),
        discourse_referents=(),
        version_reference=None,
        conversation_intent_hint="version_question",
        ambiguity_codes=(),
        confidence=0.9,
        supporting_knowledge_ids=("seed:genshin:v1",),
    )
    assert frame.game_ids == ("game:genshin-impact",)
    with pytest.raises(dataclasses.FrozenInstanceError):
        frame.confidence = 0.1
```

- [ ] **Step 2: 运行契约测试并确认 RED**

Run: `pytest -q tests/social_runtime/knowledge/test_contracts.py tests/evaluation/test_game_knowledge.py`

Expected: collection 因 `groupmate.social_runtime.knowledge` 和 `eval.knowledge` 不存在而失败。

- [ ] **Step 3: 实现最小领域类型和固定枚举**

定义 `OriginClass`、`KnowledgeScope`、`ClaimKind`、`EvidenceLevel`、`ClaimStatus`、`KnowledgeNeedOutcome`、`RiskClass`。所有 `.create()` 归一化 NFKC 文本、去重并限制长度；`safe_summary` 最大 240 字，别名最大 48 字，frame `to_prompt_facts()` 最大 1800 字且不含来源原文、内部得分和作者 ID。

- [ ] **Step 4: 实现评测聚合器**

聚合器只读逐条结果并输出 `understanding_accuracy`、`high_confidence_wrong_merge_rate`、`cross_group_leaks`、`bot_promotions`、`command_promotions`、`temporal_need_recall`；分母为 0 时返回 0.0，不静默丢弃 malformed record。

- [ ] **Step 5: 运行测试并提交**

Run: `pytest -q tests/social_runtime/knowledge/test_contracts.py tests/evaluation/test_game_knowledge.py`

Expected: PASS。

Commit: `git commit -m "feat: define game knowledge contracts"`

---

### Task 2: 建立 schema v4 与作用域安全仓储

**Files:**
- Create: `groupmate/social_runtime/knowledge/repository.py`
- Create: `tests/social_runtime/knowledge/test_repository.py`
- Modify: `groupmate/social_runtime/persistence/schema.py`
- Modify: `tests/social_runtime/test_schema.py`
- Modify: `tests/shared/test_group_scope_privacy.py`

**Interfaces:**
- Produces: `KnowledgeRepository(path)`。
- Produces: `.append_observation(observation) -> bool`，按稳定 ID 幂等。
- Produces: `.upsert_seed_entity(entity, aliases, seed)`、`.aliases_for_text(text, group_id)`、`.group_conventions(group_id, normalized_expression)`、`.record_qualified_mention(observation_id, entity_id, scene_ref)`。
- Reserves for later plans: claim/source/evidence、release state、job/usage 表和短事务 API。

- [ ] **Step 1: 写 v4 bootstrap 和 v3 migration 失败测试**

断言 clean bootstrap 与 owned v3 migration 都得到版本 4 和这些表：`knowledge_observations`、`knowledge_entities`、`knowledge_aliases`、`group_knowledge_aliases`、`knowledge_claims`、`knowledge_sources`、`knowledge_claim_evidence`、`group_conventions`、`group_topic_affinity`、`game_release_states`、`negative_search_snapshots`、`knowledge_jobs`、`knowledge_usage`、`knowledge_seeds`。迁移必须保留现有 inbox、profile、style 行。

- [ ] **Step 2: 写作用域和约束失败测试**

验证：全局/群 scope 的互斥 CHECK、`content_hash` 幂等、同群 alias 唯一、相同表达可存在于两个群、查询 g1 永远不返回 g2、claim status/release 三轨非法值被 SQLite 拒绝、所有 group 表没有 `persona_id` 列。

- [ ] **Step 3: 运行 schema/repository 测试并确认 RED**

Run: `pytest -q tests/social_runtime/test_schema.py tests/social_runtime/knowledge/test_repository.py tests/shared/test_group_scope_privacy.py`

Expected: schema 仍是 v3、知识表和 repository 缺失导致失败。

- [ ] **Step 4: 实现加法迁移和短事务仓储**

`_migrate_v3_to_v4()` 使用 `BEGIN IMMEDIATE`，创建全部知识表后最后更新版本；repository 每个写操作自行打开连接并短提交，不持有跨网络事务。`author_ref` 使用 `sha256(group_id + "\x1f" + actor_id + install_salt)[:24]`，原始 actor ID 不进入知识表。

```python
def append_observation(self, value: KnowledgeObservation) -> bool:
    with connect_database(self.path) as db:
        cursor = db.execute(
            "INSERT OR IGNORE INTO knowledge_observations("
            "observation_id, origin_class, scope_kind, group_id, author_ref, "
            "source_event_id, source_id, entity_hint, safe_summary, content_hash, "
            "occurred_at, recorded_at, status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            value.as_db_tuple(),
        )
        return cursor.rowcount == 1
```

- [ ] **Step 5: 实现 7 天半衰期 affinity 投影**

同一 `source_event_id × entity_id` 幂等；只有 qualified human observation 计数；distinct actor/scene 分开计数；读取时按 `0.5 ** (age_seconds / 604800)` 衰减，Bot、command、forward 路径不能调用投影更新。

- [ ] **Step 6: 运行测试并提交**

Run: `pytest -q tests/social_runtime/test_schema.py tests/social_runtime/knowledge/test_repository.py tests/shared/test_group_scope_privacy.py`

Expected: PASS。

Commit: `git commit -m "feat: persist scoped game knowledge"`

---

### Task 3: 导入五款版本化稳定语义包

**Files:**
- Create: `groupmate/social_runtime/knowledge/seeds.py`
- Create: `groupmate/social_runtime/knowledge/assets/genshin-impact.v1.json`
- Create: `groupmate/social_runtime/knowledge/assets/delta-force.v1.json`
- Create: `groupmate/social_runtime/knowledge/assets/wuthering-waves.v1.json`
- Create: `groupmate/social_runtime/knowledge/assets/honkai-star-rail.v1.json`
- Create: `groupmate/social_runtime/knowledge/assets/zenless-zone-zero.v1.json`
- Create: `tests/social_runtime/knowledge/test_seeds.py`

**Interfaces:**
- Produces: `load_bundled_seeds() -> tuple[GameSemanticSeed, ...]`。
- Produces: `SeedImporter(repository).import_all(seeds) -> SeedImportReport`。
- Each JSON includes: `seed_id`、`seed_version`、`game`、`aliases`、`entity_types`、`terms`、`stable_relations`、`discussion_patterns`、`official_sources`、`content_hash`。

- [ ] **Step 1: 写 seed schema 与禁区失败测试**

五个资产必须各有唯一 canonical game ID、中文官方名、英文名/常见简称、至少 5 类稳定实体、20 个稳定术语/讨论模式和官方来源注册表。测试拒绝 `current_banner`、`current_version`、`latest_numbers`、`tier_list`、`leak_content` 等时效字段，并拒绝 source 非 HTTPS 或非登记官方域名。

- [ ] **Step 2: 写幂等升级失败测试**

同版本同 hash 二次导入为 no-op；同 `seed_id × version` 不同 hash 报 `seed_hash_conflict`；v2 只 supersede 同 seed 的 v1 stable semantic，不覆盖 `evidence_level=official` 的后续 claim。

- [ ] **Step 3: 运行 seed 测试并确认 RED**

Run: `pytest -q tests/social_runtime/knowledge/test_seeds.py`

Expected: loader、资产和 importer 缺失。

- [ ] **Step 4: 分别编写并校验五个 seed 资产**

每个游戏以官方名称和稳定玩法结构为骨架，社区术语显式标 `community`；歧义 alias 必须给 `requires_any_context` 或 `ambiguity_level=high`。原神/星铁/鸣潮/绝区零覆盖抽卡、角色、配队、资源、版本讨论；三角洲覆盖行动、烽火地带、全面战场、干员、地图、装备和赛季讨论。不得写当前卡池、当前赛季和当期角色。

- [ ] **Step 5: 实现严格 loader、hash 和幂等 importer**

hash 取去除 `content_hash` 后的 canonical JSON SHA-256；loader 启动时逐个验证，任何内置资产损坏都使 knowledge readiness 降级并禁止使用该 seed，不部分导入该包。

- [ ] **Step 6: 运行测试并提交**

Run: `pytest -q tests/social_runtime/knowledge/test_seeds.py tests/social_runtime/knowledge/test_repository.py`

Expected: PASS。

Commit: `git commit -m "feat: bundle five game semantic seeds"`

---

### Task 4: 实现来源过滤、观察队列和群约定激活

**Files:**
- Create: `groupmate/social_runtime/knowledge/observation.py`
- Create: `tests/social_runtime/knowledge/test_observation.py`
- Modify: `groupmate/adapters/astrbot_events.py`
- Modify: `groupmate/adapters/astrbot_bridge.py`
- Modify: `tests/contracts/test_astrbot_events.py`

**Interfaces:**
- Produces: `KnowledgeOriginClassifier.classify(event) -> OriginDecision`。
- Produces: `KnowledgeObservationService.start()`、`.observe(event)`、`.close()`、`.health()`。
- Consumes: resolved interaction payload fields and durable event IDs。
- Produces active convention only after repository evidence thresholds are satisfied。

- [ ] **Step 1: 写来源矩阵失败测试**

覆盖 human、own output、known bot、unknown actor、external command、Groupmate 管理命令、forward、card、纯链接和正常带链接人类讨论。Event translator 只保留 `is_self`、segment kind、ownership 与可选 `sender_role/automation_hint`；classifier 才决定 origin class。

- [ ] **Step 2: 写队列/恢复失败测试**

`observe()` 只持久化或入有界队列，不等待模型/网络；重复 event 幂等；close 排空已接收项；异常记录安全 diagnostic 后重试；队列满时丢弃最低信任项而不阻塞主链路。

- [ ] **Step 3: 写群约定阈值失败测试**

覆盖“一人解释 + 另一场景一致使用”、“两人三场景一致使用”和管理员确认三条激活路径；单人刷屏、三个 Bot、跨群证据、相反定义和 90 天未使用分别保持 candidate/disputed/stale。

- [ ] **Step 4: 运行测试并确认 RED**

Run: `pytest -q tests/social_runtime/knowledge/test_observation.py tests/contracts/test_astrbot_events.py`

Expected: observation service/classifier 缺失，event 来源事实断言失败。

- [ ] **Step 5: 实现过滤和独立后台服务**

复用 `ProfileService` 的 start/wake/close 形状，但使用独立 repository 和 admission policy。Bridge 只在 `_resolve_interaction()` 之后且 `manager.ingest()` 已接受事件时调用 `knowledge_service.observe(translated)`；`observe_event()` 没有 Social Runtime durable ingest，因此不产生知识观察，外部兼容命令正文也不会沉淀。

- [ ] **Step 6: 实现 convention candidate 与 affinity 投影**

只保存裁剪表达、安全释义、opaque author ref、scene ref 与 event ID；相反释义将 active 降回 disputed，不覆盖历史证据。约定激活不修改全局 alias，只写 `group_knowledge_aliases`。

- [ ] **Step 7: 运行测试并提交**

Run: `pytest -q tests/social_runtime/knowledge/test_observation.py tests/contracts/test_astrbot_events.py tests/scenarios/test_profile_background_pipeline.py`

Expected: PASS，现有 profile 后台链路无回归。

Commit: `git commit -m "feat: learn group knowledge conventions"`

---

### Task 5: 实现本地解析、召回和知识需求判定

**Files:**
- Create: `groupmate/social_runtime/knowledge/resolver.py`
- Create: `groupmate/social_runtime/knowledge/retrieval.py`
- Create: `tests/social_runtime/knowledge/test_resolver.py`
- Create: `tests/social_runtime/knowledge/test_retrieval.py`

**Interfaces:**
- Produces: `KnowledgeEntityResolver.resolve(event, context_events, group_id, now) -> TopicUnderstandingFrame`。
- Produces: `KnowledgeRetriever.retrieve(frame, group_id, now, limit=8) -> tuple[KnowledgeHit, ...]`。
- Produces: `KnowledgeNeedAssessor.assess(frame, hits, now) -> KnowledgeNeed`。

- [ ] **Step 1: 写实体和群 alias 失败测试**

覆盖五款游戏官方名/简称、跨游戏同名词、群 alias 仅在本群生效、高 affinity 只能消歧不能凭空归类、无上下文高歧义 alias 保持 unresolved、seed disable 后不再命中。

- [ ] **Step 2: 写版本指代与意图提示失败测试**

固定消息时间覆盖“新版本”“下版本”“刚更新”“前瞻”“爆料”“测试服”“这期”；本阶段只输出 `VersionReference(relative_kind, disclosure_kind, game_id, confidence)`，不能猜版本号。没有唯一游戏时加 `ambiguous_game_for_version`。

- [ ] **Step 3: 写 need assessor 失败测试**

稳定术语聊天为 `local_sufficient`；未知游戏为 `background_learning`；明确版本/日期/阵容/数值/官方/爆料为 `fresh_evidence_required`；无法唯一解析的 direct 为 `unresolvable`；普通非知识聊天为 `none`。

- [ ] **Step 4: 运行测试并确认 RED**

Run: `pytest -q tests/social_runtime/knowledge/test_resolver.py tests/social_runtime/knowledge/test_retrieval.py`

Expected: resolver/retriever 缺失。

- [ ] **Step 5: 实现确定性 longest-match + 上下文消歧**

归一化 NFKC、大小写和常见标点；先群 exact alias，再全局 exact alias，再受上下文约束的 alias。排序键固定为 exact、context requirements、current discourse、group affinity、seed/source priority；confidence 未达 0.75 不输出 resolved entity。

- [ ] **Step 6: 实现有界召回和风险词法判定**

召回最多 8 个实体/术语，只返回 ID 与安全摘要；risk classifier 对版本、时间、清单、数值、官方/非官方状态采用保守匹配，命中时本阶段只标记 fresh evidence need，不让模型用内在知识回答。

- [ ] **Step 7: 运行测试并提交**

Run: `pytest -q tests/social_runtime/knowledge/test_resolver.py tests/social_runtime/knowledge/test_retrieval.py`

Expected: PASS。

Commit: `git commit -m "feat: resolve local game knowledge"`

---

### Task 6: 在 cognition 与 SceneContext 前注入同一冻结 frame

**Files:**
- Modify: `groupmate/social_runtime/manager.py`
- Modify: `groupmate/social_runtime/cognition/contracts.py`
- Modify: `groupmate/social_runtime/social_context.py`
- Modify: `groupmate/adapters/astrbot_bridge.py`
- Modify: `tests/social_runtime/test_social_context.py`
- Modify: `tests/scenarios/test_chat_mainline.py`
- Create: `tests/scenarios/test_game_knowledge_shadow.py`

**Interfaces:**
- `SocialRuntimeManager` 增加关键字参数 `knowledge_resolver: KnowledgeResolverPort | None = None`。
- `ShadowEvaluation.topic_understanding: TopicUnderstandingFrame | None`。
- `SceneContextBuilder.build()` 增加关键字参数 `topic_understanding: TopicUnderstandingFrame | None = None`；输出 `facts["topic_understanding"]`。
- Bridge 必须复用 evaluation 上的同一个 frame，不能在生成前重新解析。

- [ ] **Step 1: 写 cognition 注入失败测试**

Spy worker 断言 `world_summary["topic_understanding"]` 在 `cognition.evaluate()` 前存在，只含有界安全字段；resolver 异常时产生空 frame 和 `knowledge_local_resolution_failed` diagnostic，原有 cognition 仍继续。

- [ ] **Step 2: 写 scene 复用与授权不变失败测试**

同一个 `frame_id` 同时出现在 CognitiveContext 和 SceneContext；knowledge hit 不新增 intention、不改变 Governor 的 SILENCE；SHADOW 与 SOCIAL_RUNTIME 对参与判断使用相同 frame。

- [ ] **Step 3: 运行集成测试并确认 RED**

Run: `pytest -q tests/social_runtime/test_social_context.py tests/scenarios/test_chat_mainline.py tests/scenarios/test_game_knowledge_shadow.py`

Expected: Manager/SceneContext 尚无 topic frame 接口。

- [ ] **Step 4: 注入 resolver Port 与冻结 evaluation 数据**

在 `_evaluate_cycle()` 获取 focus/context events 后、构造 `world_summary` 前同步调用本地 resolver；将 `frame.to_prompt_facts()` 作为 world summary 子对象。`ShadowEvaluation.to_capture_evidence()/from_capture_evidence()` 支持可选字段，保证旧 capture 可重放。

- [ ] **Step 5: 在 Bridge 场景构建中复用 frame**

`_handle_evaluations()` 创建 `SceneContext` 时传 evaluation frame；后续 scene model 只看到同一快照。任何二次数据库更新都留到下一轮，不改变本轮理解。

- [ ] **Step 6: 运行测试并提交**

Run: `pytest -q tests/social_runtime/test_social_context.py tests/scenarios/test_chat_mainline.py tests/scenarios/test_game_knowledge_shadow.py tests/recovery/test_phase_a_replay.py`

Expected: PASS，旧 capture/replay 兼容。

Commit: `git commit -m "feat: ground social cognition in local knowledge"`

---

### Task 7: 配置、trace 与冻结理解评测发布门

**Files:**
- Modify: `groupmate/settings.py`
- Modify: `_conf_schema.json`
- Modify: `groupmate/social_runtime/control/message_traces.py`
- Modify: `groupmate/adapters/astrbot_bridge.py`
- Modify: `tests/contracts/test_message_traces.py`
- Create: `scenarios/game_knowledge_understanding.jsonl`
- Modify: `tests/evaluation/test_game_knowledge.py`
- Modify: `tests/scenarios/test_game_knowledge_shadow.py`

**Interfaces:**
- `knowledge_enabled: bool = True`。
- `knowledge_web_search_enabled: bool = True`，本阶段只记录配置，绝不调用网络。
- trace understanding 增加 `games`、`entities`、`terms`、`version_reference`、`need`、`diagnostic_codes`，不显示内部置信分和作者引用。

- [ ] **Step 1: 写配置和 trace 失败测试**

验证两个开关默认 true、拒绝字符串布尔值、knowledge off 时 resolver/observer 都不启动、web search off 在本阶段无网络副作用。Trace 只显示 canonical label 和限定诊断，不泄漏 safe summary 之外的证据。

- [ ] **Step 2: 建立冻结评测集**

写入至少 240 条匿名 JSONL：五款游戏各 36 条稳定语义/术语，跨游戏歧义 30 条，版本指代 20 条，非游戏对照 10 条。每条固定 `case_id`、`group_id`、`occurred_at`、`text`、`context`、expected games/entities/terms/version/need/ambiguity。

- [ ] **Step 3: 运行发布门并确认初次失败**

Run: `pytest -q tests/contracts/test_message_traces.py tests/evaluation/test_game_knowledge.py tests/scenarios/test_game_knowledge_shadow.py`

Expected: 新配置/trace/corpus 断言失败，随后按失败类别补 seed 或消歧规则，不降低断言阈值。

- [ ] **Step 4: 完成安全 trace 投影和 corpus runner**

MessageTraceRepository 只投影 frame 摘要和 need；未知旧 evaluation 默认空知识摘要。评测 runner 逐条从临时 v4 DB 导入 seed 后解析，记录耗时和错误类型。

- [ ] **Step 5: 运行本地认知 Gate 1**

Run: `pytest -q tests/social_runtime/knowledge tests/contracts/test_astrbot_events.py tests/contracts/test_message_traces.py tests/scenarios/test_game_knowledge_shadow.py tests/evaluation/test_game_knowledge.py`

Expected: PASS；`understanding_accuracy >= 0.95`、`high_confidence_wrong_merge_rate < 0.01`、`cross_group_leaks == 0`、`bot_promotions == 0`、`command_promotions == 0`。

- [ ] **Step 6: 运行回归和提交**

Run: `pytest -q tests/social_runtime tests/contracts tests/shared tests/scenarios`

Expected: PASS。

Run: `git diff --check`

Expected: 无输出。

Commit: `git commit -m "feat: ship shadow game cognition"`

---

## 子项目验收

- 五款游戏稳定聊天在参与判断前得到同一 `TopicUnderstandingFrame`。
- 群约定只在本群生效，不与 Persona 绑定，不更改公共真值。
- 观察过滤矩阵对自身、Bot、命令和转发失败关闭。
- 本阶段没有任何网络调用，也没有知识事实进入正式 ReplyPlan。
- SHADOW trace 能解释“听懂了什么、哪里歧义、为何需要新鲜证据”。

通过后进入：`docs/superpowers/plans/2026-08-28-game-version-public-facts.md`。
