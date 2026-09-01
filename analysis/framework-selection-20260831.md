# 框架选型评估与架构审查（2026-08-31）

> **2026-09-01 修订说明。** 本文档初版有两处依据过期需求文档的错误，已就地更正：
> 1. 第 4.2 节原建议禁用 `knowledge` / `profile` 等外围能力以简化主链——**已撤回**。08-28 的游戏知识是当前进行中的需求重心。
> 2. 涉及"把 SHADOW 门下移到 DeliveryGate"的表述——**已撤回**。`2026-08-24-participation-policy-and-dialogue-lease-design.md` 已确认 SHADOW 门当前位置正确。
>
> 需求基准统一为 `docs/superpowers/` 下**日期最新**的文档。配套文档：`analysis/effectiveness-fix-plan-20260831.md`（效果修复）、`analysis/structural-refactor-plan-20260901.md`（结构重构）。

## 结论摘要

1. **当前 bot 不说话、慢 23 秒，与框架选型无关。** 根因是一个发生在 `social_runtime` 内部的自锁闭环：延迟高导致 `scene_version` 过期，评估被判 stale 丢弃并触发认知降级，降级又必然 `force_observe`，而延迟本身有一半来自 OBSERVE 路径上的无用 LLM 调用。这套代码搬到 NoneBot2 之后，闭环一字不变。
2. **换 NoneBot2 有三项真实收益**（notice/poke 事件、事件保真度单一真相源、进程级生命周期），但没有一项能改善上述效果问题。
3. **换框架代价约 4,400 行适配层重写，并失去 AstrBot 配置 UI、`html_render`、`tool_loop_agent` 搜索 Agent 和零配置 WebUI 挂载**——最后一项尤其致命，因为 `message_traces` 控制面是当前唯一能观测决策链的手段。
4. **建议顺序：先修自锁闭环 → 再做结构清理与边界收敛 → 最后才评估换框架。** 现在换框架等于带着一个不会说话的 4 万行运行时搬家，且搬家过程中丧失唯一的观测窗口。
5. 41,020 行 `social_runtime` 中，直接服务于"像群友地说话"的约 6,000 行。其余 3.5 万行**不是冗余**——它们对应 08-25 到 08-28 的有效需求（好感度、画像、风格、游戏知识）。问题不在功能多，而在这些功能全部堆在同一个编排层上：`SocialRuntimeManager` 47 个方法 / 15 个构造参数，`AstrBotSocialRuntimeBridge` 55 个方法。

---

## 第一部分：效果问题的真实根因（与框架无关）

### 1.1 自锁闭环

```text
AMBIENT 窗口 2–5s
  + 认知 budget 8s
  + 1 次 AMBIENT 认知 LLM
  + bridge 对每条 evaluation（含 OBSERVE）无条件再调 1 次场景解析 LLM
  ↓
链路 P50 ≈ 23s
  ↓
认知在 actor 邮箱外执行，23s 内新消息推进 scene_version
  ↓
accept_result 判定 stale → evaluation=None → 五张决策表不写
  ↓
worker 超时/缺失 → blackboard.degraded=True
  ↓
AMBIENT 的 allow_degraded=False → force_observe=True
  ↓
Governor 返回 OBSERVE + forced_observe
  ↓
永不进入 ReplyPlanner → action_plans / reply_plans 必然为空
```

### 1.2 代码证据

| 环节 | 位置 | 说明 |
|---|---|---|
| 唯一设置 `force_observe` 的地方 | `groupmate/social_runtime/manager.py:1192-1198` | 条件为「认知降级且不允许降级参与」**或**「AMBIENT 参与评估未通过」 |
| Governor 强制 OBSERVE | `groupmate/social_runtime/governor.py:65-73` | 直接返回 `OBSERVE` + `forced_observe` reason code |
| 普通消息走 AMBIENT | `groupmate/social_runtime/attention.py:132-173` | 非 @/非回复的消息进入 2–5s 待定窗口 |
| AMBIENT 不允许降级 | `groupmate/social_runtime/participation.py:93` | `allow_degraded=False` |
| AMBIENT 参与门槛 | `groupmate/social_runtime/manager.py:1527-1540` | 要求 `decision=speak` 且 confidence ≥ 0.75 |
| 降级即只产出 OBSERVE 候选 | `groupmate/social_runtime/intentions.py:130-141` | `blackboard.degraded` 时短路 |
| 认知在 actor scope 外执行 | `groupmate/social_runtime/scene_actor.py:175` | 代码注释已自陈 |
| stale 判定丢弃评估 | `groupmate/social_runtime/scene_actor.py:426-462` | `status='stale'` 时不传 evaluation |
| 落库跳过五表 | `groupmate/social_runtime/persistence/event_store.py:480-481, 537-539` | `evaluation is None` 时不调 `_insert_shadow_evaluation` |
| OBSERVE 也调场景解析 LLM | `groupmate/adapters/astrbot_bridge.py:1884` | 已决定不说话仍付一次 LLM 延迟 |
| ACT 才进表达栈 | `groupmate/social_runtime/replying.py:374-378`、`actions/planner.py:31-32` | 解释了 `action_plans`/`reply_plans` 为何必然空 |
| FAST 路径的额外坑 | `groupmate/social_runtime/participation.py:110-116` | `topic_id` 为空时 `deterministic_scope_missing`，连 FAST 也会失效 |

### 1.3 四个必修项（按性价比排序）

| 优先级 | 修复 | 位置 | 预期收益 |
|---|---|---|---|
| P0 | OBSERVE 时跳过 `SocialSceneInterpreter` | `astrbot_bridge.py:1884` | 直接砍掉一次 LLM 往返，延迟立降 |
| P0 | accept 按 `frame.scene_version` 判定而非 current，或把认知移入 actor scope | `scene_actor.py:426-462` | 五张决策表开始有数据 |
| P0 | `force_observe` 后移到 DeliveryGate | `manager.py:1192-1198` → `delivery/` | 即使不发送也记录 would_reply（你的 report.md P0-2 已提出） |
| P1 | FAST 路径保证 `topic_id` 非空 | `participation.py:110-116` | 让 27.1% 的被点名场景真正走快车道 |

这四项全部在纯 Python 层，与宿主框架无关。

---

## 第二部分：NoneBot2 vs AstrBot 逐项差异

### 2.1 换过去能得到什么

| 收益 | 当前状况（证据） | 换后 |
|---|---|---|
| **notice 类事件** | `main.py:112,116` 只订阅 `GROUP_MESSAGE`，无 notice handler；`attention.py:347-349` 已为 `platform.poke` 预留 FAST 通道，但事件永远不会到达 | OneBot 11 原生 notice：poke、群表情回应、撤回、禁言、入群退群 |
| **事件保真度单一真相源** | 靠双通道拼接：`raw_message` 原始段（`astrbot_events.py:184-207`）+ AstrBot 组件链（`214-255`）+ `_enrich_segments` 补丁回填 reply/at 的 sender_id、nickname（`257-305`） | 直接消费 OneBot 原始 event，删掉 enrichment 补丁 |
| **进程级生命周期** | 8 个常驻 asyncio 任务全靠 `bridge.close()` 手动 cancel（`astrbot_bridge.py:2483-2524`）；重启行为不可控，只能靠 SQLite 恢复 | `driver.on_startup/on_shutdown`，标准 ASGI，`nonebot-plugin-apscheduler` 做定时唤醒 |
| **消除双轨发送** | 社交回复走 Outbox→OneBot 直发（`astrbot_bridge.py:1031-1037`），但画像/好感度命令走 AstrBot event yield（`main.py:119-155`），两套语义 | 全部统一到 `bot.call_api("send_group_msg", ...)` |
| **不再抢事件优先级** | `main.py:110-116` 用 priority=1000/-100 双 handler 配合其他插件，靠 `stop_event()` 抢答（`astrbot_bridge.py:1103-1113`） | 自己是主程序，无需与他人协商 |
| **配置即代码** | 19 项配置在 `_conf_schema.json`，改配置需插件重载（`main.py:29-31` 快照式读取） | pydantic-settings，CI 可测、版本可控 |

**关键判断：这六项里没有一项能改善第一部分的自锁闭环。** 唯一沾边的是"进程级生命周期"，但 stale 竞态是 `scene_actor.py` 内部的并发模型问题，不是宿主生命周期问题。

### 2.2 换过去会失去什么

| 损失 | 证据 | 替代成本 |
|---|---|---|
| **零配置 WebUI 挂载** | `pages/settings/` 靠 `window.AstrBotPluginPage` 通信（`pages/settings/bridge.js:5-7,16-34,41-66`），后端 24 个 endpoint 靠 `register_web_api`（`web_api.py:908-927`） | 需自建 ASGI 绑定、鉴权、SSE、静态托管；前端 bridge 全改 fetch + EventSource |
| **`message_traces` 控制面的可用性** | `control/message_traces.py`（1,723 行）是当前**唯一**能看到逐消息决策链的手段 | 迁移期间这个窗口会短暂失效，而你正需要它来 debug |
| **`html_render`** | 好感度卡片出图（`main.py:137-153`） | 自建 playwright / imgkit 渲染服务（中等） |
| **`tool_loop_agent` + ToolSet** | 知识联网检索 Agent（`astrbot_knowledge_search.py:128-188,236-305`，616 行） | 自研搜索 loop（大）；但注意 `knowledge_web_search_enabled` 当前为预留开关，实际未启用 |
| **配置 UI** | `_special: select_provider` 下拉（`_conf_schema.json:37-42,109-114`）、slider、hint | 无等价物，退化为 .env / 文档 |
| **LLM Provider 抽象** | `context.llm_generate`（`astrbot_models.py:25-43`） | 影响面已很小：认知已直连 DeepSeek（`astrbot_bridge.py:926-928`），只剩 generation_provider |

### 2.3 「社区说 NoneBot」这个说法对不对

对，但对的理由和你的场景不完全重合。社区推荐 NoneBot2 的典型语境是：Python 开发者要精细控制、要接 Python 生态、要多适配器。这三条你都满足——**但你已经不在"选框架"的阶段了**。你的 4 万行核心已经是框架中立的纯 Python，AstrBot 对你而言退化成了一个"OneBot 接入 + WebUI 宿主"。社区讨论的那些差异（插件生态、AI 能力内置、上手难度）对你全部无效，因为你既不用它的插件生态，也不用它的 AI 能力。

真正剩下的差异只有 2.1 和 2.2 两张表里的东西。

---

## 第三部分：迁移成本清单

### 3.1 必须重写（约 4,400 行）

| 项目 | 文件 | 工作量 |
|---|---|---|
| 插件入口 / 事件订阅 | `main.py`（195 行） | 大 |
| Bridge 组合根 | `groupmate/adapters/astrbot_bridge.py`（2,575 行，约 15% 直接耦合） | 大 |
| 知识搜索 Agent | `groupmate/adapters/astrbot_knowledge_search.py`（616 行） | 大（失去 `tool_loop_agent`） |
| 事件翻译 | `groupmate/adapters/astrbot_events.py`（308 行） | 中 |
| LLM 生成端口 | `groupmate/adapters/astrbot_models.py`（57 行） | 中 |
| Web 路由绑定 | `web_api.py` 的 `AstrBotControlPlaneRoutes`（`web_api.py:890-927`） | 中 |
| 前端 bridge | `pages/settings/bridge.js` | 中 |
| 好感度出图 | `main.py:137-153` | 中 |
| 配置加载 | `_conf_schema.json` + `groupmate/settings.py` | 中 |
| 发送适配 | `groupmate/adapters/astrbot_delivery.py`（42 行） | **小** |
| 数据目录 | `main.py:30` | 小 |
| 官方源探测 | `astrbot_official_sources.py`（399 行，已是 Protocol，bridge 传 None） | 小 |

### 3.2 直接复用（约 85% 业务代码）

- `groupmate/social_runtime/**` —— 108 文件 / 41,020 行，零 astrbot import
- `groupmate/adapters/onebot_delivery.py`（145 行）—— 已框架中立，只需换注入的 sender
- `groupmate/adapters/message_media.py` —— 无 astrbot import
- `web_api.py` 的 `ControlPlaneWebAPI` 本体（L71-885）—— 框架中立
- `groupmate/adapters/deepseek_*.py` —— 三个直连客户端
- `tests/**`（171 文件 / 38,609 行）—— 大量已用 fake context
- `pages/settings/**` 前端资产 —— 改 bridge 即可

### 3.3 隐性耦合（迁移时容易踩）

`social_runtime` 虽无 astrbot import，但依赖以下约定：

| 类型 | 内容 | 位置 |
|---|---|---|
| Envelope 字段契约 | `mentions_bot`、`reply_to_bot`、`direct_address`、`social_eligible` 决定 FAST/AMBIENT 分流 | `attention.py:81-82, 346-353` |
| adapters 反向依赖 | `cognition/ambient_worker.py:8`、`profile/service.py:11`、`profile/style_service.py:10`、`control/message_traces.py:13-14`、`profile/policy.py:7` 都 import 了 `...adapters.*` | 同左 |
| 生命周期注入 | worker 注入、drain 循环、attention wakeup 均由 bridge 驱动 | `astrbot_bridge.py:919-935, 1094-1098, 1274` |

**这意味着 `social_runtime` 目前无法独立运行。** 如果要为将来的框架自由度做投资，收敛这三处比换框架本身更有价值。

### 3.4 死代码（无论换不换都该处理）

| 项目 | 状态 | 位置 |
|---|---|---|
| `AstrBotStructuredWorker` | 生产零引用，已被 `DirectAmbientWorker` 取代 | `cognition/astrbot_workers.py:18` vs `astrbot_bridge.py:925-928` |
| `AstrBotCapabilityAdapter` | bridge 未接线，仅测试引用 | `astrbot_capabilities.py`（191 行） |
| `vision_provider` | schema/settings 中存在，运行时无消费者 | `groupmate/settings.py:91` |
| `control_admin_ids` | 代码读取但不在 schema 中，鉴权 fallback 到当前登录用户名 | `groupmate/settings.py:115-118`、`main.py:65,71` |
| `groupmate/host/event_adapters/` | 目录不存在，全库无引用 | — |

---

## 第四部分：架构瘦身清单

### 4.1 规模分布

| 层 | 文件 | 行数 | 与"像群友说话"的关系 |
|---|---:|---:|---|
| root（编排核心） | 26 | 10,286 | 核心约 6,000 行在此 |
| knowledge/ | 16 | 9,856 | 当前 0% ACT，从未触发 |
| control/ | 8 | 6,355 | 可观测性，`message_traces` 是刚需 |
| profile/ | 14 | 3,905 | 后台异步，不应在主链 |
| persistence/ | 4 | 2,482 | 必需但可简化 |
| actions/ | 6 | 1,940 | ActionPlan DAG，对"发一条短句"过重 |
| cognition/ | 7 | 1,758 | AMBIENT 单 worker，必需 |
| tasks/ | 3 | 1,211 | 与 ActionPlan 绑定 |
| persona/ | 7 | 898 | 部分必需 |
| society/ | 6 | 731 | 与 profile 重叠 |
| memory/ | 5 | 585 | OBSERVE 路径不触发 |
| delivery/ | 3 | 546 | SOCIAL_RUNTIME 需要 |
| media/ | 3 | 467 | 决策链未接入 |

数据库表约 58 张（`persistence/schema.py:21-42` 强制 56 张 + `reply_plans` + `shadow_capture_evidence`）。

### 4.2 关于禁用功能：已撤回

> **2026-09-01 修订。** 本节原先建议用 `knowledge_enabled=false`、`profile_enabled=false` 等开关关闭外围能力以简化主链。该建议**与最新需求冲突，已撤回，不要执行。**
>
> 依据：`docs/superpowers/plans/` 中 2026-08-28 的五份文档（game-version-public-facts、game-knowledge-grounding-roadmap、game-knowledge-ambient-expansion、game-knowledge-local-cognition、game-knowledge-grounded-replies）以及 `.superpowers/sdd/2026-08-28-game-version-public-facts/progress.md`（Task 1–6 已完成）表明，**游戏知识是当前进行中的需求重心**。同理 `profile/` 由 08-26 与 08-27 的 spec 支持，`society/` 由 08-25 的好感度系统 spec 支持。这些都是有效需求。
>
> 唯一至今仍被明确排除且未被恢复的能力是**冷启动主动开场**（`2026-08-24-participation-policy-and-dialogue-lease-design.md` 非目标列表）。
>
> 正确的做法不是削减功能，而是调整承载这些功能的结构——见 `analysis/structural-refactor-plan-20260901.md`。该文档只主张三类无争议的清理：十个空目录与 pyc 残留、三处确认的死代码（`AstrBotStructuredWorker`、`AstrBotCapabilityAdapter`、`vision_provider`）、九个单实现 Protocol 的收敛。

### 4.3 建议合并

| 合并 | 理由 |
|---|---|
| `cognition/blackboard` + `intentions` + `participation` + `governor` → 单一参与决策器 | 四层串行，但 AMBIENT 最终只看一个 `participation_assessment`（`manager.py:1167-1201`） |
| `social_scenes` + `stances` + `social_moves` + `expression` + `replying` → 单一表达规划器 | 五层表达栈全在 ACT 之后才有意义，OBSERVE 时完全空转（`astrbot_bridge.py:1904-1978`） |
| `persona/` + `profile/` + `society/`（5,534 行） | 三套并存的"成员关系"模型 |
| 9 个单实现 Protocol | `CognitiveWorker`、`KnowledgeResolverPort`、`DeliveryTransport`、`TextModelPort`、`StructuredModelPort`、`SocialSceneModelPort`、`OfficialSourceProbePort`、`DiscoverySearchPort`、`ReleaseStateReader` 各只有一个实现 |
| `control/projections` + `config_versions` + `stream` | 与 `message_traces` 功能重叠 |

### 4.4 必须保留

| 模块 | 理由 |
|---|---|
| `attention.py` 三通道 | 直接对应 report 的 DIRECT_FAST / CONTINUATION / AMBIENT |
| `world.py` 对话租约 | 对应目标 bot 48.4% 的连续对话 |
| `participation.py` | 策略优先，FAST/CONTINUATION 确定性候选 |
| `governor.py` | 硬门控必需。`force_observe` 需要修的是其**降级判定粒度**，不是把 SHADOW 门移走——后者已被 08-24 spec 否决 |
| `manager.py` + `scene_actor.py` | 编排核心，需修竞态 |
| `contracts.py` | canonical event 边界 |
| `control/message_traces.py` | 唯一可观测决策链 |
| `cognition/ambient_worker.py` | AMBIENT 唯一 LLM 评估点 |
| `delivery/outbox` | 正式发送需要 |

### 4.5 瘦身后的目标形态

```text
OneBot 事件
  → CanonicalEvent（保留 contracts.py）
  → GroupScene 更新（world.py + 对话租约）
  → 参与决策器（规则优先，AMBIENT 才调 1 次联合认知模型）
  → 表达规划器（层数需先度量再决定是否压缩）
  → DeliveryGate（SHADOW 门维持现位置）
  → Outbox → OneBot 发送
  → 后台异步车道（profile / knowledge / society / memory，绝不阻塞主链）
```

主链约 6,000–8,000 行，外围能力保持完整但严格异步、不阻塞发言决策。注意这里的"合并"指的是**实现层收敛**，不是删功能。

---

## 第五部分：建议执行顺序

| 阶段 | 内容 | 判定标准 |
|---|---|---|
| **1. 修自锁闭环** | 见 `analysis/effectiveness-fix-plan-20260831.md` 的四个必修项 | `cognitive_observations` / `candidate_intentions` / `attention_frames` 非空；明确互动非零候选率 ≥99%；DIRECT_FAST 决策 P50 ≤1s、P90 ≤3s；AMBIENT P50 ≤12s、P90 ≤15s（指标出自 08-24 participation-policy spec） |
| **2. 结构清理** | 见 `analysis/structural-refactor-plan-20260901.md` 的 S1、S2 | 十个空目录与 pyc 残留清除；三处死代码删除 |
| **3. 收敛适配层边界** | 同上文档 S3：修五处 adapters 反向依赖 | 存在一个不依赖 AstrBot 的离线 replay 入口 |
| **4. 拆编排层** | 同上文档 S4：抽出关系画像门面与命令处理 | `SocialRuntimeManager` public 方法降到 21 个以内 |
| **5. 再评估框架** | 此时迁移面已从 4,400 行降到约 1,000 行，且有了能说话的基线可对比 | 若仍需要 poke/notice 或独立部署控制面，再迁 |

第 3 阶段完成后，换框架会从"一次重构"变成"换一个注入"。这也是唯一能让 2.1 的收益不必付 2.2 代价的路径。
