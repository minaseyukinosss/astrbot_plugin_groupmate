# 群友说话风格蒸馏与临时模仿 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有群画像后台自动形成可复用的群友说话风格资产，并允许配置管理员通过真实 @ 在单个群内让爱弥斯临时模仿该风格。

**Architecture:** 复用 `profile_observations` 中已经持久化的群消息作为唯一证据源，新增无 `persona_id` 业务维度的成员风格设置、版本和群级会话仓储。风格只在 `StyleDirector` 已经完成后作为 `ReplyPlan` 的表达覆盖进入 `ReplyExecutor`；群聊状态指令由 Bridge 在普通闲聊前确定性处理，成功确认使用同一风格覆盖做第一次受控试演。

**Tech Stack:** Python 3.11+、asyncio、SQLite/WAL、AstrBot/OneBot 事件段、DeepSeek JSON API、原生 ES Modules、pytest

**Spec:** `docs/superpowers/specs/2026-08-27-member-speech-style-imitation-design.md`

## Global Constraints

- 不使用 git worktree；在当前工作目录实施，并保留所有无关未提交改动。
- 生产代码严格遵循测试先行：先看到目标测试因缺少行为而失败，再写最小实现。
- 风格资产的业务键只能是 `group_id × member_id`；`persona_id` 不能造成重复蒸馏。
- 每成员蒸馏开关默认关闭；只有后台管理员可以改变开关。
- 群聊激活、替换和停止必须包含真实平台 `@爱弥斯` 段，并且仅 `control_admin_ids` 有管理权限。
- 被模仿者只能结束当前群中对自己的当前会话，不能控制蒸馏开关或其他成员。
- 单群最多一个有效会话，必须明确未来结束时间，单次最长 3 天。
- 模仿只改变普通闲聊措辞；Persona、立场、能力、关系、权限、安全、命令结果和精确复读不变。
- 成熟门槛固定为至少 40 条合格消息、5 个活跃日、3 类场景；短反应不能独立形成风格。
- 启动采用“确认即第一次试演”，不增加连接、频道、上线、浓度或百分比状态提示。
- 风格资产不得包含目标成员的身份、经历、观点、隐私、攻击对象或可直接拼接的长原句。

---

## 文件结构

### 新建

- `groupmate/social_runtime/profile/speech_style.py`：风格设置、成熟度、版本和覆盖契约，以及本地证据策略。
- `groupmate/social_runtime/profile/style_repository.py`：风格设置、证据查询、版本和模仿会话的 SQLite 仓储。
- `groupmate/social_runtime/profile/style_service.py`：低频成熟度检查、蒸馏发布和会话读取编排。
- `groupmate/adapters/deepseek_member_style.py`：结构化风格蒸馏模型边界。
- `groupmate/adapters/imitation_commands.py`：真实 mention、管理员权限、目标和中文时间解析，以及状态请求结果。
- `groupmate/social_runtime/actions/member_style.py`：将风格版本转换为受限生成覆盖，并检查身份和必要事实。
- 对应测试文件放入 `tests/social_runtime/profile/`、`tests/contracts/` 和 `tests/scenarios/`。

### 修改

- `groupmate/social_runtime/persistence/schema.py`：v3 表和 v2→v3 原位迁移。
- `groupmate/social_runtime/profile/service.py`：现有画像批处理成功后唤醒成员风格服务，不增加每消息模型调用。
- `groupmate/adapters/participants.py`：本群成员 ID、唯一昵称和 member_ref 的受限解析。
- `groupmate/social_runtime/replying.py`：在持久回复计划中携带可选覆盖，并在生成与修复时应用。
- `groupmate/adapters/astrbot_bridge.py`：组合风格服务、群聊状态请求、会话自我上下文和普通回复覆盖。
- `main.py`：在普通画像查询和闲聊之前处理模仿状态请求。
- `groupmate/social_runtime/control/commands.py`、`groupmate/adapters/web_api.py`：管理员开关命令与详情查询。
- `groupmate/social_runtime/control/queries.py`：画像详情附带安全的风格状态摘要。
- `pages/settings/workspaces/profiles.js`、`pages/settings/styles/components.css`：成员风格后台区域和默认关闭开关。
- `README.md`、`docs/operations/social-runtime-control-plane.md`：操作和隐私边界。

---

### Task 1: 领域契约、v3 Schema 与仓储

**Files:**
- Create: `groupmate/social_runtime/profile/speech_style.py`
- Create: `groupmate/social_runtime/profile/style_repository.py`
- Modify: `groupmate/social_runtime/profile/__init__.py`
- Modify: `groupmate/social_runtime/persistence/schema.py`
- Test: `tests/social_runtime/profile/test_member_speech_style.py`
- Test: `tests/social_runtime/profile/test_style_repository.py`
- Test: `tests/social_runtime/test_schema.py`

**Interfaces:**
- Produces: `MemberStyleSetting`, `MemberStyleMaturity`, `MemberSpeechStyle`, `ImitationSession`, `MemberStyleEvidencePolicy`.
- Produces: `MemberStyleRepository.set_enabled(...)`, `.setting(...)`, `.eligible_observations(...)`, `.publish(...)`, `.latest_ready(...)`, `.start_session(...)`, `.stop_session(...)`, `.active_session(...)`.
- Consumes: `ProfileObservation` and the existing SQLite connection helpers.

- [x] **Step 1: Write failing contract and policy tests**

Cover literal behavior: default-disabled setting, qualitative field bounds, invalid evidence IDs, commands/forwards/chorus/sensitive text rejection, short reaction classification, and maturity requiring 40 messages across 5 days and 3 scenes.

```python
def test_maturity_requires_volume_days_and_scene_diversity():
    maturity = MemberStyleMaturity.from_evidence(
        tuple(_evidence(index, day=index % 5, scene=("answer", "banter", "care")[index % 3])
              for index in range(40))
    )
    assert maturity.ready is True
    assert maturity.eligible_message_count == 40
    assert maturity.active_day_count == 5
    assert maturity.scene_types == ("answer", "banter", "care")
```

- [x] **Step 2: Run focused tests and verify RED**

Run: `pytest -q tests/social_runtime/profile/test_member_speech_style.py`

Expected: collection fails because `speech_style` does not exist.

- [x] **Step 3: Implement immutable contracts and local evidence policy**

Use explicit validation and bounded tuples. `MemberSpeechStyle` carries only qualitative generation fields and evidence IDs; it has no Persona field and no raw-example field.

```python
@dataclass(frozen=True)
class MemberSpeechStyle:
    group_id: str
    member_id: str
    version: int
    status: str
    opening_patterns: tuple[str, ...]
    progression_patterns: tuple[str, ...]
    closing_patterns: tuple[str, ...]
    length_rhythm: str
    directness: str
    disagreement_style: str
    play_style: str
    care_style: str
    addressing_style: str
    particles_punctuation: str
    stable_traits: tuple[str, ...]
    occasional_traits: tuple[str, ...]
    evidence_event_ids: tuple[str, ...]
    generated_at: int
```

- [x] **Step 4: Add failing schema and repository tests**

Tests must verify clean v3 bootstrap, owned v2 migration, settings default off, enable timestamps, version increments, disabled assets cannot resolve, one active session per group, atomic replacement, target-only stop, and read-time expiry.

```python
def test_active_session_expires_on_read(tmp_path):
    repository = MemberStyleRepository(tmp_path / "groupmate-social-runtime-v2.db")
    _publish_ready_style(repository, group_id="g1", member_id="u1")
    repository.start_session(
        group_id="g1", target_member_id="u1", target_display_name="A",
        style_version=1, started_by="admin", started_at=100, expires_at=200,
    )
    assert repository.active_session("g1", now=199) is not None
    assert repository.active_session("g1", now=200) is None
```

- [x] **Step 5: Run repository tests and verify RED**

Run: `pytest -q tests/social_runtime/profile/test_style_repository.py tests/social_runtime/test_schema.py`

Expected: missing repository and schema v3 assertions fail.

- [x] **Step 6: Implement v3 migration and repository**

Add required tables `member_style_settings`, `member_speech_style_versions`, `imitation_sessions`; migrate an owned v2 database inside `BEGIN IMMEDIATE`. Session replacement first closes the effective row with `stop_reason='replaced'`, then inserts the new row in the same transaction. `active_session` always adds `expires_at > now` and verifies that the target setting remains enabled and its selected style remains `READY`.

- [x] **Step 7: Run focused tests and commit**

Run: `pytest -q tests/social_runtime/profile/test_member_speech_style.py tests/social_runtime/profile/test_style_repository.py tests/social_runtime/test_schema.py`

Expected: PASS.

Commit only Task 1 files with `git commit -m "feat: persist member speech styles"`.

---

### Task 2: DeepSeek 蒸馏边界与低频风格服务

**Files:**
- Create: `groupmate/adapters/deepseek_member_style.py`
- Create: `groupmate/social_runtime/profile/style_service.py`
- Modify: `groupmate/social_runtime/profile/service.py`
- Modify: `groupmate/adapters/deepseek_profile.py`
- Test: `tests/contracts/test_deepseek_member_style.py`
- Test: `tests/social_runtime/profile/test_member_style_service.py`
- Test: `tests/scenarios/test_profile_background_pipeline.py`

**Interfaces:**
- Consumes: `MemberStyleRepository.eligible_observations()` and Task 1 contracts.
- Produces: `DeepSeekMemberStyleClient.distill(batch) -> MemberStyleModelResponse`.
- Produces: `MemberStyleService.process_due(now=...)`, `.member_status(...)` and `.wake()`.
- `ProfileService` receives optional `style_service` and calls `wake()` only after durable observations change.

- [x] **Step 1: Write failing model-contract tests**

Verify request uses JSON mode and a dedicated qualitative prompt; the response must reject unknown fields, missing sections, invented event IDs, raw quotations, identity/opinion/experience fields, and unbounded strings.

```python
def test_distiller_request_asks_for_qualitative_structure_not_word_percentages():
    client = DeepSeekMemberStyleClient(api_key="k", api_base="https://example", model="m", transport=_Transport())
    payload = client.request_payload({"events": []})
    system = payload["messages"][0]["content"]
    assert "起句" in system and "推进" in system and "收尾" in system
    assert "词频" in system and "不得" in system
```

- [x] **Step 2: Run model tests and verify RED**

Run: `pytest -q tests/contracts/test_deepseek_member_style.py`

Expected: missing adapter failure.

- [x] **Step 3: Implement the dedicated client and validated parser**

Reuse the existing `AioHttpJsonTransport`, error normalization and timeout accounting. The request includes only event ID, occurred_at, local scene type and bounded text. Publishable output must list evidence IDs for every stable trait and all IDs must be in the submitted set.

- [x] **Step 4: Write failing service tests**

Tests cover no call below maturity, one call at maturity, no per-message call, disabled target skipped, prior ready version kept on provider failure, incremental evidence creates version 2, and safe diagnostics do not contain provider exception text.

- [x] **Step 5: Run service tests and verify RED**

Run: `pytest -q tests/social_runtime/profile/test_member_style_service.py tests/scenarios/test_profile_background_pipeline.py`

Expected: missing service/integration behavior fails.

- [x] **Step 6: Implement low-frequency scheduling**

`MemberStyleService` scans only enabled members with enough unprocessed evidence. It is woken by observation changes but debounces work through the existing profile interval; it never invokes the provider from `observe()`. On success it publishes a new immutable version; on failure it records a bounded failure code and leaves the prior ready version selectable.

- [x] **Step 7: Run focused tests and commit**

Run: `pytest -q tests/contracts/test_deepseek_member_style.py tests/social_runtime/profile/test_member_style_service.py tests/scenarios/test_profile_background_pipeline.py`

Expected: PASS.

Commit Task 2 files with `git commit -m "feat: distill member speech styles"`.

---

### Task 3: 管理员后台开关与状态查询

**Files:**
- Modify: `groupmate/social_runtime/control/commands.py`
- Modify: `groupmate/adapters/web_api.py`
- Modify: `groupmate/social_runtime/control/queries.py`
- Modify: `main.py`
- Test: `tests/contracts/test_commands.py`
- Test: `tests/contracts/test_profile_web_api.py`

**Interfaces:**
- Produces: `SetMemberStyleDistillation(member_ref: str, enabled: bool)` control command.
- Existing `profile` query adds `speech_style` with no raw member ID or evidence text.
- Command execution resolves opaque `member_ref`, writes through `MemberStyleRepository`, records `profile_audit`, and returns the current style setting version.

- [x] **Step 1: Write failing command and query tests**

Verify admin-only group scope, opaque member ref resolution, default disabled response, enable/disable audit, disabling closes an active session, and query output exposes only status/counts/dates/readable summary.

```python
def test_admin_can_enable_distillation_without_exposing_actor_id(tmp_path):
    result = service.execute(
        SetMemberStyleDistillation("member:abc", True),
        _context(admin_id="admin", expected_version=0),
    )
    assert result.data["enabled"] is True
    assert "actor_id" not in json.dumps(result.data)
```

- [x] **Step 2: Run tests and verify RED**

Run: `pytest -q tests/contracts/test_commands.py tests/contracts/test_profile_web_api.py`

Expected: unsupported command and absent query field failures.

- [x] **Step 3: Implement command, API parsing and safe query projection**

The command is not available to ordinary chat users. Reuse current `CommandContext` authorization, expected-version/idempotency and audit mechanics. Style summaries are constructed from the structured version, not from raw evidence.

- [x] **Step 4: Run tests and commit**

Run: `pytest -q tests/contracts/test_commands.py tests/contracts/test_profile_web_api.py`

Expected: PASS.

Commit Task 3 files with `git commit -m "feat: govern member style distillation"`.

---

### Task 4: 群聊模仿状态请求

**Files:**
- Create: `groupmate/adapters/imitation_commands.py`
- Modify: `groupmate/adapters/participants.py`
- Modify: `groupmate/adapters/astrbot_bridge.py`
- Test: `tests/contracts/test_imitation_commands.py`
- Test: `tests/scenarios/test_imitation_command_flow.py`

**Interfaces:**
- Produces: `ImitationRequest(kind, target_member_id, target_display_name, expires_at)`.
- Produces: `ImitationCommandResult(handled, transition, error_text, diagnostic_code)`; a successful `transition` contains immutable target, expiry and Persona-name facts but no rendered reply.
- Produces: `AstrBotSocialRuntimeBridge.prepare_imitation_transition(event)`; returns `None` for ordinary messages and a structured result for recognized state requests.
- Consumes: real `mentions_bot`, ordered mention segments, `control_admin_ids`, `ParticipantDirectory`, `MemberStyleRepository`.

- [x] **Step 1: Write failing deterministic parser tests**

Cover: actual @ bot required; text name rejected; configured admin start/replace/stop; target member self-stop; non-target denied; same-message target @ preferred; unique confirmed name fallback; duplicate name asks for @; relative hours and tonight/tomorrow clock parsing; missing/past/ambiguous/>3-day time rejected.

```python
def test_textual_bot_name_cannot_change_imitation_state():
    event = _event(text="爱弥斯开始模仿 A 到明晚八点", mentions=(), mentions_bot=False)
    assert interpreter.interpret(event, now=_timestamp("2026-08-27 14:00")) is None
```

- [x] **Step 2: Run parser tests and verify RED**

Run: `pytest -q tests/contracts/test_imitation_commands.py`

Expected: missing adapter failure.

- [x] **Step 3: Implement deterministic recognition and validation**

The interpreter recognizes only explicit start/stop semantics. Mention target uses platform ID; nickname lookup uses current-group exact display name/confirmed alias and succeeds only with one actor. Time parser supports explicit duration and unambiguous local dates without a model call. It returns candidates only; the service repeats membership, permission, readiness and time validation before writing.

- [x] **Step 4: Write failing bridge flow tests**

Verify state request calls no scene model, creates exactly one group session, returns a normal Aemeath error on failure, emits immutable success facts without generated text, and does not affect another group.

- [x] **Step 5: Run bridge tests and verify RED**

Run: `pytest -q tests/scenarios/test_imitation_command_flow.py`

Expected: bridge method absent.

- [x] **Step 6: Implement Bridge transition integration**

`prepare_imitation_transition()` performs translation, recognition and the repository transaction without entering cognition. Error and stop results carry a final current-Persona text; start/replace results carry only the committed session plus the three immutable confirmation facts. The public AstrBot handler is not registered until Task 5 can render a complete confirmation.

- [x] **Step 7: Run focused tests and commit**

Run: `pytest -q tests/contracts/test_imitation_commands.py tests/scenarios/test_imitation_command_flow.py`

Expected: PASS.

Commit Task 4 files with `git commit -m "feat: control group imitation sessions"`.

---

### Task 5: 闲聊表达覆盖、确认试演与身份检查

**Files:**
- Create: `groupmate/social_runtime/actions/member_style.py`
- Modify: `groupmate/social_runtime/actions/__init__.py`
- Modify: `groupmate/social_runtime/replying.py`
- Modify: `groupmate/adapters/astrbot_bridge.py`
- Modify: `groupmate/adapters/imitation_commands.py`
- Modify: `main.py`
- Test: `tests/social_runtime/actions/test_member_style_overlay.py`
- Test: `tests/social_runtime/actions/test_replying.py`
- Test: `tests/scenarios/test_imitation_command_flow.py`
- Test: `tests/scenarios/test_chat_mainline.py`

**Interfaces:**
- Produces: `MemberStyleOverlay(target_member_id, target_display_name, style_version, expires_at, directives)`.
- Produces: `MemberStyleOverlayBuilder.build(style, session) -> MemberStyleOverlay`.
- Produces: `IdentityImitationGuard.review(text, overlay, required_facts) -> tuple[str, ...]`.
- `ReplyPlan` gains `member_style_overlay: MemberStyleOverlay | None = None` with backward-compatible decode.
- `ReplyExecutor` includes overlay only in generated smalltalk paths; exact chorus bypasses it.

- [x] **Step 1: Write failing overlay and guard tests**

Verify structured asset becomes qualitative directives without raw event IDs; guard rejects target identity claims, target experience/opinion claims and omitted target/expiry/Aemeath facts in activation confirmation; ordinary Aemeath identity remains accepted.

```python
def test_guard_rejects_claiming_target_identity():
    violations = IdentityImitationGuard().review(
        "我就是 A，本人来了。", overlay=_overlay("A"), required_facts=()
    )
    assert "imitation_target_identity_claim" in violations
```

- [x] **Step 2: Run tests and verify RED**

Run: `pytest -q tests/social_runtime/actions/test_member_style_overlay.py`

Expected: missing action module failure.

- [x] **Step 3: Implement overlay builder and guard**

The builder caps field count and characters, strips event IDs, and emits explicit “只改变句式，不改变身份、事实、立场”的 rules. Guard uses normalized target names plus first-person identity/experience patterns and required fact coverage; it does not treat every occurrence of the target name as unsafe.

- [x] **Step 4: Write failing ReplyExecutor tests**

Tests verify overlay appears after base Persona/stance/move in the model prompt, exact chorus excludes it, command-like deterministic outputs do not use `ReplyExecutor`, invalid imitation output repairs once then falls back to no-overlay generation, and old stored plans decode with `None`.

- [x] **Step 5: Run replying tests and verify RED**

Run: `pytest -q tests/social_runtime/actions/test_replying.py`

Expected: ReplyPlan and prompt assertions fail.

- [x] **Step 6: Integrate overlay and safe fallback**

Bridge queries `active_session(group_id, now)` immediately before planning and resolves its fixed style version. It adds an overlay only for `realization_mode == GENERATED`. Executor combines social reviewer, output firewall and identity guard; after one failed repair it retries once without overlay, preserving the same `SocialMovePlan` and facts.

- [x] **Step 7: Render activation as confirmation-as-audition and register the handler**

Activation generation receives immutable facts for target display name, local expiry and current Persona name. The selected member style shapes the wording, but the guard requires all three facts. Model failure returns a concise Aemeath fallback such as `好，我学 A 说话到明晚八点。只是说话方式变了，我还是爱弥斯。` without connection/status imagery. Register `prepare_imitation_command()` before profile/affection query and `handle_event()` in `main.py`; a handled request stops propagation and yields exactly one result.

- [x] **Step 8: Run focused tests and commit**

Run: `pytest -q tests/social_runtime/actions/test_member_style_overlay.py tests/social_runtime/actions/test_replying.py tests/scenarios/test_imitation_command_flow.py tests/scenarios/test_chat_mainline.py`

Expected: PASS.

Commit Task 5 files with `git commit -m "feat: apply temporary member style overlay"`.

---

### Task 6: 后台成员风格界面

**Files:**
- Modify: `pages/settings/workspaces/profiles.js`
- Modify: `pages/settings/styles/components.css`
- Modify: `tests/page/test_profile_workspace.py`

**Interfaces:**
- Consumes: Task 3 `profile.summary.speech_style` and existing `submitCommand`.
- Sends: `member_style_distillation_set` with opaque `member_ref`, boolean `enabled`, current style setting version and a human reason.

- [x] **Step 1: Write failing page contract tests**

Test user-visible behaviors: default-off switch, status labels `关闭/积累中/可用/分析失败`, eligible count/day/scene/version display, readable summary, no editable style text area, and no distillation operation outside governed admin command.

- [x] **Step 2: Run page tests and verify RED**

Run: `pytest -q tests/page/test_profile_workspace.py`

Expected: missing member-style section assertions fail.

- [x] **Step 3: Implement the accessible admin section**

Add a compact section after the identity/portrait content. Use a real button or checkbox with an associated label, disabled in flight, and refresh after accepted command. The summary renders qualitative bullets; it never renders event IDs, raw messages, target platform ID or model error details.

- [x] **Step 4: Run tests and commit**

Run: `pytest -q tests/page/test_profile_workspace.py tests/contracts/test_profile_web_api.py`

Expected: PASS.

Commit Task 6 files with `git commit -m "feat: manage member style distillation"`.

---

### Task 7: 组合根、生命周期与回归场景

**Files:**
- Modify: `groupmate/adapters/astrbot_bridge.py`
- Modify: `groupmate/social_runtime/profile/service.py`
- Modify: `main.py`
- Modify: `tests/shared/test_plugin_skeleton.py`
- Modify: `tests/scenarios/test_profile_background_pipeline.py`
- Modify: `tests/scenarios/test_chat_mainline.py`
- Modify: `tests/scenarios/test_imitation_command_flow.py`

**Interfaces:**
- `AstrBotSocialRuntimeBridge.start()` creates one shared `MemberStyleRepository`, one `DeepSeekMemberStyleClient`, one `MemberStyleService` and wires them to profile/command/reply paths.
- `close()` cancels the style task and closes its client without leaking tasks.
- `runtime_status()` adds bounded style worker/session health for the admin page.

- [x] **Step 1: Write failing lifecycle and end-to-end tests**

Cover start/close, profile disabled behavior, style client failure isolation, restart persistence, expired-session recovery, command/external plugin bypass, exact chorus bypass, current-group-only overlay, self-awareness question, and active imitation not increasing proactive participation.

- [x] **Step 2: Run integration tests and verify RED**

Run: `pytest -q tests/shared/test_plugin_skeleton.py tests/scenarios/test_profile_background_pipeline.py tests/scenarios/test_chat_mainline.py tests/scenarios/test_imitation_command_flow.py`

Expected: at least lifecycle and end-to-end behavior failures.

- [x] **Step 3: Complete composition and bounded diagnostics**

Use the same database path and clock in every component. Style-service failure never prevents Social Runtime startup when an older ready asset exists; unavailable style processing is reflected as a bounded admin diagnostic. Session self-awareness is passed as authoritative runtime context, not inferred from recent chat text.

- [x] **Step 4: Run integration tests and commit**

Run: `pytest -q tests/shared/test_plugin_skeleton.py tests/scenarios/test_profile_background_pipeline.py tests/scenarios/test_chat_mainline.py tests/scenarios/test_imitation_command_flow.py`

Expected: PASS.

Commit Task 7 files with `git commit -m "feat: wire member style imitation runtime"`.

---

### Task 8: 操作文档与全量验证

**Files:**
- Modify: `README.md`
- Modify: `docs/operations/social-runtime-control-plane.md`
- Modify: `docs/superpowers/plans/2026-08-27-member-speech-style-imitation.md`

**Interfaces:**
- Documents exact authorization, default-off behavior, maturity thresholds, current-group scope, max duration, target opt-out, failure fallback and database v3 migration.

- [x] **Step 1: Update operator documentation**

Document that enabling distillation begins with future messages, disabling immediately makes the asset unselectable and ends its active session, no normal-user asset editing exists, activation requires actual @, and old v2 databases migrate in place.

- [x] **Step 2: Run format and focused verification**

Run:

```bash
git diff --check
pytest -q tests/social_runtime/profile tests/social_runtime/actions/test_member_style_overlay.py tests/social_runtime/actions/test_replying.py tests/contracts/test_deepseek_member_style.py tests/contracts/test_imitation_commands.py tests/contracts/test_profile_web_api.py tests/page/test_profile_workspace.py tests/scenarios/test_profile_background_pipeline.py tests/scenarios/test_imitation_command_flow.py tests/scenarios/test_chat_mainline.py
```

Expected: no whitespace errors; all selected tests pass.

- [x] **Step 3: Run full regression suite**

Run: `pytest -q`

Expected: all feature-related tests pass. Any pre-existing environment/socket or stale-contract failures must be listed separately with exact test names and compared against the baseline; do not call them feature successes.

- [x] **Step 4: Inspect final scope**

Run: `git status --short` and `git diff --stat`.

Verify user-owned `analysis/shadow_20260824.sql` and `analysis/target_bot_20260824/` remain untouched, and no unrelated dirty file is staged.

- [x] **Step 5: Commit documentation and final integration**

Commit only feature documentation and any final feature-owned integration files with `git commit -m "docs: operate member style imitation"`.

---

## 自检结果

- 规格覆盖：持久开关、证据过滤、成熟度、独立模型调用、版本资产、管理员权限、真实 mention、目标解析、三天上限、单群会话、确认试演、自我认知、身份边界、例外通道、目标退出、后台展示、降级与诊断均映射到明确任务。
- 占位符扫描：计划中没有 `TBD`、`TODO`、未定义的“稍后实现”步骤或“同上”引用。
- 类型一致性：业务键统一为 `group_id/member_id`；会话统一引用 `style_version`；覆盖统一由 `MemberStyleOverlay` 进入 `ReplyPlan`；后台使用 `member_ref`，群聊运行时使用平台 `member_id`。
