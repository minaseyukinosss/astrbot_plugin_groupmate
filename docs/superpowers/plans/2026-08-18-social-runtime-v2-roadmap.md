# Groupmate Social Runtime v2 实施总路线图

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 删除旧 Groupmate 领域路线，以 clean-slate Social Runtime v2 重建能够达到并超过目标群聊伙伴效果的插件。

**Architecture:** 新核心位于 `groupmate/social_runtime/`，以 Durable Event Fabric、PersonaSupervisor、GroupSceneActor、Social Governor、ActionPlan、Task Runtime 和 Transactional Outbox 为唯一权威线路。旧 Workflow、Runtime、Store、配置、页面和架构型测试在 M0 清除；低层 AstrBot/OneBot/Provider 调用只有通过新 Contract Test 后才可按 V2 类型重新采用。

**Tech Stack:** Python 异步运行时、标准库 `dataclasses`/`asyncio`/`sqlite3`、pytest、AstrBot `>=4.24,<5`、OneBot、原生 ES Modules、CSS、SSE。

**Spec:** `docs/superpowers/specs/2026-08-18-groupmate-social-runtime-v2-design.md`

## Global Constraints

- 运行时继续只使用 Python 标准库和 AstrBot 已提供的包；未经单独评审不增加生产依赖。
- 每个人格只有一个 `PersonaSupervisor` 可以写全局自身状态。
- 每个 `(persona_id, group_id)` 只有一个 `GroupSceneActor` 可以写群世界状态。
- 模型只能返回观察、候选、草案或影响建议，不能授权状态、工具、配置或发送。
- 每个群同一时刻只能处于 `OFF`、`SHADOW`、`SOCIAL_RUNTIME` 之一。
- `SHADOW` 禁止平台发送、外部副作用和正式社会状态写入。
- 群内关系、印象、文化、原始消息和主动关心依据默认禁止跨群读取。
- 每次认知周期冻结一个配置版本和一个场景版本。
- 每个可见 Delivery Part 必须具有 Decision、ActionPlan、DeliveryBundle 和幂等身份。
- 回放不得重发已经确认的历史消息；未知发送状态不得盲目重试。
- 页面和 Projection 故障不得阻塞 Actor 权威写线路。
- 不迁移旧数据库，不兼容旧内部 API，不让 V2 领域层导入任何旧领域模块。

---

## 1. 分支与工作区策略

执行第一阶段前使用 `superpowers:using-git-worktrees`：

```bash
git status --short
git worktree add .worktrees/social-runtime-v2 -b refactor/social-runtime-v2
```

约束：

- `refactor/social-runtime-v2` 是唯一 V2 集成分支；
- 每份阶段计划按任务小步提交，不把多个验收单元压成一个提交；
- M0 在新分支删除旧架构，Git 历史已足以恢复误删文件；
- 阶段验收失败时修正 V2，不恢复旧架构来绕过问题；
- 不在主工作区直接开发 V2。

## 2. 五份执行计划

| 顺序 | 计划 | 里程碑 | 独立交付物 | 接管权限 |
|---|---|---|---|---|
| A | `2026-08-18-social-runtime-v2-phase-a-foundation.md` | M0–M2 | 旧架构清除、事件存储、Supervisor、Scene Actor、恢复 | 仅 OFF/Shadow |
| B | `2026-08-18-social-runtime-v2-phase-b-cognition-social-state.md` | M3–M5 | 世界模型、注意力、Worker、意图、Governor、人格与社会记忆 | 仅 Shadow |
| C | `2026-08-18-social-runtime-v2-phase-c-actions-tasks-delivery.md` | M6 | ActionPlan、风格、媒体、任务、Outbox、自主机会 | 测试群可发送 |
| D | `2026-08-18-social-runtime-v2-phase-d-control-plane-page.md` | M7 | CQRS 控制面、配置版本、SSE、五工作区插件页面 | 管理员可治理 |
| E | `2026-08-18-social-runtime-v2-phase-e-shadow-rollout.md` | M8–M10 | Shadow 评估、故障演练、逐群接管、灾难恢复 | 生产逐群接管 |

必须按 A → B → C → D → E 执行。阶段内任务按文档顺序执行；阶段间接口以各计划的“Produces”声明为准。

## 3. 锁定后的文件结构

```text
groupmate/social_runtime/
├── __init__.py
├── contracts.py                 # 共享 ID、事件、版本、RuntimeMode
├── event_fabric.py              # Durable Inbox → Actor 路由与 Cursor 提交
├── manager.py                   # Supervisor/Scene Actor 生命周期
├── supervisor.py                # 全局 Persona 状态单写者
├── scene_actor.py               # 群世界单写者与异步结果回流
├── world.py                     # 多话题 GroupWorldState Projection
├── attention.py                 # Fast/Ambient/Temporal Attention
├── intentions.py                # Persona Goals → CandidateIntention
├── governor.py                  # 确定性 ACT/DEFER/OBSERVE/SILENCE
├── cognition/
│   ├── contracts.py             # Worker 输入输出协议
│   ├── blackboard.py            # 单周期证据黑板
│   ├── service.py               # 成本分级与 Worker 调度
│   └── astrbot_workers.py       # AstrBot 模型适配
├── persona/
│   ├── constitution.py          # 不可变 Constitution 版本
│   ├── self_state.py            # GlobalSelfState Effect 策略
│   └── modes.py                 # Mode Director
├── society/
│   ├── relationships.py         # 多维关系事件与 Projection
│   ├── impressions.py           # 社交印象与使用范围
│   └── culture.py               # 群文化晋升与衰减
├── memory/
│   ├── pipeline.py              # 候选、作用域、冲突、墓碑
│   ├── retrieval.py             # 意图/对象驱动召回
│   └── consolidation.py         # 周期整合与校准候选
├── actions/
│   ├── contracts.py             # ActionPlan DAG 与 DeliveryBundle
│   ├── planner.py               # GovernorResult → ActionPlan
│   ├── validator.py             # 有限性、权限、版本、所有权
│   ├── style.py                 # StyleDirective 与输出护栏
│   └── coordinator.py           # 计划节点推进
├── tasks/
│   ├── contracts.py             # TaskRun 与 Provider Event
│   └── runtime.py               # 持久任务状态机
├── media/
│   ├── contracts.py             # 素材许可、标签和限制
│   └── registry.py              # 去重、冷却和选择
├── delivery/
│   ├── outbox.py                # 持久 Outbox 状态机
│   └── dispatcher.py            # OneBot 发送与回执回流
├── persistence/
│   ├── schema_v22.py            # 全部 V2 Shadow 表和约束
│   ├── event_store.py           # Inbox、Journal、Snapshot、Cursor
│   └── repositories.py          # V2 状态仓储
├── control/
│   ├── commands.py              # 服务端验证的领域 Command
│   ├── queries.py               # 只读 Projection Query
│   ├── config_versions.py       # Draft/Validate/Dry-run/Publish
│   ├── projections.py           # 独立 Cursor 的 Read Model
│   └── stream.py                # 隐私裁剪 SSE
pages/settings/
├── index.html
├── app.js                       # 仅启动器
├── bridge.js                    # iframe/API/SSE
├── router.js                    # Hash Route
├── store.js                     # 前端 Projection Store
├── i18n.js
├── components/
├── workspaces/
│   ├── runtime.js
│   ├── persona.js
│   ├── people.js
│   ├── activity.js
│   └── governance.js
└── styles/
```

## 4. 测试目录和 Marker

```text
tests/
├── shared/                      # 平台、隐私、存储、能力、发送不变量
├── social_runtime/              # V2 领域单元与集成
├── scenarios/                   # 多消息社会场景
├── contracts/                   # Worker/Capability/Projection/Command
├── recovery/                    # 崩溃、重复、过期、部分成功、未知
├── evaluation/                  # 目标效果、安全和 Shadow
└── page/                        # iframe、路由、命令、响应式、可访问性
```

M0 删除旧测试目录中的架构行为测试，并根据权威规格重新建立 Shared 不变量测试。旧聊天导出只进入 `tests/evaluation/fixtures/`，不进入运行时测试。

## 5. 阶段验收门

### Gate A：可恢复的 Shadow 骨架

```bash
pytest -m 'shared or social_runtime or recovery' -q
python -m tests.architecture_guard
```

必须证明重复事件只入 Journal 一次、Actor 重启恢复相同版本、Shadow 无发送、副作用为零，并且生产代码和测试中均无旧领域导入。

### Gate B：确定性社会决策

```bash
pytest tests/social_runtime tests/scenarios tests/contracts -q
```

必须证明硬约束压倒效用、过期观察不行动、并行话题对象正确、模型输出不能直接修改状态。

### Gate C：可靠行动与交付

```bash
pytest tests/social_runtime/actions tests/social_runtime/tasks tests/recovery -q
```

必须证明 ActionPlan 有限、Task 状态合法、未知发送不重发、部分发送可恢复、自主机会执行前重新验证。

### Gate D：可治理控制面

```bash
pytest tests/contracts tests/page tests/test_plugin_page_assets.py -q
```

必须证明 Expected Version 冲突返回 409、页面不直接写领域表、SSE 断线可续传、iframe 内五个工作区可用。

### Gate E：生产接管

```bash
pytest -q
python -m eval.runner --suite social-runtime-v2 --mode shadow
python -m eval.behavior_diff --baseline target-labels --candidate social-runtime-v2
```

除规格第 25 节的生产门槛外，接管报告还必须记录 Actor backlog、Worker 调用量、P95 决策延迟、Projection lag、发送未知率和回退演练结果。

## 6. 提交节奏

每个任务遵循同一循环：

1. 写一个只覆盖当前验收行为的失败测试；
2. 运行精确测试并保存预期失败原因；
3. 写使该测试通过的最小领域实现；
4. 运行精确测试和本阶段回归集；
5. 执行 `git diff --check`；
6. 只暂存任务文件并提交；
7. 在进入下一任务前检查 `git status --short`。

阶段完成后使用 `superpowers:requesting-code-review`；全部阶段与生产门槛完成后才使用 `superpowers:finishing-a-development-branch`。

## 7. 明确禁止的兼容行为

- 不保留或包装 `groupmate/engine/workflow.py`、`groupmate/engine/runtime.py`、旧 `SQLiteMemoryStore` 和旧页面 Snapshot API；
- 不读取或迁移旧 `groupmate.db`；
- 不让旧测试约束新类型、目录或执行顺序；
- 不允许 V2 Shadow 通过现有 `PlatformPort` 发送；
- 不在页面实现服务端授权或效用计算；
- 不用随机概率替代 Social Governor；
- 不在旧插件实例仍有外部副作用或发送能力时启动 V2 正式发送；
- 不为旧内部 API 设置兼容版本周期。

## 8. 完成定义

只有以下条件全部满足，Social Runtime v2 重构才算完成：

- M0–M10 的阶段提交均可追溯到本路线图和权威规格；
- 全量测试、场景测试、恢复测试、页面测试和 Shadow 评估通过；
- 生产群可以逐群切换，旧实例停止与 V2 启用之间不会发生双回复；
- 所有可见动作可追溯到 Event → Scene → Attention → Cognition → Intention → Governor → Plan → Bundle → Receipt；
- 跨群隐私泄漏、内部推理泄漏、未授权工具和重复交付均为零；
- 页面能够完成暂停、检查、配置发布、纠正、遗忘、复核和回滚；
- 架构守卫证明新代码不再导入旧领域模块，旧数据库和旧 API 不属于 V2 发布面。
