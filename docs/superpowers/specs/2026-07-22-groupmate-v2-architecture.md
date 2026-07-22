# Groupmate v2 架构：拟人群聊伙伴

日期：2026-07-22  
状态：**已收敛为精简核心（lean cut）**  
前置：[`2026-07-17-groupmate-agent-design.md`](./2026-07-17-groupmate-agent-design.md)  

> **2026-07-22 精简决议**：只保留直接有利于拟人化的路径——触发路由、Actor/防抖/续聊、决策门控、人格+Guard、拟人延迟/分段、关系称呼、基础记忆检索、管理命令。  
> **已舍弃**：表达学习、反思合并、疲劳/在场状态机、表情包、影子评测/WebUI/evaluation。下文若仍出现这些模块，视为历史草案，不以代码为准。

目标（现行）：可靠直接唤醒 + 适当插话 + 短自然回复；不追求“越聊越熟”的伪学习层。

## 1. 产品目标（不变）

1. 直接唤醒时可可靠聊天。
2. 能根据群话题**适当**加入，不刷屏。
3. 表现得像群聊伙伴，而不是指令机器人或客服。

v2 相对 v1 的差异：不只「能插话」，还要「越聊越熟、有节奏、像群友」。

## 2. 非目标

- 不做成独立聊天平台；能力以 AstrBot 插件形式交付。
- 不替换 AstrBot 的指令生态；指令始终旁路优先。
- 首版不追求图谱记忆、完整情绪二维模型、随机错别字；这些进 P2。
- 不把爱弥斯关系硬编码进领域核心；关系进入可配置 Profile。

## 3. 设计原则

1. **LLM 仍不拥有运行循环** —— 调度、额度、幂等、取消、持久化由代码控制。
2. **保留六边形边界** —— `groupmate/` 不 import AstrBot；适配仍在 `astrbot_adapter.py`。
3. **状态显式、失败沉默** —— fail-closed；影子模式继续可用。
4. **渐进迁移** —— v1 路径可并存一个版本周期；P0 落地后切默认。
5. **人格硬规则 > 学习风格** —— 表达学习只改表层，不改身份与边界。

## 4. 目标运行时总览

```text
AstrBot Star (main)
  └─ AstrBotBridge
       ├─ Platform / History / LLM / Vision adapters
       └─ PresenceRuntime (每群一个 PresenceActor)
            ├─ TriggerRouter          # 确定性分流（保留）
            ├─ TopicWindow            # 工作记忆窗口（强化切分）
            ├─ EngagementController   # 新：在场状态 + 时机 + 疲劳
            ├─ CognitivePipeline      # 升级自 CognitiveWorkflow
            │    OBSERVE → SEGMENT → RECALL → THINK → GATE
            │    → [VISION/TOOLS] → PLAN → GENERATE → GUARD
            │    → SCHEDULE → SEND → LEARN
            ├─ MemorySubsystem        # 写回闭环（新）
            ├─ ExpressionStore        # P1：黑话/风格
            ├─ DeliveryScheduler      # 新：延迟/多段/取消
            └─ ShadowCollector        # 保留并扩展标签维度
```

### 4.1 与 v1 对照

| v1 | v2 |
|---|---|
| `GroupActor` 邮箱 + 防抖 | `PresenceActor`：在场循环 + 候选取消 + 状态机 |
| `CognitiveWorkflow` 压缩管线 | `CognitivePipeline` 显式 11 阶段（对齐原规格） |
| `Decision` = respond/ignore | `Intent` + `EngagementDecision`（动作更丰富） |
| Memory 只读检索 | Memory 读 + LEARN 写回 + Profile 权威层 |
| `send_text` 单条 | `DeliveryPlan`：延迟、分段、可选引用 |
| 关系硬编码 QQ | `RelationshipConfig` / profiles 表 |

## 5. 核心模块

### 5.1 PresenceActor（每群串行）

职责：

- 串行处理入站消息与内部事件（评估、发送、学习、取消）。
- 维护 `PresenceState`：`OBSERVING | ENGAGED | FOCUSED | FATIGUED | PAUSED`。
- 管理 `CandidateSlot`：至多一个待发送主动候选；直接唤醒可抢占。
- 强制 `topic_max_seconds`、`candidate_ttl_seconds`；超时丢弃。

事件类型：

```text
MessageIngest | DebounceFire | DirectWake | CancelCandidate | DeliverDue | LearnDue | Tick
```

状态迁移（简化）：

```text
OBSERVING --(direct wake)--> ENGAGED
OBSERVING --(candidate pass gate)--> ENGAGED
ENGAGED   --(same sender within continuation)--> FOCUSED
ENGAGED/FOCUSED --(rate limit / ignore streak)--> FATIGUED
FATIGUED --(cooldown elapsed)--> OBSERVING
任意 --(admin pause)--> PAUSED
```

### 5.2 EngagementController（时机层）

在调用决策模型之前，用确定性规则过滤：

- 指令 / bot 自身 / 空内容 → 旁路
- 冷却与小时额度
- 话题过期、重复内容、低信息噪声启发
- 疲劳态降低或禁止自发
- 直接唤醒：跳过自发额度，进入高优先级通道

输出：`EngagementVerdict = BYPASS | DEFER | EVALUATE | FORCE_REPLY`

### 5.3 CognitivePipeline（认知管线）

显式阶段（每次写入 `decision_id` + reason_code）：

| 阶段 | 职责 | v1 现状 |
|---|---|---|
| OBSERVE | 校验输入、触发、策略 | 有 |
| SEGMENT | 话题归属 / 新开 topic | 弱（仅窗口） |
| RECALL | 检索 profile + episodic + style | 弱检索 |
| THINK | 生成一句话 contribution 意图 | 缺（直接 GATE） |
| GATE | 结构化决策；失败则沉默 | 有（偏薄） |
| VISION/TOOLS | 按需看图；P1 可扩展工具 | 仅 vision |
| PLAN | 目标消息、语气、分段数、是否引用 | 有（偏薄） |
| GENERATE | 人格 + 有限上下文生成 | 有 |
| GUARD | 确定性校验；可 repair 一次 | 有（repair≈空） |
| SCHEDULE | 拟人延迟；发送前再确认未过期 | 缺 |
| SEND | Outbox 幂等投递 | 有 |
| LEARN | 抽取并写入允许的记忆 | 缺 |

直接唤醒路径：`FORCE_REPLY` 可跳过 THINK 模型调用，但仍走 RECALL → PLAN → GENERATE → GUARD → SCHEDULE → SEND → LEARN。

### 5.4 决策模型（加厚）

`EngagementDecision`（替代仅有 respond/ignore）：

```text
action: silence | react | reply | ask | emoji_only
confidence: 0..1
contribution: string          # 一句话意图
target_message_id: optional
needs_vision: bool
urgency: low | normal | high
reply_segments: 1..3          # 建议分段
should_quote: bool
reason_code: enum
```

规则：

- 结构无效 / 模型失败 → silence
- 自发路径须 `confidence >= threshold`
- `emoji_only` 仅在表情系统启用时允许（P1）

### 5.5 MemorySubsystem（一级子系统）

#### 存储分层

| 层 | 存储 | 写入方 | 权威 |
|---|---|---|---|
| Event log | `messages` | ingest | n/a |
| Working memory | `TopicWindow` | Actor | n/a |
| Profile | `profiles` | 配置 + 保守 LEARN | 人工配置最高 |
| Episodic | `memories(kind=episodic)` | LEARN | 模型，带过期 |
| Reflection | `memories(kind=reflection)` | 后台合并（P1） | 低于 profile |
| Style | `expressions`（P1 新表） | 表达学习 | 低于人格硬规则 |

#### LEARN 写回策略（P0 必须）

发送成功或直接唤醒对话结束后：

1. 用小模型或规则从「本轮话题 + 回复」抽出 0～3 条候选记忆。
2. 过滤：指令输出、系统词、内部 ID、低置信、与受保护 profile 冲突。
3. `add_memory` / `upsert_profile_field`（仅非保护字段）。
4. 记录 `learn_trace` 便于影子审计。

受保护字段（不可被 LEARN 覆盖）：关系标签、固定称呼、黑名单偏好（来自配置）。

#### 检索（P0 可先增强词面，P1 加嵌入）

评分：`overlap * recency * importance * confidence * authority`  
预算：最多 N 条，总字符上限；禁止整库塞入 prompt。

### 5.6 DeliveryScheduler（拟人发送）

`DeliveryPlan`：

```text
decision_id
segments: [text, ...]      # 每段仍受 max_chars 约束
delay_ms: int              # 首段延迟
gap_ms: int                # 段间间隔
quote_message_id: optional
expires_at: int
```

延迟启发式（可配置上限）：

- HIGH / 直接唤醒：0～800ms
- NORMAL：按字数 300～2500ms
- LOW：800～4000ms
- 硬上限：直接唤醒 ≤1s，自发 ≤5s

发送前校验：话题未过期、候选未取消、额度仍有效、Outbox 未重复。

`PlatformPort` 扩展：

```text
send_text(...)
send_segments(group_id, segments, decision_id, quote_message_id=None)
```

### 5.7 Persona / Relationship

- System：AstrBot persona_id → override → bundled md（不变）
- User context：最近消息 XML + memories + **profile 关系标签**（来自 DB/配置，不再写死 QQ）
- 默认爱弥斯关系迁移为 `relationships.json` 或配置 schema 列表：`sender_id → relationship, address`

### 5.8 ExpressionStore（P1）

- 从表层对话沉淀短语、称呼习惯、群梗
- 生成时作为 `<style_hints>` 注入，不得覆盖 Guard / 人格硬规则
- 提供清理与禁用开关

### 5.9 Shadow / Evaluation（保留并扩展）

新增可标注维度（可选字段，兼容旧标签）：

- timing: early | ok | late
- style: assistant | peer | persona_ok
- memory: missed | wrong | ok

导出与 WebUI 审阅继续走现有控制中心。

## 6. 端口变更

保留并扩展 `ports.py`：

| Port | 变更 |
|---|---|
| `DecisionModelPort` | 输入增加 recalled memories + presence state；输出 `EngagementDecision` |
| `GenerationModelPort` | `generate(plan, topic, memories, style_hints)`；**repair 必须真修复** |
| `VisionPort` | 不变 |
| `PlatformPort` | 增加 `send_segments` |
| `MemoryRepository` | 增加 `upsert_profile`、`list_protected_fields`、LEARN 所需查询 |
| `PersonaProvider` | `build_user_context` 接收 profiles |
| `LearnModelPort`（新） | `extract_memories(topic, reply) -> list[MemoryItem]` |
| `Clock` / `TraceSink` | 不变 |
| `ExpressionRepository`（P1） | style hints CRUD |

## 7. 包结构（目标）

```text
groupmate/
  main 入口仍在仓库根 main.py
  ports.py
  models.py                 # 扩展 Intent/Delivery/Presence
  config.py
  triggers.py               # 保留
  topics.py                 # 强化 SEGMENT
  presence/
    actor.py                # PresenceActor
    engagement.py           # EngagementController
    states.py
  pipeline/
    cognitive.py            # CognitivePipeline
    stages.py               # 可选：阶段函数拆分
  memory/
    store.py                # 现 memory.py 迁入
    learn.py                # LEARN
    retrieve.py
  delivery/
    scheduler.py
    guardrails.py           # 从根迁入或 re-export
  persona/
    provider.py
    relationships.py        # 配置化关系
  expression/               # P1
    store.py
    learner.py
  evaluation/               # 保留
  astrbot_adapter.py        # 变薄：只适配，少编排
  bridge.py                 # 从 adapter 拆出编排（可选重构）
  web_api.py
  shadow_admin.py
  rate_limit.py
```

迁移期允许旧路径 `from groupmate.memory import ...` 做兼容 re-export。

## 8. 消息生命周期（v2）

```text
群消息
  → Bridge 过滤 paused / enabled_groups
  → PresenceActor.ingest
       → TopicWindow.append + save_message
       → TriggerRouter.classify (+ continuation)
       → EngagementController.verdict
            BYPASS → 可选 shadow observe → end
            FORCE_REPLY → 取消自发候选 → Pipeline(direct)
            DEFER → 重置 debounce（受 topic_max 约束）
            EVALUATE → debounce 后 Pipeline(candidate)
  → Pipeline
       → … GUARD → DeliveryScheduler.enqueue
       → delay → 发送前复核 → send_segments
       → LEARN 异步/串行写回
       → 更新 PresenceState / continuation / rate_limit
```

影子模式：Pipeline 在 GATE 后停止（或跑到 PLAN 但不 GENERATE/SEND），LEARN 默认关闭。

## 9. 配置增量（`_conf_schema.json`）

P0 新增/暴露：

| 键 | 默认 | 说明 |
|---|---|---|
| `topic_max_seconds` | 12 | 话题硬切 |
| `humanize_delay_enabled` | true | 拟人延迟 |
| `max_reply_segments` | 2 | 最多分段 |
| `learn_enabled` | true | 发送后写记忆 |
| `learn_provider` | "" | 空则复用 decision_provider |
| `relationships` | [] | `{id, relationship, address}` |
| `fatigue_after_ignores` | 8 | 连续忽略后进入疲劳 |
| `fatigue_seconds` | 300 | 疲劳时长 |

P1：`expression_learning_enabled`、`emoji_enabled` 等。

## 10. 分阶段落地

### Phase 0 — 架构落盘（本文档）✅

批准后进入实现。

### Phase 1 — P0 体感跃迁（优先实现）

1. `PresenceActor` + 话题硬切 + 候选取消  
2. Pipeline 补齐 `SCHEDULE` + `DeliveryScheduler`（延迟/多段）  
3. `LEARN` 写回 + Profile 配置化关系（去掉硬编码 QQ）  
4. `EngagementDecision` 最小加厚（silence/reply + contribution + segments）  
5. `repair` 真正调用生成模型重写  
6. 单测覆盖：actor 取消、delay 过期丢弃、learn 过滤、关系配置

验收：直接唤醒仍可靠；自发不刷屏；重启后仍能召回近期情景记忆；发送呈 1～2 段短消息且有轻微延迟。

### Phase 2 — P1 拉开差距（进行中 / 部分完成）

1. ExpressionStore + 风格注入 — **已完成**  
2. 表情包端口 — **已完成（见 P2）**  
3. FOCUSED / FATIGUED 状态完善 — **已完成（疲劳抑制自发；直接唤醒解除）**  
4. Reflection 合并任务 — **已完成（轻量 overlap 合并）**  
5. 影子标注扩展 timing/style/memory — **已完成**  

### Phase 3 — P2 拟人能力深化（部分完成）

1. 嵌入检索 — **已用字三元组 + 主体/反思加权增强，无外部向量依赖**  
2. 多轮内部推理 / 工具 — 延期  
3. 表情包端口 — **已完成（本地 stickers + 可选发送）**  
4. 更细情绪与积极性自适应 — 延期（疲劳态已覆盖一部分）  

## 11. 风险与约束

- **成本**：LEARN + 更厚 GATE 增加调用；默认 LEARN 用小模型，失败则跳过写回。  
- **隐私**：记忆默认群内命名空间；导出继续脱敏。  
- **双回复**：保持 `handle_native_wake` 与指令旁路语义不变。  
- **适配层膨胀**：编排迁出 `bridge.py` / `presence/`，避免 `astrbot_adapter.py` 继续变神类。

## 12. 成功标准

相对「拟人群聊伙伴」目标的可操作定义（插件语境）：

| 能力 | 达标信号 |
|---|---|
| 直接唤醒 | @/别名/续聊稳定单次回复 |
| 适当加入 | 影子标注「必须沉默」误回率可控；有额度与疲劳 |
| 拟人节奏 | 非秒回；可多段；过期不补发 |
| 越聊越熟 | 明确事件/偏好可在后续对话被引用 |
| 像伙伴 | Guard 通过率高；客服腔/旁白接近为 0 |

## 13. 决策前路径保留决议（2026-07-22）

对照「GATE / 决策模型调用」之前的现有链路，结论如下。

| 组件 | 决议 | 理由 |
|---|---|---|
| Bridge 过滤（paused / enabled_groups） | **保留** | 与 AstrBot 生命周期绑定，无需重做 |
| History preload | **保留** | 冷启动上下文必需 |
| `TriggerRouter` | **保留** | 确定性分流已测通，直接唤醒可靠 |
| `GroupActor` 串行邮箱 + debounce generation | **保留** | 防并发/防抖代际是正确抽象 |
| 续聊 `CONTINUATION` | **保留** | 沉浸式跟聊已满足产品需求 |
| IGNORE / COMMAND 旁路 | **保留** | 指令生态兼容硬约束 |
| 直接唤醒立即评估 vs 候选防抖 | **保留** | 产品行为正确 |
| `SlidingWindowRateLimiter` | **保留** | 自发额度控制有效 |
| Workflow `OBSERVE` 前置校验 | **保留** | fail-closed 必需 |
| Workflow `RECALL` | **保留** | 检索保留；靠 LEARN 写回喂养 |
| 独立 `EngagementController` 替换触发层 | **不做（P0）** | 与 TriggerRouter 重复；规则已分散且够用 |
| 独立 THINK LLM 调用 | **延期 P1** | 决策模型已返回 `contribution`；避免双倍成本 |
| 完整 Presence 状态机 | **延期** | P0 只补话题硬切与候选取消（已有 generation） |

**P0 在决策前仅增量：**

1. 强制 `topic_max_seconds`：防抖 delay 钳制到话题剩余收集时间。  
2. 其余决策前行为保持兼容，不换入口。

**P0 重点在决策后：** `SCHEDULE`（延迟/多段）→ `SEND` → `LEARN`；关系配置化；`repair` 真修复。

## 14. 批准后立即开工项

1. 写入本节保留决议（已完成）  
2. 实现 `DeliveryScheduler` 并接入现有 workflow  
3. 实现 LEARN 写回  
4. 关系配置迁移出硬编码默认进 settings  
5. 补齐 `topic_max_seconds` 强制逻辑  

---

本文档是 Groupmate v2 的架构真源。实现按第 13 节决议执行。
