# Groupmate AMBIENT 游戏知识持续扩展 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在前三阶段安全边界全部成立后，谨慎开放 AMBIENT 有界即时核验、非预装游戏后台学习、群约定人工纠正、完整知识控制面和容量 canary，使每个群形成自己的热门游戏理解而不把 Bot 变成抢答百科。

**Architecture:** AMBIENT 仍先用本地 frame 完成 cognition/participation/Governor，只有已获 ACT 且剩余 attention/intent 预算充足才调用 2 秒 enrichment lane；否则沉默并创建后台任务。长尾学习由 group affinity 触发，经过 discovery→admission 后分别写全局稳定语义候选和群作用域约定，不能由聊天频次生成 public fact。控制面通过现有 Web API/消息 trace 架构提供只读投影与审计式纠正，最终以 installed-live SHADOW 和单群 canary 放量。

**Tech Stack:** Python 3.11+、asyncio、SQLite/WAL、AstrBot ToolSet、原生 ES Modules、pytest、现有 Settings SPA/SSE 控制面

**Spec:** `docs/superpowers/specs/2026-08-27-world-knowledge-and-game-version-grounding-design.md`

## Global Constraints

- 前置计划 `2026-08-28-game-knowledge-grounded-replies.md` 的 Gate 3 必须通过。
- AMBIENT 总参与窗口仍是 8 秒；即时搜索硬超时 2 秒，且必须小于 scene/intent 剩余预算。
- AMBIENT 搜索不能提高参与概率：必须先由本地 cognition 与 Governor 独立得到 ACT。
- 排队、超时、证据不足、场景前进和审查失败一律沉默；不得用固定失败文案制造插话。
- 非知识普通 AMBIENT 消息、OBSERVE、外部插件消息不搜索。
- 长尾群热度阈值按群计算，不跨群，不带 Persona；群热度不是事实可信度。
- 聊天重复只能激活群约定/学习任务；全局 stable/public claim 仍需公开来源准入。
- 管理员纠正是新增审计观察和 supersede/reject 状态变化，不物理删除来源历史。
- 控制页不显示网页正文、完整查询、作者原始 ID、Profile/关系数据、密钥或模型原始输出。
- canary 可随时关闭 AMBIENT 搜索并退回 DIRECT / CONTINUATION，不回滚已验证知识。

---

## 文件结构

### 新建

- `groupmate/social_runtime/knowledge/learning.py`：未知实体、长尾语义和群热门预热策略。
- `groupmate/social_runtime/control/knowledge.py`：知识只读投影、纠正命令和审计 DTO。
- `pages/settings/workspaces/knowledge.js`：知识运行与管理工作区。
- `tests/social_runtime/knowledge/test_learning.py`
- `tests/contracts/test_knowledge_admin.py`
- `tests/contracts/test_knowledge_web_api.py`
- `tests/page/test_knowledge_workspace.py`
- `tests/scenarios/test_ambient_game_knowledge.py`
- `tests/evaluation/test_knowledge_capacity.py`
- `docs/operations/game-knowledge-rollout.md`

### 修改

- `groupmate/social_runtime/knowledge/enrichment.py`：开放严格 AMBIENT lane。
- `groupmate/social_runtime/knowledge/jobs.py`：未知实体、群热门预热和长尾按需刷新任务。
- `groupmate/social_runtime/knowledge/observation.py`：学习 task 准入。
- `groupmate/social_runtime/knowledge/repository.py`：长尾 lifecycle、retention、admin audit 查询。
- `groupmate/adapters/astrbot_bridge.py`：AMBIENT rollout/canary 与运行状态。
- `groupmate/adapters/web_api.py`：知识 query/correction endpoints。
- `groupmate/social_runtime/control/commands.py`：管理员纠正命令契约。
- `groupmate/social_runtime/control/message_traces.py`：AMBIENT budget/降级摘要。
- `pages/settings/index.html`
- `pages/settings/router.js`
- `pages/settings/store.js`
- `pages/settings/app.js`
- `pages/settings/i18n.js`
- `pages/settings/styles/components.css`
- `tests/contracts/test_commands.py`
- `tests/contracts/test_message_traces.py`
- `tests/evaluation/test_load_budget.py`
- `docs/operations/social-runtime-capacity.md`

---

### Task 1: 开放严格预算内的 AMBIENT enrichment lane

**Files:**
- Modify: `groupmate/social_runtime/knowledge/enrichment.py`
- Modify: `groupmate/adapters/astrbot_bridge.py`
- Create: `tests/scenarios/test_ambient_game_knowledge.py`
- Modify: `tests/social_runtime/knowledge/test_enrichment.py`

**Interfaces:**
- `AmbientBudget.from_evaluation(evaluation, now) -> AmbientBudget`。
- coordinator 接受 `lane="AMBIENT"` 时，只有 `remaining_ms >= 2250` 才尝试搜索；provider timeout 固定 2000ms，余下 250ms 留给提交、重验和审查。
- `KnowledgeRolloutPolicy` 从现有 `governance_actions` 读取每群 `knowledge.ambient_canary_enabled` 状态；默认 false，不增加普通配置项。

- [x] **Step 1: 写先授权后搜索失败测试**

Governor OBSERVE/SILENCE 时 provider calls=0；ACT + local sufficient 时 calls=0；ACT + fresh need + 充足预算时最多一次 enrichment；知识 hit 本身不能把 OBSERVE 改 ACT。

- [x] **Step 2: 写 8 秒预算边界失败测试**

剩余 2249ms 不搜索并沉默/排后台任务；2250ms 可进入但 hard timeout 2000ms；semaphore 排队耗尽预算不调用 provider；搜索完成后剩余时间不足也不生成回复。

- [x] **Step 3: 写失败关闭场景**

timeout、partial、empty without valid negative、disputed、scene advanced、target changed、review failed 都 outbox=0；已验证本地 fresh fact 可不搜索正常参与。

- [x] **Step 4: 运行并确认 RED**

Run: `.venv/bin/python -m pytest -q tests/social_runtime/knowledge/test_enrichment.py tests/scenarios/test_ambient_game_knowledge.py`

Expected: 当前 coordinator 返回 `ambient_search_disabled`。

- [x] **Step 5: 实现预算对象和 canary gate**

预算来自 evaluation attention deadline、candidate intention expiry 和当前 monotonic/epoch clock 的安全最小值；只对已有有效 `knowledge.ambient_canary_enabled` 治理动作的群开放。无论配置 `knowledge_web_search_enabled` 如何，未进入 canary 的群都不执行 ambient search。

- [x] **Step 6: 运行测试并提交**

Run: `.venv/bin/python -m pytest -q tests/social_runtime/knowledge/test_enrichment.py tests/scenarios/test_ambient_game_knowledge.py tests/scenarios/test_attention_windows.py`

Expected: PASS。

Commit: `git commit -m "feat: gate ambient game knowledge search"`

---

### Task 2: 实现非预装游戏的群热门触发与后台学习

> 2026-08-31 执行修订：根据产品优先级，本 Task 提前于 AMBIENT 放量执行。本轮完成群热门触发、稳定语义学习和 retention；`long_tail_official_refresh` 等管理员来源注册完成后再开放，期间长尾时效问题继续失败关闭。

**Files:**
- Create: `groupmate/social_runtime/knowledge/learning.py`
- Create: `tests/social_runtime/knowledge/test_learning.py`
- Modify: `groupmate/social_runtime/knowledge/observation.py`
- Modify: `groupmate/social_runtime/knowledge/jobs.py`
- Modify: `groupmate/social_runtime/knowledge/repository.py`

**Interfaces:**
- `KnowledgeLearningPolicy.evaluate(observation, affinity, now) -> LearningDecision`。
- New job kinds: `unknown_entity_learning`、`group_topic_warmup`、`long_tail_official_refresh`。
- 热门自动阈值：滚动 7 天内至少 8 次 qualified mentions、3 个 distinct human actors、4 个 distinct scenes；管理员确认可直接预热但不能直接激活 public fact。

- [x] **Step 1: 写长尾触发失败测试**

同一成员刷 20 次、不足 4 场景、Bot/forward/command、跨群合并都不触发；满足 8×3×4 时只创建一个稳定 job。已有长尾游戏的 official refresh 按上方执行修订延后。

- [x] **Step 2: 写作用域分流失败测试**

群独有外号只成为 group convention candidate；公开来源确认的游戏 canonical entity/stable genre 才可全局；群聊声称“新角色”不能变 public claim；两个群分别热议同游戏可复用全局 entity，但 affinity 独立。

- [x] **Step 3: 写学习结果失败与恢复测试**

搜索无可靠来源、冲突、adapter unavailable 保持 pending/retry；成功学习至少要求 canonical game identity 和一条公开稳定语义证据；不得建立当前版本结论；job 重启幂等。

- [x] **Step 4: 运行并确认 RED**

Run: `.venv/bin/python -m pytest -q tests/social_runtime/knowledge/test_learning.py tests/recovery/test_knowledge_job_recovery.py`

Expected: learning policy/job kinds 缺失。

- [x] **Step 5: 实现 background-only 学习编排**

消息主链只 append observation + enqueue stable job key；后台 job 使用 discovery Port，`SearchRequest.entity_hint` 只能取单个安全观察中 NFKC 后 1–48 字的未知表达，不能携带整句群聊。结果经过同一 admission policy。新长尾游戏不进入 seed daily baseline；官方 refresh 按上方执行修订延后。

- [x] **Step 6: 实现保留策略**

180 天未激活低信任观察裁剪 safe summary；30 天 rejected search candidate 裁剪 excerpt；90 天未用群约定 stale；公共已验证事实和审计关系不删除。清群只删 group convention/affinity/opaque author refs。

- [x] **Step 7: 运行测试并提交**

Run: `.venv/bin/python -m pytest -q tests/social_runtime/knowledge/test_learning.py tests/social_runtime/knowledge/test_observation.py tests/recovery/test_knowledge_job_recovery.py tests/shared/test_group_scope_privacy.py`

Expected: PASS。

Commit: `git commit -m "feat: learn long tail game semantics"`

---

### Task 3: 实现知识管理查询与审计式纠正

**Files:**
- Create: `groupmate/social_runtime/control/knowledge.py`
- Create: `tests/contracts/test_knowledge_admin.py`
- Modify: `groupmate/social_runtime/control/commands.py`
- Modify: `tests/contracts/test_commands.py`
- Modify: `groupmate/social_runtime/knowledge/repository.py`

**Interfaces:**
- `KnowledgeControlQueries.overview(group_id, now)`、`.entities(group_id, cursor, filters)`、`.claims(group_id, cursor, filters)`、`.conventions(group_id, cursor, filters)`、`.jobs(group_id, cursor, filters)`。
- Admin commands: reject convention、confirm convention、supersede alias、mark claim disputed、retry job、invalidate cache、enable/disable ambient canary。
- 所有 mutation 要求 `actor_id in control_admin_ids`、reason、expected revision，并 append `origin_class=admin` observation/audit record。

- [x] **Step 1: 写只读投影安全失败测试**

输出 canonical entity、作用域、status、source domain/class、checked/fresh time、group salience、diagnostic；不输出 URL query、excerpt、完整搜索 query、author ref、raw event text、Persona/Profile/关系数据。

- [x] **Step 2: 写权限/并发失败测试**

非管理员拒绝；跨群 convention target 拒绝；stale expected revision 返回 conflict；重复 request id 幂等；管理员确认群 alias 不产生 global alias；reject/supersede 保留旧记录。

- [x] **Step 3: 运行并确认 RED**

Run: `.venv/bin/python -m pytest -q tests/contracts/test_knowledge_admin.py tests/contracts/test_commands.py`

Expected: control query/commands 缺失。

- [x] **Step 4: 实现 query DTO 与 mutation handler**

使用现有 control 命令 envelope、治理审计和 revision 风格；每次 mutation 一个短事务，先 append admin observation，再状态迁移。`retry job` 只把 eligible failed/retry job 的 `next_attempt_at` 设为 now，不直接做网络调用。AMBIENT canary 写入现有 `governance_actions`，动作类型固定为 `knowledge.ambient_canary_enabled`，按 group 读取最新有效 revision。

- [x] **Step 5: 运行测试并提交**

Run: `.venv/bin/python -m pytest -q tests/contracts/test_knowledge_admin.py tests/contracts/test_commands.py tests/shared/test_group_scope_privacy.py`

Expected: PASS。

Commit: `git commit -m "feat: administer scoped game knowledge"`

---

### Task 4: 增加知识 Web API 与前端工作区

**Files:**
- Modify: `groupmate/adapters/web_api.py`
- Create: `tests/contracts/test_knowledge_web_api.py`
- Create: `pages/settings/workspaces/knowledge.js`
- Modify: `pages/settings/index.html`
- Modify: `pages/settings/router.js`
- Modify: `pages/settings/store.js`
- Modify: `pages/settings/app.js`
- Modify: `pages/settings/i18n.js`
- Modify: `pages/settings/styles/components.css`
- Create: `tests/page/test_knowledge_workspace.py`

**Interfaces:**
- 全局共享 GET：`/api/groupmate/knowledge/library/{overview|entities|claims|entity-detail|jobs}`，不依赖当前群。
- 群级 GET：`/api/groupmate/knowledge/group/{overview|aliases|conventions|entity-context|usage|jobs}?group_id={group_id}`。
- mutation 分为 `/api/groupmate/knowledge/library/actions` 与 `/api/groupmate/knowledge/group/actions`，沿用现有管理员 token/origin/CSRF 安全边界。
- Workspace 默认“本群认知”，另设“共享知识库”；详情同时组合共享公开知识与当前群语境，但保持字段隔离。
- 共享实体按 `canonical_game_id` 自动归到所属游戏；列表显示所属游戏，详情展示同游戏相关实体与公开证据链。

- [x] **Step 1: 写 API 安全契约失败测试**

缺 group、非法 cursor、跨群、未授权 mutation、错误 content type、stale revision、超大 payload 拒绝；分页 cursor 不泄漏 rowid；响应头沿用现有 no-store/security policy。

- [x] **Step 2: 写前端静态/交互失败测试**

导航可达、窄屏无横向溢出、empty/loading/error states、filter/pagination、confirm/reject/retry dialog、键盘焦点、status 不只靠颜色、所有动态文本用 `textContent`。

- [x] **Step 3: 运行并确认 RED**

Run: `.venv/bin/python -m pytest -q tests/contracts/test_knowledge_web_api.py tests/page/test_knowledge_workspace.py`

Expected: routes/workspace 缺失。

- [x] **Step 4: 实现薄 API adapter**

Web API 只验证请求并调用 Task 3 control service，不拼 SQL、不返回 domain objects。mutation 响应返回新 revision 与安全 audit ref；冲突用 409，权限用 403。

- [x] **Step 5: 实现工作区**

复用现有 store/router/component tokens；默认显示本群摘要而不是全局百科。版本卡明确“最近成功核验”与“最近尝试”，negative snapshot 使用限定性中文，不展示“网络上没有”。

- [x] **Step 6: 运行测试并提交**

Run: `.venv/bin/python -m pytest -q tests/contracts/test_knowledge_web_api.py tests/page/test_knowledge_workspace.py tests/page/test_accessibility_contract.py tests/page/test_frontend_security.py`

Expected: PASS。

Commit: `git commit -m "feat: add game knowledge workspace"`

---

### Task 5: 增加容量、诊断和 retention 验证

**Files:**
- Create: `tests/evaluation/test_knowledge_capacity.py`
- Modify: `tests/evaluation/test_load_budget.py`
- Modify: `groupmate/social_runtime/control/message_traces.py`
- Modify: `tests/contracts/test_message_traces.py`
- Modify: `docs/operations/social-runtime-capacity.md`

**Interfaces:**
- Metrics: local resolution latency、queue depth、provider calls/cache hits、quota rejects、job lag、freshness lag、scene invalidations、grounding rejects、ambient silence reason。
- Metrics label 不含 group/member/raw query/entity free text；group 级细节只在认证控制查询中按需读取。

- [x] **Step 1: 写负载失败测试**

模拟 50 群并发普通消息：非知识 search=0，本地 resolver P95<50ms；20 个相同知识请求 provider calls=1；跨查询并发受 semaphore；reply locks 不产生跨群 head-of-line blocking。

- [x] **Step 2: 写数据库/retention 失败测试**

10 万 observations 下 group/entity 索引查询保持既定预算；retention batch 每次最多 500 行且短事务；清理期间正常 append/read 成功；WAL/busy timeout 不变。

- [x] **Step 3: 写 trace 诊断矩阵**

每个故障语义都有固定 code 和用户安全解释：unresolvable、adapter unavailable、timeout、empty、valid negative、disputed、stale、scene advanced、quota、ambient budget。未知 code 显示通用降级，不暴露 exception。

- [x] **Step 4: 运行并优化**

Run: `.venv/bin/python -m pytest -q tests/evaluation/test_knowledge_capacity.py tests/evaluation/test_load_budget.py tests/contracts/test_message_traces.py`

Expected: PASS，所有预算断言成立。

- [x] **Step 5: 更新容量手册并提交**

记录默认 20/hour、100/day、全局 search concurrency、2/5s timeout、queue/job/DB 告警阈值和关闭 AMBIENT 的恢复动作。

Commit: `git commit -m "perf: validate game knowledge capacity"`

---

### Task 6: installed-live SHADOW、单群 canary 与 Gate 4

**Files:**
- Create: `docs/operations/game-knowledge-rollout.md`
- Modify: `tests/scenarios/test_ambient_game_knowledge.py`
- Modify: `tests/evaluation/test_game_knowledge.py`
- Modify: `tests/evaluation/test_knowledge_capacity.py`

**Interfaces:**
- 运行手册固定阶段：fixture SHADOW→installed-live SHADOW→DIRECT/CONTINUATION 已有组→一个 AMBIENT canary 群→逐组扩大。
- 自动 rollback 条件：unsupported claim >0、stale scene send >0、cross-group leak >0、非知识 ambient search >0、provider quota 异常或参与率显著上升。

- [x] **Step 1: 写可重复的 installed-live 检查清单**

覆盖 seed import、四款 daily jobs、一次官方更新、一次 negative、一次 rumor、一次 provider timeout、一次场景过期、一个长尾游戏、一条群 alias 纠正；每项记录 trace ref，不保存群消息全文。

- [x] **Step 2: 写 canary 前后对比脚本/测试**

以同群最近 7 天 SHADOW baseline 比较参与率、ambient search rate、P95 latency、silence reason、unsupported claims；参与率相对上升超过 10% 或绝对上升超过 2 个百分点即停止扩大并人工复核。

- [x] **Step 3: 运行 Gate 4 自动部分**

Run: `.venv/bin/python -m pytest -q tests/social_runtime/knowledge tests/contracts/test_knowledge_admin.py tests/contracts/test_knowledge_web_api.py tests/page/test_knowledge_workspace.py tests/scenarios/test_ambient_game_knowledge.py tests/evaluation/test_knowledge_capacity.py`

Expected: PASS；非知识 ambient search=0、cross-group leaks=0、stale scene sends=0、unsupported claims=0。

- [ ] **Step 4: 运行全量回归**

Run: `.venv/bin/python -m pytest -q`

Expected: PASS。

2026-09-01：使用 `--import-mode=importlib` 完成全量收集，结果 1287 passed、11 failed；其中 5 项因当前沙箱禁止本地端口，另 6 项为本计划范围外的既有契约/前端断言。Gate 4 与本计划定向回归已通过，本步骤不伪标完成。

- [ ] **Step 5: 执行 installed-live SHADOW 人工门**

按手册在管理员已配置搜索源的真实 AstrBot 环境完成至少一个 24 小时刷新周期。人工确认每个 trace 的 source class、freshness、fragment 和失败语义；未完成此步骤不得切 `ambient_canary`。

- [ ] **Step 6: 开放单群 canary 并观察一个完整日界周期**

只为一个明确授权群提交 `knowledge.ambient_canary_enabled=true` 治理动作；出现 rollback 条件立即写入 false，DIRECT/CONTINUATION 保持。完成后保存匿名聚合验收记录。

- [x] **Step 7: 文档检查并提交**

Run: `git diff --check`

Expected: 无输出。

Commit: `git commit -m "docs: operationalize game knowledge rollout"`

---

## 子项目验收

- AMBIENT 只在本地已决定参与且有足够剩余预算时搜索，失败时沉默。
- 每个群能形成独立热门游戏和群约定；长尾 stable/public 知识仍由公开证据决定。
- 管理员可以审查、纠正和重试，但任何纠正保留审计历史。
- 控制面可解释知识来源、时效和降级，不泄漏原始群证据与查询正文。
- installed-live SHADOW 和单群 canary 完成后，才允许逐群开放 AMBIENT。

四个子项目全部完成后，按 `2026-08-28-game-knowledge-grounding-roadmap.md` 执行总体验证与发布门复核。
