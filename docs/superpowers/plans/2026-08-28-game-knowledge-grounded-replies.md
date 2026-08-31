# Groupmate 游戏知识根据回复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 DIRECT / CONTINUATION 在需要时通过 AstrBot 已配置搜索完成有界即时核验，并且只使用冻结、可审查的知识快照回答版本、日期、清单、数值、官方与爆料状态，任何失败都不编造。

**Architecture:** `KnowledgeEnrichmentCoordinator` 在 Governor ACT 后、任何 reply lock 前运行，通过 `OfficialSourceProbePort` 和只拥有搜索/页面提取工具的 `DiscoverySearchPort` 获取证据；全局 semaphore、single-flight 和持久额度限制外部 I/O。证据提交后重验 scene/target/lease，再冻结 `KnowledgeSnapshot`。高风险事实走 `parts[text|knowledge_fragment]` 协议和本地 assembler，grounded 稳定事实走封闭证据审查；最终仍经过 SocialOutputReviewer、OutputFirewall 与 Outbox。

**Tech Stack:** Python 3.11+、asyncio、SQLite/WAL、AstrBot 4.24–4.x ToolSet/tool-loop Agent、pytest/fake tools、现有 ReplyPlan/Outbox 管线

**Spec:** `docs/superpowers/specs/2026-08-27-world-knowledge-and-game-version-grounding-design.md`

## Global Constraints

- 前置计划 `2026-08-28-game-version-public-facts.md` 的 Gate 2 必须通过。
- 首次正式放量只允许 DIRECT / CONTINUATION；AMBIENT 搜索仍禁用。
- 最终回复模型永远拿不到搜索工具，检索 Agent 永远拿不到 Persona/Profile/关系数据或发送能力。
- DIRECT / CONTINUATION 软超时 3 秒、硬超时 5 秒；每轮最多 2 查询、每查询最多 4 结果。
- 全实例最多 20 次/小时、100 次/日；同一规范查询 single-flight；成功结果缓存 10 分钟。
- 任何网络 I/O 必须发生在按群 reply lock 之外，且不能持有 SQLite 写事务。
- 搜索完成后 scene version、target、conversation lease、intention expiry、knowledge expiry 任一变化都不得发送。
- strict 模式下版本号、日期、数字、专名与状态断言只能来自本地替换的 fragment；模型不能生成 URL。
- 审查最多修复一次；第二次失败 DIRECT / CONTINUATION 使用固定失败关闭语义，不能二次搜索或新增事实。
- 未完成本计划 Gate 3 前，所有网页结果只能 SHADOW。

---

## 文件结构

### 新建

- `groupmate/social_runtime/knowledge/search.py`：DiscoverySearchPort、查询规范化、缓存和安全结果契约。
- `groupmate/social_runtime/knowledge/enrichment.py`：need→probe/search→admission→revalidation 编排。
- `groupmate/social_runtime/knowledge/snapshot.py`：冻结事实选择、TTL 和 revision。
- `groupmate/social_runtime/knowledge/grounding.py`：risk classifier、strict renderer、assembler 与知识审查。
- `groupmate/adapters/astrbot_knowledge_search.py`：受限 ToolSet/专用检索 Agent adapter。
- `tests/social_runtime/knowledge/test_search.py`
- `tests/social_runtime/knowledge/test_enrichment.py`
- `tests/social_runtime/knowledge/test_snapshot.py`
- `tests/social_runtime/knowledge/test_grounding.py`
- `tests/contracts/test_astrbot_knowledge_search.py`
- `tests/recovery/test_knowledge_enrichment_recovery.py`
- `tests/scenarios/test_game_grounded_reply.py`
- `scenarios/game_version_grounding.jsonl`

### 修改

- `groupmate/social_runtime/knowledge/contracts.py`：KnowledgeFact、fragment、snapshot、enrichment result。
- `groupmate/social_runtime/knowledge/repository.py`：usage/cache/job/snapshot source lookup。
- `groupmate/social_runtime/knowledge/admission.py`：discovery evidence 准入。
- `groupmate/social_runtime/social_moves.py`：knowledge policy 与独立 knowledge ID 集。
- `groupmate/social_runtime/replying.py`：ReplyPlan 快照引用、strict parts 生成/修复/组装。
- `groupmate/social_runtime/social_review.py`：知识 reviewer 接入，不混淆 social fact IDs。
- `groupmate/adapters/astrbot_bridge.py`：按群锁、enrichment、重验和放量编排。
- `groupmate/social_runtime/manager.py`：只读 scene revision/guard 接口。
- `groupmate/social_runtime/control/message_traces.py`：搜索和 grounding 摘要。
- `tests/social_runtime/actions/test_social_review.py`
- `tests/social_runtime/actions/test_replying.py`
- `tests/social_runtime/test_social_moves.py`
- `tests/contracts/test_message_traces.py`

---

### Task 1: 固定搜索、快照与严格片段契约

**Files:**
- Modify: `groupmate/social_runtime/knowledge/contracts.py`
- Create: `groupmate/social_runtime/knowledge/search.py`
- Create: `groupmate/social_runtime/knowledge/snapshot.py`
- Create: `tests/social_runtime/knowledge/test_search.py`
- Create: `tests/social_runtime/knowledge/test_snapshot.py`

**Interfaces:**
- `DiscoverySearchPort.search(request) -> DiscoverySearchResult`。
- `SearchRequest` 只含 game/entity/query intent、region/platform、max_results、deadline；后台长尾任务可带从单个观察裁剪出的 `entity_hint`（NFKC 后 1–48 字），但不含 raw chat/profile/persona。
- `KnowledgeSnapshotBuilder.build(frame, need, hits, now) -> KnowledgeSnapshot`。
- `KnowledgeFact` 明确 `risk_class`、`evidence_level`、`qualifier`、`checked_at/expires_at` 和 source IDs。

- [x] **Step 1: 写 search request/result 失败测试**

拒绝 raw message、任意 query 字符串、私密字段、超过 2 个 intents、超过 4 results、非安全 URL、网页正文、未知 source class、deadline 已过和过长 evidence excerpt。

- [x] **Step 2: 写 snapshot 冻结失败测试**

快照 ID 对 canonical 内容稳定；过期 claim、disputed claim、错误版本/区服/平台不进入 allowed facts；strict 风险必须有 fragment；source IDs 必须属于 facts；后台 repository 改变不改变已构建对象。

```python
def test_snapshot_does_not_change_after_repository_update(repository):
    snapshot = builder.build(frame, need, repository.hits(), now=100)
    repository.supersede("claim:1", checked_at=101)
    assert snapshot.allowed_knowledge_facts[0].knowledge_id == "claim:1"
    assert snapshot.version_state_revision == 3
```

- [x] **Step 3: 运行测试并确认 RED**

Run: `.venv/bin/python -m pytest -q tests/social_runtime/knowledge/test_search.py tests/social_runtime/knowledge/test_snapshot.py`

Expected: search/snapshot 模块不存在。

- [x] **Step 4: 实现受限 query intent 与快照构建器**

查询由本地模板从 canonical game/entity/intent 生成，支持 `official_next_version`、`official_recent_update`、`rumor_next_version`、`named_fact_verification`；只有 `unknown_entity_learning` 后台任务可插入隔离后的 `entity_hint`，其他路径不直接拼接群消息。快照最多 8 facts、8 fragments、2 sources，TTL 取最短 evidence freshness 与 intention expiry。

- [x] **Step 5: 运行测试并提交**

Run: `.venv/bin/python -m pytest -q tests/social_runtime/knowledge/test_search.py tests/social_runtime/knowledge/test_snapshot.py`

Expected: PASS。

Commit: `git commit -m "feat: define bounded knowledge snapshots"`

---

### Task 2: 实现 AstrBot 受限检索 adapter

**Files:**
- Create: `groupmate/adapters/astrbot_knowledge_search.py`
- Create: `tests/contracts/test_astrbot_knowledge_search.py`
- Modify: `groupmate/adapters/astrbot_bridge.py`

**Interfaces:**
- `AstrBotKnowledgeSearch(context, allowed_tool_names, timeout_seconds=5)`。
- `.search(request) -> DiscoverySearchResult` 通过显式 ToolSet 和 `tool_loop_agent`。
- readiness code: `knowledge_search_adapter_unavailable`。

- [ ] **Step 1: 写 fake ToolSet 失败契约测试**

断言 adapter 只选择管理员已配置的搜索和页面提取工具；最终 reply provider 没有 tools；专用 Agent 最大 steps=4；查询/result 数受 contract 限制；tool 请求发送消息、文件、代码、内网 URL 时拒绝。

- [ ] **Step 2: 写版本兼容与失败映射测试**

覆盖 AstrBot 4.24 可用签名、能力缺失、function calling 不支持、timeout、rate limit、部分 tool failure、malformed JSON、prompt injection 页面。所有异常映射固定 code，不返回原异常或 key。

- [ ] **Step 3: 运行契约测试并确认 RED**

Run: `.venv/bin/python -m pytest -q tests/contracts/test_astrbot_knowledge_search.py tests/shared/test_astrbot_package_loading.py`

Expected: adapter 缺失。

- [ ] **Step 4: 实现延迟绑定的专用 Agent**

Bridge 启动时从 host context 解析当前启用工具，白名单仅接受 search/page-extract capability；adapter 构造单轮 system instruction，明确网页指令无效，只输出 JSON source candidates。禁止 HTTP 自调用与插件自存 API key。

- [ ] **Step 5: 规范化并二次验证 tool 输出**

每个候选经 URL policy、domain/source classification、长度限制、hash 和发布时间 parser；完整页面不越过 adapter。结果不可信时 `invalid_result`，而不是退回模型自由文本。

- [ ] **Step 6: 运行测试并提交**

Run: `.venv/bin/python -m pytest -q tests/contracts/test_astrbot_knowledge_search.py tests/shared/test_astrbot_package_loading.py`

Expected: PASS。

Commit: `git commit -m "feat: adapt astrbot knowledge search"`

---

### Task 3: 实现额度、single-flight 与 enrichment coordinator

**Files:**
- Create: `groupmate/social_runtime/knowledge/enrichment.py`
- Create: `tests/social_runtime/knowledge/test_enrichment.py`
- Modify: `groupmate/social_runtime/knowledge/repository.py`
- Modify: `groupmate/social_runtime/knowledge/admission.py`
- Create: `tests/recovery/test_knowledge_enrichment_recovery.py`

**Interfaces:**
- `KnowledgeEnrichmentCoordinator.enrich(request) -> EnrichmentResult`。
- `EnrichmentRequest` 固定 lane、scene guard、frame、need、soft/hard deadline。
- `SceneGuard` 包含 group_id、scene_version、target_id、lease_id/expiry、intention_id/expiry。
- `EnrichmentResult` 分开 `knowledge_committed` 与 `reply_still_valid`。

- [ ] **Step 1: 写 lane 和缓存失败测试**

DIRECT/CONTINUATION 最多 5s，AMBIENT 在本阶段返回 `ambient_search_disabled`；相同 normalized request 并发只调用 provider 一次；10 分钟成功缓存可复用；失败/partial 不缓存为成功；官方新证据使 negative cache 失效。

- [ ] **Step 2: 写额度与队列失败测试**

小时 20、日 100，跨小时/Asia-Shanghai 日期正确重置；额度预留原子；provider 未实际调用时释放 reservation；排队等待也计入 hard deadline；日志只写 intent hash、domain、latency、cache hit 和 diagnostic。

- [ ] **Step 3: 写场景过期恢复失败测试**

搜索成功后 scene 前进：知识照常 admission，但 `reply_still_valid=False`；进程在 search 成功/commit 前崩溃，稳定 job key 恢复且 source hash 幂等；不得恢复历史 reply。

- [ ] **Step 4: 运行测试并确认 RED**

Run: `.venv/bin/python -m pytest -q tests/social_runtime/knowledge/test_enrichment.py tests/recovery/test_knowledge_enrichment_recovery.py`

Expected: coordinator 缺失。

- [ ] **Step 5: 实现实例级 semaphore 与 single-flight**

semaphore 只包 provider I/O；single-flight map 在 `finally` 清理；等待者各自重验 deadline。流程固定为 official probe→若用户 intent 是 rumor 才 discovery search→admission short transaction→scene guard callback→snapshot build。

- [ ] **Step 6: 实现持久 usage/job 语义**

每次 provider call 原子消耗额度并记录 safe usage；缓存命中不消耗 provider quota；running job 只代表可恢复的知识补全，不代表待发送回复。过期 reply guard 不回滚已经验证的知识。

- [ ] **Step 7: 运行测试并提交**

Run: `.venv/bin/python -m pytest -q tests/social_runtime/knowledge/test_enrichment.py tests/recovery/test_knowledge_enrichment_recovery.py`

Expected: PASS。

Commit: `git commit -m "feat: coordinate bounded knowledge enrichment"`

---

### Task 4: 将全局 reply lock 改为按群锁并在锁外核验

**Files:**
- Modify: `groupmate/adapters/astrbot_bridge.py`
- Modify: `groupmate/social_runtime/manager.py`
- Create: `tests/scenarios/test_parallel_knowledge_enrichment.py`
- Modify: `tests/scenarios/test_parallel_topic_governance.py`

**Interfaces:**
- Bridge: `_reply_locks: dict[str, asyncio.Lock]` 和有界 idle cleanup。
- Manager: `current_scene_guard(group_id, evaluation) -> SceneGuardCheck`，只读最新 GroupWorld/lease。
- `_handle_evaluations()` 对每个 ACT evaluation 先 enrichment，后重验，再进入对应 group lock 规划/生成/Outbox。

- [ ] **Step 1: 写网络不持锁失败测试**

阻塞 g1 search 时，g2 direct reply 能完成；同群 g1 第二轮保持顺序；fake search port 检查调用时对应 reply lock 未锁；全局 search semaphore 仍限制跨群 provider 并发。

- [ ] **Step 2: 写四重重验失败测试**

分别推进 scene version、改变 target、终止 continuation lease、越过 intention expiry、使 snapshot TTL 过期；每种都不创建 ReplyPlan/Outbox，trace 标相应 code，知识结果仍可提交。

- [ ] **Step 3: 运行并确认 RED**

Run: `.venv/bin/python -m pytest -q tests/scenarios/test_parallel_knowledge_enrichment.py tests/scenarios/test_parallel_topic_governance.py`

Expected: 当前 `_reply_lock` 是全局锁，场景核验接口缺失。

- [ ] **Step 4: 实现按群锁生命周期**

使用 `defaultdict` 替代全局 lock，但锁对象通过受保护 registry 获取；当 lock 未持有且无 waiter 时清理。排序仍优先 FAST，但不同群允许并行；同群 evaluation 按 scene_version/source time 串行。

- [ ] **Step 5: 重排 Bridge 执行边界**

OBSERVE 和无需知识 ACT 不搜索；需补全 ACT 在锁外 await coordinator；重验成功后才进入 group lock，并在锁内再次快速重验一次，随后执行现有 scene→stance→move→reply→outbox。

- [ ] **Step 6: 运行测试并提交**

Run: `.venv/bin/python -m pytest -q tests/scenarios/test_parallel_knowledge_enrichment.py tests/scenarios/test_parallel_topic_governance.py tests/recovery/test_stale_cognition.py`

Expected: PASS。

Commit: `git commit -m "refactor: isolate group reply lanes"`

---

### Task 5: 扩展 SocialMove 与 ReplyPlan 的知识授权

**Files:**
- Modify: `groupmate/social_runtime/social_moves.py`
- Modify: `groupmate/social_runtime/replying.py`
- Modify: `tests/social_runtime/test_social_moves.py`
- Modify: `tests/social_runtime/actions/test_replying.py`

**Interfaces:**
- `KnowledgePolicy`: `none`、`grounded`、`strict`。
- `SocialMovePlan` 增加 `knowledge_policy`、`must_use_knowledge_ids`、`may_use_knowledge_ids`、`prohibited_assertion_classes`。
- `ReplyPlan` 增加 `knowledge_snapshot`；repository encode/decode 保留旧 plan 兼容。
- `covered_fact_ids` 与 `used_knowledge_ids` 完全分离。

- [ ] **Step 1: 写 move 不变量失败测试**

SILENCE/JOIN_CHORUS 必须 knowledge none；strict 必须有 snapshot 和 required IDs；required/may 必须是 snapshot 子集；risk fact 不能用 grounded；knowledge IDs 不能出现在 must_say DecisionFact；未知 assertion class 拒绝。

- [ ] **Step 2: 写 ReplyPlan 持久兼容失败测试**

新 plan round-trip 保留 snapshot revision/TTL；旧 JSON 缺字段默认 knowledge none；过期 snapshot 无法 authorize bundle；plan identity hash 包含 snapshot ID/revision，防止换证据复用旧 plan。

- [ ] **Step 3: 运行并确认 RED**

Run: `.venv/bin/python -m pytest -q tests/social_runtime/test_social_moves.py tests/social_runtime/actions/test_replying.py`

Expected: 新字段/enum 缺失。

- [ ] **Step 4: 实现加法契约与 Planner 选择规则**

local stable semantic 可 grounded；版本/日期/清单/数字/status 自动 strict；need unresolvable 的 direct move 变为只问一个歧义问题且 knowledge none；证据不足时禁止 assertion class，并由 executor 走固定失败关闭语义。

- [ ] **Step 5: 运行测试并提交**

Run: `.venv/bin/python -m pytest -q tests/social_runtime/test_social_moves.py tests/social_runtime/actions/test_replying.py tests/recovery/test_delivery_recovery.py`

Expected: PASS。

Commit: `git commit -m "feat: authorize grounded reply knowledge"`

---

### Task 6: 实现 strict renderer、parts assembler 和 GroundedReplyReviewer

**Files:**
- Create: `groupmate/social_runtime/knowledge/grounding.py`
- Create: `tests/social_runtime/knowledge/test_grounding.py`
- Modify: `groupmate/social_runtime/replying.py`
- Modify: `groupmate/social_runtime/social_review.py`
- Modify: `tests/social_runtime/actions/test_replying.py`
- Modify: `tests/social_runtime/actions/test_social_review.py`

**Interfaces:**
- `KnowledgeFactRenderer.render(snapshot) -> tuple[StrictFactFragment, ...]`。
- `StrictReplyAssembler.assemble(parts, snapshot, required_ids) -> RealizedReply`。
- `GroundedReplyReviewer.review(reply, plan, now, current_revision) -> KnowledgeReview`。
- `RealizedReply.used_knowledge_ids: tuple[str, ...]`。

- [ ] **Step 1: 写 renderer 限定语失败测试**

官方 preview、released、unofficial rumor、conflicted、negative official/search 使用各自固定措辞；renderer 不接受 stale/disputed 作为肯定事实；URL 只由 source ID 本地组装且最多 2 条。

- [ ] **Step 2: 写 strict parts 攻击失败测试**

拒绝未知/重复 fragment、漏 required、text part 中新增数字/日期/版本号/专名/status、模型 URL、顺序越界、总字数超限、fragment 文本被改写。允许纯语气连接如“那目前只能说：”。

```json
{
  "parts": [
    {"kind": "text", "text": "那目前只能说："},
    {"kind": "knowledge_fragment", "fragment_id": "fragment:official-negative:1"}
  ],
  "used_knowledge_ids": ["negative:official-next-version:1"]
}
```

- [ ] **Step 3: 写 grounded 封闭审查失败测试**

稳定背景自然转述必须声明 snapshot 中 IDs；review model 只看到 reply + allowed evidence，不能看到网页/工具；unsupported assertion、官方/rumor 混淆和 snapshot revision 变化失败。一次修复后仍失败使用固定 fallback。

- [ ] **Step 4: 运行并确认 RED**

Run: `.venv/bin/python -m pytest -q tests/social_runtime/knowledge/test_grounding.py tests/social_runtime/actions/test_replying.py tests/social_runtime/actions/test_social_review.py`

Expected: grounding 模块和 knowledge review 缺失。

- [ ] **Step 5: 实现 deterministic strict 路径**

ReplyExecutor 在 strict 时要求 JSON parts，不走现有完整 text parser；assembler 替换 fragment ID 后产生最终 text 和 used IDs。数字/日期/知识域专名 allowlist 从 fragment tokens 生成；SceneContext 已验证的称呼只进入独立 address allowlist，允许自然称呼群成员但不能引入新游戏实体。text part 执行 Unicode 规范化后检查。

- [ ] **Step 6: 实现审查顺序与单次修复**

顺序固定为 parse/assemble→GroundedReplyReviewer→现有 SocialOutputReviewer→OutputFirewall→enqueue。修复 Prompt 只包含 violation code 和允许结构；不得搜索。第二次失败 ambient（未来）沉默，direct/continuation 返回不带外部事实的固定“我现在没核实到可靠信息，先不乱说”。

- [ ] **Step 7: 运行测试并提交**

Run: `.venv/bin/python -m pytest -q tests/social_runtime/knowledge/test_grounding.py tests/social_runtime/actions/test_replying.py tests/social_runtime/actions/test_social_review.py`

Expected: PASS。

Commit: `git commit -m "feat: render strict grounded game facts"`

---

### Task 7: SHADOW、DIRECT / CONTINUATION 放量与 Gate 3

**Files:**
- Create: `tests/scenarios/test_game_grounded_reply.py`
- Create: `scenarios/game_version_grounding.jsonl`
- Modify: `groupmate/social_runtime/control/message_traces.py`
- Modify: `tests/contracts/test_message_traces.py`
- Modify: `groupmate/adapters/astrbot_bridge.py`

**Interfaces:**
- Bridge 按现有 `runtime_mode` 派生知识发送范围：`SHADOW` 只生成 preview，`SOCIAL_RUNTIME` 允许 DIRECT / CONTINUATION；AMBIENT 在本阶段代码路径固定关闭，不增加普通配置项。
- trace 显示 need、cache/search、source domains、evidence level、snapshot/fragment IDs、revalidation 与 review code。

- [ ] **Step 1: 建立冻结根据回复场景**

至少覆盖五款游戏各 8 条：本地稳定知识、最新版本、下一版本未披露、已 preview、rumor 无结果、rumor 有非官方证据、来源冲突、搜索失败；另含 continuation 复用、用户索要来源、场景前进和插件命令对照。

- [ ] **Step 2: 写 SHADOW side-effect 失败测试**

SHADOW 可完整执行 search/admission/snapshot/render/review preview，但 outbox 与平台 sender 为 0；trace 可人工审查本来会使用的 fragment。`SOCIAL_RUNTIME` 仅开放 DIRECT / CONTINUATION，AMBIENT 搜索固定关闭。

- [ ] **Step 3: 写 direct/continuation 正式路径失败测试**

仅 direct/continuation 可在 rollout 后 enqueue；ambient 即使相同问题也不 search/send；search adapter off 时稳定语义可用，高风险问题固定失败关闭；要求来源最多 2 条且来自 snapshot。

- [ ] **Step 4: 运行场景并修复实现**

Run: `.venv/bin/python -m pytest -q tests/scenarios/test_game_grounded_reply.py tests/contracts/test_message_traces.py`

Expected: 初次因 rollout/trace 缺失失败；实现后 PASS。

- [ ] **Step 5: 运行 Gate 3**

Run: `.venv/bin/python -m pytest -q tests/social_runtime/knowledge tests/contracts/test_astrbot_knowledge_search.py tests/recovery/test_knowledge_enrichment_recovery.py tests/scenarios/test_game_grounded_reply.py`

Expected: PASS；`unsupported_temporal_claims=0`、`rumor_as_official=0`、`false_negative_claims=0`、`stale_scene_sends=0`、`search_under_reply_lock=0`。

- [ ] **Step 6: 全量回归和提交**

Run: `.venv/bin/python -m pytest -q`

Expected: PASS。

Run: `git diff --check`

Expected: 无输出。

Commit: `git commit -m "feat: ship grounded direct game replies"`

---

## 子项目验收

- DIRECT / CONTINUATION 的新版本问题会即时核验，但任何搜索失败都不会被说成“官方没有”。
- 事实补全在 reply lock 外完成，不阻塞其他群；同群仍保持回复顺序。
- 搜索后 scene、target、lease、intention 和 snapshot 五类过期均失败关闭。
- 高风险事实只能通过冻结 fragment，最终模型不能编造 URL、数字、版本和状态。
- AMBIENT 即时搜索仍为关闭状态。

通过后进入：`docs/superpowers/plans/2026-08-28-game-knowledge-ambient-expansion.md`。
