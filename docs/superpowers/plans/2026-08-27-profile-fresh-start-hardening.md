# 新库画像成长链路加固 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让全新数据库上的画像事实和关系跨批成熟，自动生成整群画像，并为后台健康、命令过滤和管理页枚举提供可验证行为。

**Architecture:** 保持模型候选与本地确认分离；在现有事实和关系表中使用稳定 ID 做证据合并，不新增历史迁移。后台服务在每批完成后重建成员及整群快照，并通过现有 Bridge/health 控制面发布安全运行指标。

**Tech Stack:** Python 3.10+、dataclasses、asyncio、SQLite、pytest、原生 ES modules。

**Spec:** `docs/superpowers/specs/2026-08-27-profile-fresh-start-hardening-design.md`

## Global Constraints

- 正式部署使用全新数据库；实现代码不得删除或重置数据库文件。
- 群成员不能修改画像，仅保留精确命令 `查看我的画像`。
- 第三方说法和未确认候选不得进入闲聊上下文。
- 不保存模型原始输出或原始异常字符串。
- 不实现未来画像卡片版式。
- 不使用 worktree。

---

### Task 1: 跨批事实与关系证据累计

**Files:**
- Modify: `groupmate/profile_vocabulary.py`
- Modify: `groupmate/social_runtime/profile/extractor.py`
- Modify: `groupmate/social_runtime/profile/policy.py`
- Modify: `groupmate/social_runtime/profile/repository.py`
- Modify: `groupmate/social_runtime/profile/service.py`
- Modify: `groupmate/adapters/deepseek_profile.py`
- Test: `tests/social_runtime/profile/test_profile_repository.py`
- Test: `tests/social_runtime/profile/test_profile_policy.py`
- Test: `tests/scenarios/test_profile_background_pipeline.py`

**Interfaces:**
- Produces: `normalize_profile_claim(text: object) -> str`
- Produces: `ProfileEvidencePolicy.reinforce_fact(old, incoming) -> ProfileFact`
- Produces: `ProfileEvidencePolicy.reinforce_edge(old, incoming) -> SocialEdge`
- Produces: `ProfileRepository.fact(fact_id) -> ProfileFact | None`
- Produces: `ProfileRepository.edge(edge_id) -> SocialEdge | None`
- Produces: `ProfileRepository.upsert_fact(fact) -> ProfileFact`

- [x] **Step 1: Write failing cross-batch tests**

Add tests that process three one-message batches returning the same `observed_pattern` with different evidence IDs and assert one confirmed fact with three evidence IDs. Add the equivalent relation test and a test proving `third_party_claim` never merges with `self_statement`.

- [x] **Step 2: Run the focused tests and confirm red**

Run: `.venv/bin/python -m pytest -q tests/social_runtime/profile/test_profile_policy.py tests/social_runtime/profile/test_profile_repository.py tests/scenarios/test_profile_background_pipeline.py`

Expected: FAIL because fact IDs include evidence and repository writes do not merge evidence.

- [x] **Step 3: Implement stable claims and deterministic reinforcement**

Use NFKC normalization, collapsed whitespace, case-folding and punctuation removal. Exclude evidence IDs from `_candidate_id` but retain `source_kind`. Add repository lookup/upsert methods with scope verification. Reinforcement unions evidence, retains maximum confidence/strength, then reuses local thresholds; confirmed records never downgrade.

Update the model prompt so a direct relationship candidate may be proposed with one evidence event while local policy remains the only confirmation authority.

- [x] **Step 4: Run focused tests**

Run the Step 2 command. Expected: PASS.

### Task 2: 自动刷新整群画像

**Files:**
- Modify: `groupmate/social_runtime/profile/repository.py`
- Modify: `groupmate/social_runtime/profile/group_portrait.py`
- Modify: `groupmate/social_runtime/profile/service.py`
- Test: `tests/social_runtime/profile/test_group_portrait.py`
- Test: `tests/scenarios/test_profile_background_pipeline.py`

**Interfaces:**
- Produces: `ProfileRepository.snapshots_for_group(persona_id, group_id) -> tuple[ProfileSnapshot, ...]`
- Produces: `ProfileRepository.observation_hours(persona_id, group_id, limit=500) -> tuple[int, ...]`
- Produces: `ProfileService._refresh_group_portrait(group_id, generated_at) -> None`

- [x] **Step 1: Write a failing automatic refresh scenario**

Process a batch that creates a member snapshot, then assert the repository contains a group portrait with `member_count == 1`, a non-empty activity rhythm and a privacy-safe summary that omits the member fact text.

- [x] **Step 2: Run and confirm red**

Run: `.venv/bin/python -m pytest -q tests/social_runtime/profile/test_group_portrait.py tests/scenarios/test_profile_background_pipeline.py`

Expected: FAIL because production service never stores a group portrait.

- [x] **Step 3: Implement scoped aggregate inputs and refresh**

Read only same-group snapshots, confirmed edges and the latest 500 observation timestamps. Build with empty `culture` and `topic_counts` until a separately governed group-topic source exists. Add a deterministic member-count fallback summary and store the portrait after every completed batch.

- [x] **Step 4: Run focused tests**

Run the Step 2 command. Expected: PASS.

### Task 3: 画像后台健康与异常自恢复

**Files:**
- Modify: `groupmate/social_runtime/profile/service.py`
- Modify: `groupmate/adapters/astrbot_bridge.py`
- Modify: `groupmate/adapters/web_api.py`
- Modify: `pages/settings/workspaces/profiles.js`
- Test: `tests/scenarios/test_profile_background_pipeline.py`
- Test: `tests/contracts/test_web_api.py`
- Test: `tests/page/test_profile_workspace.py`

**Interfaces:**
- Produces: `ProfileService.health(group_id) -> dict[str, object]`
- Extends: `AstrBotSocialRuntimeBridge.runtime_status(group_id)` with `profile_status`
- Extends: `/health` response with `profile_status`

- [x] **Step 1: Write failing health and recovery tests**

Assert a legal empty result records `profile_no_candidates`, a successful candidate updates `last_success_at`, and one unexpected extractor exception records `profile_worker_failed` without ending a test background loop. Assert the health API and profile workspace expose only safe status fields.

- [x] **Step 2: Run and confirm red**

Run: `.venv/bin/python -m pytest -q tests/scenarios/test_profile_background_pipeline.py tests/contracts/test_web_api.py tests/page/test_profile_workspace.py`

Expected: FAIL because profile health is not exported and unexpected exceptions escape the loop.

- [x] **Step 3: Implement in-memory health and safe recovery**

Track task state, pending count, last attempt, last success and last diagnostic per group. Catch unexpected loop exceptions at the scheduler boundary, record only `profile_worker_failed`, yield control and continue. Preserve existing provider retry timing. Merge the safe mapping into Bridge runtime status and `/health`; render a compact status line on the profile page.

- [x] **Step 4: Run focused tests**

Run the Step 2 command. Expected: PASS.

### Task 4: 过滤命令噪声并修正管理页枚举

**Files:**
- Modify: `groupmate/adapters/astrbot_bridge.py`
- Modify: `pages/settings/workspaces/profiles.js`
- Test: `tests/scenarios/test_profile_background_pipeline.py`
- Test: `tests/page/test_profile_workspace.py`
- Test: `tests/contracts/test_astrbot_events.py`

**Interfaces:**
- Consumes: translated payload fields `social_eligible` and `interaction_owner`
- Preserves: exact member command `查看我的画像`

- [x] **Step 1: Write failing filtering and enum tests**

Assert configured external command and external-link events do not increase pending profile observations, while an ordinary unaddressed group message does. Assert frontend labels cover `stable` and every value in the backend relation vocabulary.

- [x] **Step 2: Run and confirm red**

Run: `.venv/bin/python -m pytest -q tests/contracts/test_astrbot_events.py tests/scenarios/test_profile_background_pipeline.py tests/page/test_profile_workspace.py`

Expected: FAIL because profile observation ignores ownership and the frontend uses obsolete enum names.

- [x] **Step 3: Implement the minimal boundary changes**

Skip profile observation when `social_eligible is False`, owner is `EXTERNAL_PLUGIN`, or the text is the view command. Replace frontend mappings with exact backend vocabulary and readable Chinese labels.

- [x] **Step 4: Run focused tests**

Run the Step 2 command. Expected: PASS.

### Task 5: 新库验收与清库部署准备

**Files:**
- Modify: `README.md`
- Modify: `docs/operations/social-runtime-control-plane.md`
- Test: `tests/scenarios/test_member_profile_acceptance.py`

**Interfaces:**
- Documents: stop, `.backup`, move `.db/.db-wal/.db-shm`, install, smoke-test, rollback

- [x] **Step 1: Add a clean-database acceptance scenario**

Create a new database, process multiple batches, and assert confirmed fact, confirmed edge, member snapshot, group portrait, profile health and the single view command all work together.

- [x] **Step 2: Run all relevant tests**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/social_runtime/profile \
  tests/contracts/test_deepseek_profile.py \
  tests/contracts/test_profile_query.py \
  tests/contracts/test_profile_web_api.py \
  tests/contracts/test_web_api.py \
  tests/scenarios/test_profile_background_pipeline.py \
  tests/scenarios/test_profile_query_flow.py \
  tests/scenarios/test_member_profile_acceptance.py \
  tests/page/test_profile_workspace.py \
  tests/shared/test_group_scope_privacy.py
```

Expected: PASS.

- [x] **Step 3: Document the destructive deployment boundary**

Document that code never deletes the database. Operators must stop AstrBot, create an SQLite `.backup`, preserve config, move all three SQLite files, start the new version, verify health and keep the backup for rollback.

- [x] **Step 4: Final verification**

Run: `.venv/bin/python -m compileall -q groupmate && git diff --check`

Expected: exit 0 with only intended files plus the existing user-owned untracked analysis files.
