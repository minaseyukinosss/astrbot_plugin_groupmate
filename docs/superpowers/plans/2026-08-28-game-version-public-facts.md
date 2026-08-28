# Groupmate 游戏版本公开事实 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在仍不正式输出网页事实的 SHADOW 阶段，为五款预装游戏建立可审计的公开 claim、来源、版本三轨、按天官方刷新和严格负向核验语义。

**Architecture:** 复用 schema v4 预建的 claim/source/release/job 表；领域层通过 `OfficialSourceProbePort` 获取规范化候选，`KnowledgeAdmissionPolicy` 决定证据等级、冲突、替代和有效期，`GameReleaseStateService` 独立维护发布、官方披露和 rumor 三条轨道。持久 scheduler 以 Asia/Shanghai 日期边界调度五款 baseline 游戏，只在完整成功时推进 `official_checked_at`。

**Tech Stack:** Python 3.11+、asyncio、SQLite/WAL、HTTP(S) 安全 URL 规范化、pytest/frozen fixtures、AstrBot adapter boundary

**Spec:** `docs/superpowers/specs/2026-08-27-world-knowledge-and-game-version-grounding-design.md`

## Global Constraints

- 前置计划 `2026-08-28-game-knowledge-local-cognition.md` 的 Gate 1 必须通过。
- 本计划可访问官方来源，但所有结果只进入 SHADOW、trace 和知识库，不进入正式回复事实。
- 日常任务只核验官方信息，不主动发现、整理或传播 rumor。
- `release_state`、`official_state`、`rumor_state` 是并行字段，禁止折叠成一个可信度状态。
- 失败、超时、限流、配置缺失和部分成功不得更新最后成功核验时间。
- 空结果不是“没有信息”；negative snapshot 只有完整策略成功且覆盖目标语义时才能创建。
- 官方证据可以 supersede 旧 claim，但不能改写旧 rumor 的来源类别或物理删除审计链。
- 网络结果是 untrusted input；领域层只接收已规范化的标题、发布者、时间、URL、短证据和 hash。

---

## 文件结构

### 新建

- `groupmate/social_runtime/knowledge/sources.py`：官方 probe Port、规范化结果和安全 URL policy。
- `groupmate/social_runtime/knowledge/admission.py`：来源分级、claim 准入、冲突和 supersede。
- `groupmate/social_runtime/knowledge/release_state.py`：版本槽位、三轨状态和相对版本解析。
- `groupmate/social_runtime/knowledge/jobs.py`：持久任务、日界调度、重试和恢复。
- `groupmate/adapters/astrbot_official_sources.py`：AstrBot 环境中的官方页面探测 adapter；只实现 Port。
- `tests/social_runtime/knowledge/test_sources.py`
- `tests/social_runtime/knowledge/test_admission.py`
- `tests/social_runtime/knowledge/test_release_state.py`
- `tests/social_runtime/knowledge/test_jobs.py`
- `tests/contracts/test_official_source_probe.py`
- `tests/recovery/test_knowledge_job_recovery.py`
- `tests/scenarios/test_game_release_shadow.py`

### 修改

- `groupmate/social_runtime/knowledge/contracts.py`：补充 source、claim、release、negative snapshot 契约。
- `groupmate/social_runtime/knowledge/repository.py`：实现预建表的 claim/source/release/job API。
- `groupmate/social_runtime/knowledge/seeds.py`：暴露每款游戏官方 source registry。
- `groupmate/social_runtime/knowledge/resolver.py`：将相对版本解析委托给 release state service。
- `groupmate/adapters/astrbot_bridge.py`：probe、admission、scheduler 组合与 lifecycle。
- `groupmate/social_runtime/control/message_traces.py`：官方核验 SHADOW 摘要。
- `tests/social_runtime/knowledge/test_repository.py`
- `tests/contracts/test_message_traces.py`

---

### Task 1: 固定公开来源、claim 和三轨版本契约

**Files:**
- Modify: `groupmate/social_runtime/knowledge/contracts.py`
- Create: `groupmate/social_runtime/knowledge/sources.py`
- Create: `tests/social_runtime/knowledge/test_sources.py`
- Modify: `tests/social_runtime/knowledge/test_contracts.py`

**Interfaces:**
- Produces: `OfficialSourceProbePort.probe(request) -> OfficialProbeResult`。
- Produces: `SourceEvidence`、`KnowledgeClaimCandidate`、`VersionSlot`、`NegativeSearchSnapshot`。
- `OfficialProbeResult.status` 只能是 `complete`、`partial`、`timed_out`、`unavailable`、`failed`。

- [x] **Step 1: 写契约失败测试**

覆盖：非 HTTP(S)、userinfo、fragment、回环、RFC1918、link-local、云元数据地址、过长 URL/标题/证据；官方 evidence 发布者与 seed registry 不匹配；negative snapshot 来自 partial probe；claim 缺 checked_at/validity；三轨非法组合。

```python
def test_negative_snapshot_requires_complete_covered_probe():
    with pytest.raises(ValueError, match="complete covered probe"):
        NegativeSearchSnapshot.create(
            snapshot_id="negative:1",
            game_entity_id="game:genshin-impact",
            query_intent="next_version_official",
            probe_status="partial",
            covered_source_ids=("source:official-news",),
            checked_at=100,
            expires_at=700,
        )
```

- [x] **Step 2: 运行测试并确认 RED**

Run: `.venv/bin/python -m pytest -q tests/social_runtime/knowledge/test_contracts.py tests/social_runtime/knowledge/test_sources.py`

Expected: source/release 契约缺失。

- [x] **Step 3: 实现 URL policy 与 Port 数据边界**

规范 URL 移除 tracking query，保留语义 query 白名单；DNS/重定向前后均由 adapter 执行地址安全检查。领域对象不保存正文、脚本、Prompt、异常原文或厂商密钥；短证据最大 320 字。

- [x] **Step 4: 实现结果完整性语义**

`complete` 要求 registry 中本次策略的 required source 全部成功或得到可验证无更新响应；`partial` 与空 `evidence` 可以共存但不能创建 negative snapshot。错误只暴露固定 diagnostic code。

- [x] **Step 5: 运行测试并提交**

Run: `.venv/bin/python -m pytest -q tests/social_runtime/knowledge/test_contracts.py tests/social_runtime/knowledge/test_sources.py`

Expected: PASS。

Commit: `git commit -m "feat: define official knowledge evidence"`

---

### Task 2: 持久化来源、claim、版本槽位和负向快照

**Files:**
- Modify: `groupmate/social_runtime/knowledge/repository.py`
- Create: `tests/social_runtime/knowledge/test_admission.py`
- Create: `tests/social_runtime/knowledge/test_release_state.py`
- Modify: `tests/social_runtime/knowledge/test_repository.py`

**Interfaces:**
- Produces: `.upsert_source(evidence) -> source_id`。
- Produces: `.admit_claim(candidate, evidence_ids) -> ClaimWriteResult`。
- Produces: `.load_release_state(game_id, region, platform)`、`.save_release_state(state, expected_revision)`。
- Produces: `.save_negative_snapshot(snapshot)`、`.valid_negative_snapshot(key, now)`、`.invalidate_negative_snapshots(game_id, reason)`。

- [x] **Step 1: 写 repository 失败测试**

验证 canonical URL/content hash 去重、来源分类不可升级覆盖、claim/evidence 原子提交、official 新证据 supersede 旧 official、rumor 历史保留、optimistic revision 冲突、negative TTL 与新官方证据即时失效。

- [x] **Step 2: 运行 repository 测试并确认 RED**

Run: `.venv/bin/python -m pytest -q tests/social_runtime/knowledge/test_repository.py tests/social_runtime/knowledge/test_admission.py tests/social_runtime/knowledge/test_release_state.py`

Expected: v4 表存在但相应 repository API 缺失。

- [x] **Step 3: 实现 claim/source 短事务**

同一 canonical source 更新 `fetched_at/content_hash` 但不改变 publisher/source_class；同 subject/predicate/version/region/platform 的 active claim 在同一事务内比较证据等级。新 claim 用 `supersedes_claim_id` 指向旧 claim，旧行改 `superseded`，evidence link 永不重挂。

- [x] **Step 4: 实现 release revision 和 negative 失效**

每次三轨改变递增 `revision`。official evidence 写入与相关 negative snapshot 失效必须在同一写事务；read path 同时检查 `expires_at > now` 与当前 revision。

- [x] **Step 5: 运行测试并提交**

Run: `.venv/bin/python -m pytest -q tests/social_runtime/knowledge/test_repository.py tests/social_runtime/knowledge/test_admission.py tests/social_runtime/knowledge/test_release_state.py`

Expected: PASS。

Commit: `git commit -m "feat: persist game release evidence"`

---

### Task 3: 实现 KnowledgeAdmissionPolicy 与三轨状态机

**Files:**
- Create: `groupmate/social_runtime/knowledge/admission.py`
- Create: `groupmate/social_runtime/knowledge/release_state.py`
- Modify: `groupmate/social_runtime/knowledge/resolver.py`
- Modify: `tests/social_runtime/knowledge/test_admission.py`
- Modify: `tests/social_runtime/knowledge/test_release_state.py`
- Modify: `tests/social_runtime/knowledge/test_resolver.py`

**Interfaces:**
- Produces: `KnowledgeAdmissionPolicy.evaluate(candidate, existing) -> AdmissionDecision`。
- Produces: `GameReleaseStateService.apply_evidence(state, evidence) -> ReleaseTransition`。
- Produces: `.resolve_reference(frame, message_time, region, platform) -> ResolvedVersionReference`。

- [x] **Step 1: 写 evidence ladder 和冲突失败测试**

bundled 只支持 stable semantic；单官方来源可激活其明确支持的 public fact；两个独立可靠 secondary 才能激活非官方稳定事实；单一 unofficial 只能 rumor；同等级相反来源进入 disputed；正式实装优先于测试服数值。

- [x] **Step 2: 写三轨状态迁移失败测试**

覆盖 future→current→past、none→teaser→preview→notice→released、rumor weak/corroborated/conflicted/stale，并验证 official 变化不覆写 rumor。到达 `release_at` 只建立 revalidation job，不靠时钟自动宣称 released。

- [x] **Step 3: 写相对版本解析失败测试**

给定两个槽位和消息时间，验证“这期/下期/刚更新”；没有唯一 region/platform 或 next slot 时返回 ambiguity，绝不通过版本号 +1 构造 label。

- [x] **Step 4: 运行测试并确认 RED**

Run: `.venv/bin/python -m pytest -q tests/social_runtime/knowledge/test_admission.py tests/social_runtime/knowledge/test_release_state.py tests/social_runtime/knowledge/test_resolver.py`

Expected: policy/service 缺失或仍只有词法 version reference。

- [x] **Step 5: 实现纯函数准入与显式状态机**

Policy 输出 `activate/reject/dispute/supersede/keep_pending` 和固定 reason codes，不自行写库。Release service 在单个 `(game, region, platform)` 聚合根内验证时间和轨道，所有 transition 携带 old/new revision 与 evidence IDs。

- [x] **Step 6: 接回 resolver**

resolver 先识别语言相对词，再由 service 绑定已验证 slot；绑定失败保留原始 relative kind 和 ambiguity，DIRECT 的澄清动作留给根据回复阶段。

- [x] **Step 7: 运行测试并提交**

Run: `.venv/bin/python -m pytest -q tests/social_runtime/knowledge/test_admission.py tests/social_runtime/knowledge/test_release_state.py tests/social_runtime/knowledge/test_resolver.py`

Expected: PASS。

Commit: `git commit -m "feat: model game release truth tracks"`

---

### Task 4: 实现官方探测 adapter 的冻结契约

**Files:**
- Create: `groupmate/adapters/astrbot_official_sources.py`
- Create: `tests/contracts/test_official_source_probe.py`
- Modify: `groupmate/adapters/astrbot_bridge.py`

**Interfaces:**
- Produces: `AstrBotOfficialSourceProbe(context, url_policy, timeout_seconds)`。
- Consumes: seed source registry，不使用成员/Persona/关系数据。
- 返回严格 `OfficialProbeResult`，不把网页正文传入 knowledge domain。

- [ ] **Step 1: 用 fake AstrBot context 写失败契约测试**

覆盖成功、多 source partial、timeout、provider unavailable、redirect 到 private IP、页面诱导指令、重复 URL、发布时间缺失、异常消息含 secret。断言最多使用 registry 中 URL、诊断不含 secret、输出证据有界。

- [ ] **Step 2: 运行 adapter 测试并确认 RED**

Run: `.venv/bin/python -m pytest -q tests/contracts/test_official_source_probe.py`

Expected: adapter 不存在。

- [ ] **Step 3: 实现最小 AstrBot adapter**

使用 Bridge 注入的 host capability，不通过插件 HTTP 自调用，不保存 API key。每个来源独立 timeout，最终聚合完整性；页面文本只交给确定性 metadata extractor，无法确定 publisher/time 时保留 evidence pending。

- [ ] **Step 4: 运行最低兼容契约**

Run: `.venv/bin/python -m pytest -q tests/contracts/test_official_source_probe.py tests/shared/test_astrbot_package_loading.py`

Expected: PASS，并且 import 在无 AstrBot 测试环境使用现有 shim/延迟导入方式。

- [ ] **Step 5: 提交**

Commit: `git commit -m "feat: probe official game sources"`

---

### Task 5: 实现每日调度、重试与进程恢复

**Files:**
- Create: `groupmate/social_runtime/knowledge/jobs.py`
- Create: `tests/social_runtime/knowledge/test_jobs.py`
- Create: `tests/recovery/test_knowledge_job_recovery.py`
- Modify: `groupmate/adapters/astrbot_bridge.py`
- Modify: `groupmate/social_runtime/knowledge/repository.py`

**Interfaces:**
- Produces: `KnowledgeJobService.start()`、`.wake()`、`.close()`、`.health()`。
- Job kinds: `seed_import`、`official_daily_probe`、`time_boundary_revalidation`。
- Stable key: `kind × game × region × platform × Asia/Shanghai date-or-boundary`。

- [ ] **Step 1: 写日界与抖动失败测试**

五款 seed 永远 baseline active；每款距最后成功 ≥24h 时最多一个 daily job；使用 Asia/Shanghai 日界和基于 stable key 的 0–20 分钟确定性抖动；重启不生成重复 job。

- [ ] **Step 2: 写失败与恢复测试**

完整成功更新 `official_checked_at` 和 completed；partial/timeout/unavailable 进入 retry，保留旧成功时间；指数退避有上限；启动时 running 任务按 attempt/next_attempt_at 恢复；close 不把执行中失败写成成功。

- [ ] **Step 3: 运行测试并确认 RED**

Run: `.venv/bin/python -m pytest -q tests/social_runtime/knowledge/test_jobs.py tests/recovery/test_knowledge_job_recovery.py`

Expected: job service 缺失。

- [ ] **Step 4: 实现单 worker 持久调度器**

每次 claim job 后离开事务再做 I/O，得到 probe result 后用新短事务 admission；单 writer 序列化知识提交。触达已验证 release_at 时创建 boundary job，probe 成功后才改变 official/release 状态。

- [ ] **Step 5: 接入 Bridge lifecycle/readiness**

knowledge enabled 时先 seed import，再启动 job service；search/probe adapter unavailable 只设置 `knowledge_search_adapter_unavailable`，不阻止 seed 和观察。close 顺序为停止接收→等待有界 worker→关闭 adapter。

- [ ] **Step 6: 运行测试并提交**

Run: `.venv/bin/python -m pytest -q tests/social_runtime/knowledge/test_jobs.py tests/recovery/test_knowledge_job_recovery.py tests/contracts/test_official_source_probe.py`

Expected: PASS。

Commit: `git commit -m "feat: refresh official game versions daily"`

---

### Task 6: SHADOW 版本场景、trace 与 Gate 2

**Files:**
- Create: `tests/scenarios/test_game_release_shadow.py`
- Modify: `groupmate/social_runtime/control/message_traces.py`
- Modify: `tests/contracts/test_message_traces.py`
- Modify: `groupmate/adapters/astrbot_bridge.py`

**Interfaces:**
- evaluation knowledge diagnostic includes probe reason、status、source domains、evidence level、checked/fresh times、release revision。
- 不投影 URL query、网页短证据、查询全文、异常原文或内部评分。

- [ ] **Step 1: 写端到端 SHADOW 失败场景**

用冻结 fixture 覆盖：已有当前版本、下一版本无官方资料、后来出现 preview、用户问 rumor 但日常任务不查 rumor、官方与 rumor 并存、空结果、partial、timeout、release boundary、场景正常但本阶段不生成事实回复。

- [ ] **Step 2: 写 trace 失败测试**

Trace 能区分 `official_complete`、`official_partial`、`negative_snapshot_valid`、`evidence_disputed` 和 `knowledge_stale`；只显示 source domain、等级和时间。

- [ ] **Step 3: 运行并按失败完善编排**

Run: `.venv/bin/python -m pytest -q tests/scenarios/test_game_release_shadow.py tests/contracts/test_message_traces.py`

Expected: 首次因 SHADOW orchestration/trace 缺失而失败；实现后 PASS。

- [ ] **Step 4: 运行 Gate 2**

Run: `.venv/bin/python -m pytest -q tests/social_runtime/knowledge tests/contracts/test_official_source_probe.py tests/recovery/test_knowledge_job_recovery.py tests/scenarios/test_game_release_shadow.py`

Expected: PASS；失败/partial 不推进成功时间，false negative claims 为 0，三轨审计链完整。

- [ ] **Step 5: 回归并提交**

Run: `.venv/bin/python -m pytest -q tests/social_runtime tests/contracts tests/recovery tests/scenarios`

Expected: PASS。

Run: `git diff --check`

Expected: 无输出。

Commit: `git commit -m "feat: ship shadow game release grounding"`

---

## 子项目验收

- 五款游戏的官方轨道以每 24 小时成功核验为目标，失败不伪造成功时间。
- “新版本”可绑定已验证槽位；不能唯一绑定时保持歧义，不猜版本号。
- 官方、发布和 rumor 三轨可并存，证据纠正保留历史。
- 只有完整、覆盖充分的成功 probe 能形成限定性的 negative snapshot。
- 所有公开事实仍停留在 SHADOW，不进入正式回复。

通过后进入：`docs/superpowers/plans/2026-08-28-game-knowledge-grounded-replies.md`。
