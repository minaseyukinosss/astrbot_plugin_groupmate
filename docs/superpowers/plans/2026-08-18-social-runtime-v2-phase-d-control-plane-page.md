# Social Runtime v2 Phase D：控制面与插件页面实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付不会侵入 Actor 写线路的 CQRS 管理面、版本化行为配置、隐私裁剪 SSE 和 ChatGPT 式五工作区 AstrBot 插件页面。

**Architecture:** Projection Consumer 以独立 Cursor 从 Journal 构建 Read Model；Query 只读 Projection，Command 经服务端鉴权、作用域、Expected Version、确认和原因校验后产生领域事件。前端是无构建 ES Modules + Hash Router，不计算权威领域结论。

**Tech Stack:** Python/AstrBot Web API、SSE、原生 JavaScript ES Modules、CSS、pytest、受限 iframe。

**Spec:** `docs/superpowers/specs/2026-08-18-groupmate-social-runtime-v2-design.md`

## Global Constraints

- 页面/Projection/SSE 故障不得阻塞 Event Fabric、Task 或 Outbox。
- Secret、原始高权限 Event、CoT、提示词、内部 ID 不进入 Projection。
- 所有修改必须走 Command；JavaScript 不直接写 DB、不实现权限或 Governor。
- 高影响 Command 必须有 Expected Version、确认和非空原因；冲突返回 HTTP 409。
- 页面支持明暗主题、Reduced Motion、200% 缩放、键盘操作和窄 iframe。

---

### Task 1: Projection Bus 与 Read Models

**Files:** Create `control/projections.py`, `control/queries.py`; create `tests/contracts/test_projection_consumer.py`, `tests/recovery/test_projection_rebuild.py`.

**Interfaces:** `ProjectionConsumer.consume(limit)`, `rebuild(name)`, `ProjectionQueries.bootstrap/runtime/activity/scenes/people/culture/tasks/persona/governance/evaluation/health`.

- [x] 写测试：每个 Projection 独立 Cursor；重复 Journal effect 幂等；一个 Consumer 崩溃不影响 Actor；删除 Projection 后可完整重建。
- [x] 实现 read tables 与 `projection_version`；查询返回显式 `as_of/cursor/stale`，不拼装领域写模型。
- [x] 隐私裁剪：成员页面可显示管理员允许的事实摘要和 evidence reference，不能返回原始敏感 payload。
- [x] Run: `pytest tests/contracts/test_projection_consumer.py tests/recovery/test_projection_rebuild.py -q`; commit `feat: build independent social projections`。

---

### Task 2: Command Service 与配置版本

**Files:** Create `control/commands.py`, `control/config_versions.py`; create `tests/contracts/test_commands.py`, `test_config_versions.py`.

**Interfaces:** `CommandContext(admin_id, persona_id, group_id, expected_version, reason, confirmed)`; `CommandService.execute(command, context)`; config lifecycle `DRAFT→VALIDATED→PUBLISHED→SUPERSEDED`.

- [x] 写测试：非管理员 403、跨群 ID 404、缺原因 400、版本冲突 409、重复 command ID 幂等；发布失败保留 Draft。
- [x] 实现 pause/reset/config draft/validate/dry-run/publish/restore/review/forget/correct/link/cancel/approve calibration 命令。
- [x] Dry-run 使用固定历史事件和 Worker outputs，返回语义 diff，不修改正式 Config；新周期才读取新版本。
- [x] Run contracts tests; commit `feat: add versioned social runtime commands`。

```python
with pytest.raises(ExpectedVersionConflict):
    service.execute(PublishConfig("draft-1"), context(expected_version=2))
assert repository.published_version() == 3
```

---

### Task 3: Web API 与 SSE

**Files:** Create `groupmate/adapters/web_api.py`, `control/stream.py`; modify `main.py`; create `tests/contracts/test_web_api.py`, `test_sse.py`.

**Interfaces:** `/bootstrap`, `/runtime`, `/activity`, `/scenes`, `/people`, `/culture`, `/tasks`, `/persona`, `/governance`, `/evaluation`, `/health`, `/commands`, `/events`.

- [x] 写 API 测试：Query 不触发领域写；Command 传服务端 admin identity；409 保留；SSE 从 `Last-Event-ID` 续传，过期 Cursor 返回 `snapshot_required`。
- [x] SSE event 固定为 `cursor/kind/scope/entity/projection_version/summary`，禁止 payload 中出现 `prompt`, `chain_of_thought`, `secret`, `auth_code`。
- [x] SSE 失败不取消 Projection；客户端可降级到 15 秒有界轮询，health 返回真实降级原因。
- [x] Run contracts tests; commit `feat: expose governed control plane api`。

---

### Task 4: 重建前端壳、Bridge、Router 与 Store

**Files:** Create/replace `pages/settings/index.html`, `app.js`, `bridge.js`, `router.js`, `store.js`, `i18n.js`, `styles/tokens.css`, `styles/layout.css`, `styles/components.css`; create `tests/page/test_shell_assets.py`, `test_router_contract.py`.

**Interfaces:** Hash routes `#/runtime`, `#/persona`, `#/people`, `#/activity`, `#/governance`; `ApiBridge.query/command/connect`; normalized Projection Store.

- [ ] 写静态契约测试：无 inline script、所有模块相对路径、五 routes、skip link、ARIA live connection state、主题 tokens、`prefers-reduced-motion`。
- [ ] 实现紧凑左栏、顶部群/人格/版本/连接/暂停、主工作区、按需右 Inspector；植物绿只用于 primary/selected/healthy。
- [ ] Store 只合并带更高 projection_version 的实体；Command 后等待 Projection event，不乐观伪造领域成功。
- [ ] 400/403/409/500、SSE 断开和 polling 状态显示真实影响。
- [ ] Run: `pytest tests/page/test_shell_assets.py tests/page/test_router_contract.py -q`; commit `feat: rebuild groupmate plugin shell`。

---

### Task 5: 五个工作区

**Files:** Create `pages/settings/workspaces/runtime.js`, `persona.js`, `people.js`, `activity.js`, `governance.js`; create `pages/settings/components/*.js`; create `tests/page/test_workspaces.py`.

**Interfaces:** 每个 workspace 只接收 Store selector 和 command callback；Inspector 使用 entity ref Query。

- [ ] Runtime：状态叙述、实时活动、任务、健康、暂停/恢复；Persona：Constitution、状态/模式、注意力、自主性、Governor、风格、媒体、工具、Draft/Diff/Dry-run。
- [ ] People：身份、关系维度、印象、经历、事实、open loops、承诺、文化、历史；Activity：因果时间线、观察、意图、Governor、Plan、Task、Delivery。
- [ ] Governance：待复核、纠正、遗忘、身份关联、配置版本、校准、导出、保留和 Evaluation；任何 destructive action 二次确认并要求原因。
- [ ] Inspector 只展示证据、结构化 Observation、候选/效用贡献、硬约束、Plan/版本/结果，不展示思维链。
- [ ] Run: `pytest tests/page/test_workspaces.py -q`; commit `feat: add five social runtime workspaces`。

---

### Task 6: iframe、可访问性、安全与 Gate D

**Files:** Create `tests/page/test_iframe_workflows.py`, `test_accessibility_contract.py`, `test_frontend_security.py`; create `docs/operations/social-runtime-control-plane.md`.

**Interfaces:** 完整管理工作流 pause→inspect→draft→dry-run→publish→correct→rollback。

- [ ] 用本地 AstrBot API fake 验证五 route、SSE 重连、409 conflict refresh、窄屏、200% zoom、键盘焦点、dark/light、reduced motion。
- [ ] 插入 `<img onerror>`、脚本字符串、路径逃逸文件名，断言只以 textContent 呈现且上传拒绝。
- [ ] Projection 服务停止时验证群聊 Actor/Task/Delivery 继续，页面显示 stale 与 polling fallback。
- [ ] Run: `pytest tests/contracts tests/page tests/recovery -q && python -m tests.architecture_guard && git diff --check`。
- [ ] Commit `test: verify social runtime control plane`，请求评审，通过后进入 Phase E。
