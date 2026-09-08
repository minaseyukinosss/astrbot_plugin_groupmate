# Groupmate 插件结构缺陷与重构任务书

> 交付对象：接手本任务的 AI 助手或工程师
> 生成日期：2026-09-01
> 代码基线：`astrbot_plugin_groupmate` v1.0.0-rc.33
> 需求基准：以 `docs/superpowers/specs/` 与 `docs/superpowers/plans/` 中**日期最新**的文档为准
> 前置依赖：**本文档的所有改动必须在 `analysis/effectiveness-fix-plan-20260831.md` 的四个必修项完成之后进行**

---

## 0. 硬性约束

1. **不要换宿主框架。** 评估见 `analysis/framework-selection-20260831.md`，结论是先修效果、再收敛边界，最后才评估迁移。
2. **不要禁用或删除任何有最新 spec 支持的功能。** 特别是 `knowledge/`（游戏知识）——它是 2026-08-28 正在进行的需求重心，见第 1.3 节。本文档不主张削减功能，只主张调整承载这些功能的结构。
3. **不要动 SHADOW 零发送门。** `2026-08-24-participation-policy-and-dialogue-lease-design.md` 已确认其位置正确。
4. **测试从简。** 只为发生行为变化的地方写针对性测试。纯搬移代码（移动方法、改依赖注入方向）**不需要新增测试**，依靠既有测试保持通过来验证。详见第 4.2 节。
5. **一次只做一项，每项独立可回滚。** 本文档的六项改动之间没有强依赖，但都依赖前置的效果修复。
6. **保持中文注释风格**，与现有代码一致。

---

## 1. 背景：这个插件在做什么

### 1.1 技术栈

QQ 群聊 AI 机器人插件，人格名"爱弥斯"。NapCat（NTQQ 无头客户端，OneBot 11）→ AstrBot（Python 插件平台）→ 本插件。核心 `groupmate/social_runtime/` 共 108 文件约 41,000 行，零 astrbot import。存储为 SQLite，约 58 张表。运行模式 `OFF` / `SHADOW`（完整决策零发送）/ `SOCIAL_RUNTIME`。

### 1.2 功能全景

`2026-08-18-groupmate-social-runtime-v2-design.md` 定义了一个持续存在的社会智能体运行时，七个业务域：

| 域 | 内容 |
|---|---|
| 持久事件底座 | Durable Inbox / Journal / Replay / Projection Cursor |
| Actor 层级 | 一个 Persona Supervisor（共享自我）+ 每群一个 Group Scene Actor（群世界） |
| 注意力 | 三路：Fast（明确互动）/ Ambient（环境窗口）/ Temporal（时间机会） |
| 认知 | Cognition Blackboard + 无状态 Worker，四个成本等级 |
| 决策 | Intention Engine + Social Governor（14 个效用维度，硬门优先） |
| 人格与社会状态 | Constitution / Self Model / 全局状态 / 模式；关系、印象、群文化、六层记忆 |
| 行动与交付 | ActionPlan DAG + StyleDirector + 媒体 + 能力 + 任务 + 事务性 Outbox |

外加控制面（Command/Query 分离、配置版本化、SSE）与插件页面。

### 1.3 需求演进（这决定了什么不能碰）

specs 与 plans 按日期演进，**后出覆盖先出**：

| 日期 | 主题 | 状态 |
|---|---|---|
| 08-18 | v2 总设计（M0–M10 里程碑） | 架构基准 |
| 08-21 | P0 闲聊主线、消息 trace 控制台 | **部分已被 08-24 覆盖** |
| 08-24 | 参与策略与对话租约、AMBIENT 延迟根因修复、认知输出修复、可观测性 | **核心需求基准** |
| 08-25 | 好感度系统、自适应好感名册、响应归属、参考 bot 触发机制 | 有效需求 |
| 08-26 | 成员画像与社交图谱、trace 状态与人格配置 | 有效需求 |
| 08-27 | 闲聊风格系统、成员风格模仿、画像加固、世界知识与版本落地 | 有效需求 |
| 08-28 | 游戏版本公开事实、游戏知识（本地认知/环境扩展/落地回复） | **当前进行中** |

`.superpowers/sdd/2026-08-28-game-version-public-facts/progress.md` 显示 Task 1–6 均已完成或收尾。**因此 `knowledge/` 的 9,856 行是当前投入重心，任何"禁用知识链以简化主链"的建议都与最新需求冲突，不要执行。**

已被 08-24 明确列为"本轮不实现"且此后未被恢复的，只有：冷启动主动开场。其余（媒体、人格配置页面、多模态）都在 08-26 与 08-27 的 spec 中回归。

---

## 2. 结论：缺陷的性质

分层设计本身是合理的——事件底座 → Actor → 注意力 → 认知 → 意图 → 治理 → 计划 → 交付，这条链与产品目标对得上，不需要重画。

**真正的结构缺陷是：七个业务域的需求全部堆在同一个编排层上，核心闭环没有独立的、可测的边界。**

后果不是抽象的。`effectiveness-fix-plan` 里那个决定性 bug——`scene_actor.py:429` 用当前 `scene_version` 去比冻结帧的 `scene_version`，导致五张决策表永久为空——之所以能长期存活，正是因为核心决策逻辑淹没在 4,216 行编排代码里，而且核心无法脱离宿主独立回放。**结构问题的代价是延长了核心 bug 的存活时间。**

以下六项按"改动风险从低到高"排序。

---

## 3. 六项结构缺陷

---

### S1. clean-slate 未收尾：十个空目录在暗示一个不存在的架构

**风险等级：最低（纯清理，无行为变化）**

#### 证据

`groupmate/` 下有十个目录**不含任何 `.py` 文件**：

```
capabilities/   core/   engine/   fun/   host/
mail/           memory/ persona/  social/ tools/
```

它们只剩 `__init__.pyc` 和空的子目录：`capabilities/providers`、`fun/features`、`host/event_adapters`、`persona/aemeath`。

另有残留字节码：`groupmate/` 下 13 个非 `__pycache__` 的 `.pyc`，`tests/` 下 35 个（`test_affinity.pyc`、`test_memory.pyc`、`test_scenes.pyc` 等 v1 时代产物）。`git ls-files` 显示这些 `.pyc` **一个都没被跟踪**，纯属本地残留。

`2026-08-18-groupmate-social-runtime-v2-design.md` 第 253 行原文："当前 `engine`、`core`、`social`、`memory`、Host Bridge、配置和页面实现都可以删除。" M0 里程碑要求"旧架构代码与测试清除"。这项工作只删了 `.py`，没删目录和字节码。

#### 为什么是缺陷

目录结构是架构的第一份文档。十个空目录在向每一个接手者（人或 AI）宣告一套不存在的分层。

这不是理论风险：本次调查中，一个负责审查适配层的探查员报告"`groupmate/host/event_adapters/` 在仓库中不存在，全库无引用"，而 `ls` 显示它确实存在——因为里面只有一个 `.pyc`。任何自动化工具、任何新人、任何 AI 助手都会先在这里被误导一轮。

#### 修复

删除十个空目录及其空子目录，删除所有非 `__pycache__` 的 `.pyc` 残留。确认 `.gitignore` 已覆盖 `*.pyc` 与 `__pycache__/`。

删除前用一条命令确认没有任何 `.py` 文件与实际 import 依赖它们：

```bash
# 确认目录内无源文件
find groupmate/{capabilities,core,engine,fun,host,mail,memory,persona,social,tools} -name '*.py' | wc -l   # 期望 0
# 确认无人 import 这些模块
rg -n 'from (groupmate\.)?(capabilities|core|engine|fun|host|mail|memory|persona|social|tools)|import (groupmate\.)?(capabilities|core|engine|fun|host|mail|memory|persona|social|tools)' --type py
```

注意第二条命令会匹配到 `social_runtime`、`persona_*` 等合法名称，需人工甄别；真正要确认的是没有指向这十个包本身的 import。

#### 验收

`pytest` 全绿（不应有任何变化）。`find groupmate -name '*.pyc' -not -path '*__pycache__*' | wc -l` 为 0。

---

### S2. 死代码与单实现抽象过载

**风险等级：低**

#### 证据

**确认的死代码：**

| 项 | 状态 | 位置 |
|---|---|---|
| `AstrBotStructuredWorker` | 生产零引用，已被 `DirectAmbientWorker` 取代 | `social_runtime/cognition/astrbot_workers.py:18` vs `adapters/astrbot_bridge.py:925-929` |
| `AstrBotCapabilityAdapter` | bridge 未接线，仅测试引用 | `adapters/astrbot_capabilities.py`（191 行） |
| `vision_provider` | schema 与 settings 中存在，运行时无消费者 | `groupmate/settings.py:91`、`_conf_schema.json` |

**九个 Protocol 各只有一个实现：** `CognitiveWorker`、`KnowledgeResolverPort`、`DeliveryTransport`、`TextModelPort`、`StructuredModelPort`、`SocialSceneModelPort`、`OfficialSourceProbePort`、`DiscoverySearchPort`、`ReleaseStateReader`。

#### 为什么是缺陷

抽象本身没错，但九个单实现接口说明抽象是按"设计文档里可能需要"而非"实际存在的变化点"建立的。每个多余的 Protocol 都在阅读时增加一次跳转，在修改时增加一处需要同步的签名。

需要区别对待：`OfficialSourceProbePort` 有明确的宿主能力注入语义（见 sdd ledger 中 Task 4 的裁定：不绑定 AstrBot 的 `fetch_url`，而要求显式受约束能力），这个抽象是**有理由的**，应保留。`DeliveryTransport` 是框架迁移的关键接缝，也应保留。

#### 修复

删除三处死代码及其测试。逐个审视九个 Protocol，只保留满足以下任一条件的：跨框架接缝（`DeliveryTransport`、`TextModelPort`、`StructuredModelPort`）、宿主能力注入点（`OfficialSourceProbePort`）、测试替身必需。其余就地内联为具体类型。

**注意**：`tests/shared/test_architecture_boundaries.py` 可能对模块依赖方向有断言，删除抽象前先读它。

#### 验收

`pytest` 全绿。删除死代码时对应测试一并删除，不要保留只测试死代码的用例。

---

### S3. 核心无法脱离宿主独立回放

**风险等级：中**

#### 证据

`social_runtime/` 内**无直接 astrbot import**（这点做得很好），但存在五处指向 `adapters/` 的反向依赖：

| 位置 | 依赖 |
|---|---|
| `social_runtime/cognition/ambient_worker.py:8` | `...adapters.deepseek_cognition` |
| `social_runtime/profile/service.py:11` | `...adapters.deepseek_profile` |
| `social_runtime/profile/style_service.py:10` | `...adapters.deepseek_member_style` |
| `social_runtime/control/message_traces.py:13-14` | `...adapters.participants`、`...adapters.message_media` |
| `social_runtime/profile/policy.py:7` | `...profile_vocabulary` |

此外核心依赖若干由 bridge 注入的约定：envelope 的 `mentions_bot` / `reply_to_bot` / `direct_address` / `social_eligible` 字段决定注意力分流（`attention.py:81-82, 346-353`）；worker 注入、drain 循环、attention wakeup 全由 bridge 驱动（`astrbot_bridge.py:919-935, 1094-1098, 1274`）。

#### 为什么是缺陷

`2026-08-18` 设计的第 6.3 节明确要求事件回放能力，用途包括"使用固定 Worker 输出进行确定性测试"和"调查关系、状态、记忆、任务和发送变化"。这个能力目前无法使用：核心跑不起来，除非把整个 AstrBot 宿主拉起来。

代价是具体的：`effectiveness-fix-plan` 4.2 描述的 stale bug，本该能用一次离线回放在几分钟内定位（喂入 5 条消息、观察 accept 结果），实际却要靠读 SHADOW 数据库的空表反推。**这项缺陷直接延长了核心 bug 的诊断时间。**

它同时也是框架自由度的关键：`framework-selection-20260831.md` 指出，修好这五处依赖后迁移面会从 4,400 行降到约 1,000 行。

#### 修复

把这五处改为依赖注入。`ambient_worker`、`profile/service`、`profile/style_service` 应接受一个满足 `StructuredModelPort` 的客户端实例，而不是 import 具体的 deepseek 适配器；`message_traces` 对 `participants` / `message_media` 的依赖应改为传入裁剪函数；`profile/policy` 对 `profile_vocabulary` 的依赖如果只是静态词表，可以移入 `social_runtime/profile/` 内部。

然后新增一个最小的离线回放入口（建议 `social_runtime/replay.py`，约 100 行），能力要求：从 SQLite journal 或一个 JSONL 事件列表喂入事件、用可注入的假 worker 替代真模型、打印每条事件的 lane / candidates / governor outcome / accept 结果。

#### 验收

一个测试：用假 worker 和内存 SQLite 跑通"5 条消息 → 输出决策序列"，全程不 import 任何 `adapters` 模块。

`tests/shared/test_architecture_boundaries.py` 应能加一条断言：`social_runtime` 不 import `adapters`。如该文件已有类似断言，说明当前是被豁免的，请一并收紧。

---

### S4. 编排层上帝对象（核心缺陷）

**风险等级：高（改动面最大，但收益也最大）**

#### 证据

实测两个编排类：

| 类 | public 方法 | private 方法 | 总计 | 行数 | 构造参数 |
|---|---:|---:|---:|---:|---:|
| `SocialRuntimeManager` | 28 | 19 | 47 | 1,641 | **15** |
| `AstrBotSocialRuntimeBridge` | 16 | 39 | 55 | 2,575 | — |

`SocialRuntimeManager` 的 28 个 public 方法按职责分类：

| 职责 | 方法 |
|---|---|
| 生命周期 | `start`、`close` |
| 主链 | `ingest`、`drain`、`next_attention_deadline`、`expired_attention_count` |
| 场景守卫 | `group_snapshot`、`freeze_scene_guard`、`current_scene_guard`、`validate_scene_guard` |
| 人格 | `persona_profile_mapping`、`persona_snapshot` |
| **关系与画像数据访问** | `relationship_affection`、`relationship_projection`、`member_profile_retrieval`、`relationship_memory_records`、`group_member_refs`、`relationship_memory_cues`、`member_profile_context` |
| 治理 | `governance_state`、`update_governance_state`、`group_mode`、`require_social_runtime_group` |
| SHADOW 证据 | `pending_shadow_review_evidence`、`complete_shadow_review_evidence`、`update_shadow_review_evidence` |
| 表达 | `record_usable_reply`、`submit_plan` |

`AstrBotSocialRuntimeBridge` 的 16 个 public 方法中：

- **6 个是服务 getter**：`trace_repository`、`manager`、`profile_service`、`knowledge_service`、`member_style_repository`、`member_style_service`
- **4 个是命令处理**：`prepare_affection_query`、`prepare_imitation_transition`、`prepare_imitation_command`、`prepare_profile_command`
- 6 个是本职：`start`、`close`、`handle_event`、`observe_event`、`runtime_status`、`resolved_persona_status`

#### 为什么是缺陷

三个具体问题，都不依赖需求判断：

**其一，15 个构造参数意味着这个类的职责无法用一句话描述。** 它同时是主链编排器、场景守卫、人格快照源、关系画像数据门面、治理状态机和 SHADOW 证据仓库。

**其二，那 7 个关系与画像方法是数据访问，不是编排。** 它们是 08-25 好感度和 08-26 画像需求的产物——需求本身完全有效——但它们让核心 manager 变成了 bridge 读取外围数据的门面。bridge 需要画像时走 `manager.member_profile_retrieval()`，于是核心与外围通过 manager 强耦合。

**其三，bridge 的 6 个服务 getter 是服务定位器反模式。** 它把 bridge 变成了全局服务注册表，任何模块拿到 bridge 就能拿到一切，依赖关系从此不可见。4 个 `prepare_*` 命令处理器混在事件桥里，让"翻译平台事件"和"处理群内命令"两件事共享同一个类。

**合并起来的后果**：每个新需求都往这两个类加方法。核心闭环的修改风险随外围功能数量单调上升。这就是 stale bug 长期存活的结构原因。

#### 修复

分三步，每步独立可回滚。**这是纯搬移，不改变任何行为。**

**第一步：从 manager 抽出关系画像门面。** 新建 `social_runtime/member_insight.py`，定义 `MemberInsightFacade`，接收它需要的仓库依赖，承接那 7 个方法。bridge 直接持有 facade，不再经过 manager。manager 的 public 方法降到 21 个，构造参数减少。

**第二步：从 bridge 抽出命令处理。** 新建 `adapters/command_handlers.py`（或 `groupmate/commands/`），承接 4 个 `prepare_*`。`main.py:119-155` 的命令分派改为直接调用它，不经过 bridge。

**第三步：消除服务定位器。** 删除 6 个服务 getter，改为在组合根（bridge 的 `start` 或 `main.py`）显式把所需服务注入给需要它的对象。这一步会暴露真实的依赖图，可能发现循环依赖——遇到时优先把共享的数据结构下移到 `contracts.py`，而不是保留 getter。

**顺序建议**：先做第一步和第二步（纯搬移，低风险），第三步单独评估。如果第三步暴露出大量循环依赖，说明需要更深的设计调整，此时应停下来单独立项，不要在本轮硬改。

#### 验收

**不需要新增测试。** 这是纯搬移，验收标准就是既有测试全部保持通过——如果搬移引入了行为变化，既有测试会失败。

每一步完成后跑 `pytest`。如果某个测试因为直接调用了被搬移的方法而失败，只改调用路径，不改断言。

---

### S5. 三套并行的"证据 → 策略 → 投影"实现

**风险等级：中（收益取决于后续是否还有同类需求）**

#### 证据

三个领域各自实现了同一个模式：模型提出结构化候选 → 本地证据策略决定是否确认 → 投影到隐私裁剪的读模型。

| 领域 | 位置 | 规模 |
|---|---|---|
| 成员画像 | `social_runtime/profile/`（含 `policy.py`、`service.py`、`style_service.py`） | 14 文件 / 3,905 行 |
| 好感度与关系 | `social_runtime/society/` + `social_runtime/memory/` | 11 文件 / 1,316 行 |
| 游戏知识 | `social_runtime/knowledge/` | 16 文件 / 9,856 行 |

三者的共同结构在各自 spec 中都有描述：`2026-08-26-group-member-profiles-and-social-graph-design.md`（自述可确认、第三方转述不注入、多次独立证据）、`2026-08-25-groupmate-affection-system-design.md`、`2026-08-27-world-knowledge-and-game-version-grounding-design.md`（结构化 claim、来源可信度、否定快照）。

#### 为什么是缺陷

这是**实现层的重复，不是需求层的冗余**——三个需求都有效，都该保留。问题在于"证据等级判定、冲突检查、过期衰减、墓碑防止重学、隐私裁剪投影"这五件事被写了三遍，各自有独立的 bug 面。sdd ledger 中 Task 3 关于"证据独立性由已推导的证据等级表示"的裁定，就是在第三套实现里重新解决一个前两套已经解决过的问题。

#### 修复

**不要现在重构。** 抽取共用框架的正确时机是出现第四个同类需求时，或者三者之一需要大改时。现在动它风险高于收益，而且 `knowledge/` 正在活跃开发中，不应并发重构。

本轮只做一件事：写一份约两页的对照文档（建议 `docs/superpowers/evidence-pipeline-comparison.md`），逐项对比三者在证据等级、冲突处理、过期衰减、墓碑、投影裁剪上的具体差异。这份文档的作用是：下一次改动其中任何一个时，能立刻知道另外两个是否有同样的问题。

#### 验收

文档产出即可，无代码改动，无测试。

---

### S6. 表达栈层数与生成预算未对齐（待度量，非确认缺陷）

**风险等级：需先度量再决定**

#### 证据

ACT 路径在 bridge 内依次经过：场景解析（`SocialSceneInterpreter`，`astrbot_bridge.py:1884`）→ 立场决策（`_stance_policy.decide`，1904）→ 社交动作规划（`_move_planner.plan`，1916）→ 表达与回复规划（`ReplyPlanner.plan`，`replying.py:354`）→ 生成执行（`ReplyExecutor`）。

#### 为什么这里只标风险而不下结论

`2026-08-24-participation-policy-and-dialogue-lease-design.md` 的验收目标只约束**参与决策**时延（DIRECT_FAST P50 ≤1s / P90 ≤3s，AMBIENT P50 ≤12s / P90 ≤15s），并明确写着"完整候选回复生成时延单独统计，不与参与决策混为一个指标"。

也就是说，最新 spec **没有给生成阶段设定硬指标**。因此我无法断言五层表达栈超预算——那需要实测数据。

作为参考（非验收线）：参考 Bot 的文本回复偏短，直接互动端到端时延通常落在数秒到十余秒。如果本项目的五层表达栈实测远超这个量级，才构成需要压缩层数的证据。

#### 修复（度量优先）

在 `effectiveness-fix-plan` 的四项修完、ACT 路径真正跑通之后，先给这五层各自加一个耗时诊断字段（复用 `2026-08-24-ambient-cognition-latency-root-fix-design.md` 已经确立的诊断模式：`queue_wait_ms`、`provider_latency_ms`、`input_bytes`、`timeout_ms`），跑一轮 SHADOW 拿到分层耗时分布。

然后再决定：如果某一层耗时占比高且产物未被下游真正使用，就合并或删除它；如果分布均匀且总时延可接受，就保留。

**在拿到数据之前不要动这五层。**

#### 验收

分层耗时诊断进入 trace，能在运行中心看到 ACT 路径的耗时构成。

---

## 4. 执行顺序与测试策略

### 4.1 顺序

**前置门槛**：`analysis/effectiveness-fix-plan-20260831.md` 的四项必修完成，且 SHADOW 数据显示明确互动非零候选率 ≥99%、五张决策表非空。**在 0% ACT 的状态下重构，无法验证是否改坏。**

之后按风险递增：

| 步骤 | 项目 | 风险 | 说明 |
|---|---|---|---|
| 1 | **S1** 清理空目录与 pyc | 最低 | 无行为变化，先做掉，让后续调查不再被误导 |
| 2 | **S2** 删死代码、收敛单实现 Protocol | 低 | 独立可回滚 |
| 3 | **S3** 五处反向依赖改注入 + 离线回放入口 | 中 | 收益最大：让核心可独立诊断 |
| 4 | **S4** 第一步与第二步（抽 facade、抽命令处理） | 中 | 纯搬移 |
| 5 | **S6** 加分层耗时诊断并度量 | 低 | 只加观测，不改结构 |
| 6 | **S5** 证据流水线对照文档 | 无 | 文档产出 |
| 7 | **S4** 第三步（消除服务定位器） | 高 | 单独评估，暴露循环依赖时停下立项 |

### 4.2 测试策略（从简）

本项目明确要求减少测试投入，`.superpowers/sdd/` 的 ledger 中多次记录"honor the user's request to avoid excessive tests"，做法是把同族行为合并成表驱动测试并限制新增函数数量。本文档沿用该原则。

**本轮新增测试上限：两个。**

1. S3 的离线回放测试（一个函数，验证核心不 import adapters 即可跑通决策序列）
2. S3 的架构边界断言（可以加进 `tests/shared/test_architecture_boundaries.py` 的既有表格，不算新函数）

**其余五项都不需要新测试**，理由：S1、S2、S4 是纯清理与搬移，行为不变，既有测试全绿即为验收；S5 只产出文档；S6 只加诊断字段。

**必须保持通过的既有测试**（编码的是安全不变量，不是实现细节）：

- `tests/shared/test_shadow_side_effects.py` —— SHADOW 零副作用
- `tests/shared/test_group_scope_privacy.py` —— 跨群隐私隔离
- `tests/shared/test_architecture_boundaries.py` —— 模块依赖方向
- `tests/social_runtime/test_governor.py` —— 硬门不被绕过

改动涉及哪个模块就跑对应目录，不必每次全量。若既有测试因直接调用被搬移的方法而失败，只改调用路径，不改断言。

### 4.3 明确不做

- 禁用或删除 `knowledge/`、`profile/`、`society/` 等有最新 spec 支持的功能
- 合并 S5 的三套证据流水线（时机未到，且 knowledge 正在活跃开发）
- 压缩 S6 的表达栈层数（缺度量数据）
- 移动 SHADOW 门、调整 AMBIENT 8 秒预算或窗口时长（spec 设计值）
- 更换宿主框架
- 重画分层（分层设计本身合理）

---

## 5. 关键事实索引

| 事实 | 数值 | 来源 |
|---|---|---|
| `social_runtime` 规模 | 108 文件 / 40,877 行 | 实测 |
| `tests` 规模 | 171 文件 / 38,609 行 | 实测 |
| 绑定 AstrBot 的适配层 | 约 4,400 行 | 实测 |
| `SocialRuntimeManager` | 47 方法 / 15 构造参数 / 1,641 行 | AST 实测 |
| `AstrBotSocialRuntimeBridge` | 55 方法 / 2,575 行 | AST 实测 |
| 空目录 | 10 个 | 实测 |
| 非 `__pycache__` 的 pyc 残留 | groupmate 13 + tests 35 | 实测 |
| 单实现 Protocol | 9 个 | 实测 |
| 数据库表 | 约 58 张 | `persistence/schema.py:21-42` |
| 已有的功能开关 | 仅 `profile_enabled`、`knowledge_enabled` | `groupmate/settings.py:35,39` |
