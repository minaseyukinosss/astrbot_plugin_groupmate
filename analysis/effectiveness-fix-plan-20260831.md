# Groupmate 群聊效果修复任务书

> 交付对象：接手本任务的 AI 助手或工程师
> 生成日期：2026-08-31（2026-09-01 按最新需求修订）
> 代码基线：`astrbot_plugin_groupmate` v1.0.0-rc.33
> 需求基准：以 `docs/superpowers/specs/` 中**日期最新**的设计文档为准
> 所有行号均为本文生成时的真实行号，修改前请先核对上下文

---

## 0. 阅读顺序与硬性约束

本文档自包含。第一次接触本项目请按顺序读：第 1 节（项目是什么）→ 第 2 节（需求基准与验收目标）→ 第 3 节（故障因果链）→ 第 4 节（四个必修项）→ 第 5 节（执行顺序）。

**不得违反的约束：**

1. **不要重构架构、不要换框架、不要引入新依赖。** 本任务只修复导致 bot 不说话的具体缺陷。架构问题另有文档，见 `analysis/structural-refactor-plan-20260901.md`。
2. **不要移动或削弱 SHADOW 的零发送门。** 最新 spec 已确认当前位置正确，见第 2.3 节。
3. **测试从简。** 只为"新增的行为分支"写针对性测试，不做全覆盖，不为内部类补重复单元测试，不运行与本轮无关的 UI 或灾难恢复套件。具体要求见第 5.2 节。这是项目的明确要求：过去在测试上花费过多且收益不佳。
4. **保持中文注释风格**，与现有代码一致（参考 `participation.py:150`、`astrbot_bridge.py:1824-1825`）。
5. **改动有顺序依赖**，见第 5 节，不要跳步。

---

## 1. 项目是什么

`astrbot_plugin_groupmate` 是一个 QQ 群聊 AI 机器人插件，人格名"爱弥斯"。

- **协议层**：NapCat（NTQQ 无头客户端，对上暴露 OneBot 11）
- **宿主框架**：AstrBot（Python 插件平台，经 `aiocqhttp` 接入 OneBot 11）
- **核心**：`groupmate/social_runtime/`，108 个文件约 41,000 行，**零 astrbot import**，纯 Python
- **存储**：SQLite，约 58 张表，路径 `data/plugin_data/astrbot_plugin_groupmate/groupmate-social-runtime-v2.db`
- **运行模式**：`OFF` / `SHADOW`（完整决策但零发送）/ `SOCIAL_RUNTIME`（正式发言）

**产品目标**：像真实群友一样参与群聊——接住别人对它说的话、维持多轮连续对话、偶尔加入正在聊的话题、几乎不凭空开场、用短句和图片说话。

**主链路**：

```
NapCat 事件 → AstrBot（main.py 订阅 GROUP_MESSAGE）
  → astrbot_events.py 翻译为 SocialEventEnvelope
  → SocialRuntimeManager.ingest（manager.py:552）写 inbox
  → GroupSceneActor 消费（scene_actor.py，每群一个单写者）
  → GroupWorldProjector 更新群世界（world.py：话题、参与者、对话租约）
  → AttentionScheduler 分流（attention.py）
       FAST：被 @ / 被回复 / 被称呼 / poke → 立即出帧
       CONTINUATION：命中对话租约 → 立即出帧
       AMBIENT：普通消息 → 2–5 秒窗口后出帧
  → SocialRuntimeManager._evaluate_cycle（manager.py:1089）
       → CognitionService.evaluate（AMBIENT 才调模型）
       → ParticipationPolicy.propose（participation.py）
       → SocialGovernor.decide（governor.py，输出 ACT/DEFER/OBSERVE/SILENCE）
  → GroupSceneActor.accept_result（scene_actor.py:401-488，落库）
  → astrbot_bridge._handle_evaluations_locked（bridge:1740）
  → ReplyPlanner（replying.py，仅 ACT）
  → Outbox → Dispatcher → OneBot send_group_msg
```

**两个关键概念：**

`scene_version`——每群单调递增，每处理一条事件 +1，用于乐观并发控制。它是本次故障的核心。

`AttentionFrame`（冻结帧）——认知与决策在冻结的世界快照上进行，帧内记录 `scene_version`、`focus_event_ids`、`focus_topic_ids`、`candidate_audiences`、`requested_workers`、`deadline`。

---

## 2. 需求基准与验收目标

### 2.1 需求以最新 spec 为准

`docs/superpowers/specs/` 下有 22 份设计文档，文件名带日期。**后出的文档覆盖先出的。** 与本任务最相关的两份是：

- `2026-08-24-participation-policy-and-dialogue-lease-design.md`（参与策略与对话租约）
- `2026-08-24-ambient-cognition-latency-root-fix-design.md`（AMBIENT 认知延迟根因修复）

**注意：`2026-08-21-groupmate-chat-mainline-design.md` 中关于 Worker 数量、SHADOW 门位置、延迟目标的内容已被上述两份覆盖，不要按它执行。**

### 2.2 已被最新 spec 确定、不要再改的设计

以下几点常被误判为缺陷，实际是符合最新需求的正确实现：

| 项 | 现状 | 依据 |
|---|---|---|
| 只注册一个 `ambient_social_assessor` 联合认知 worker | 正确 | latency-root-fix 明确改为"单次联合认知"，撤销了原本的双 worker 方案 |
| AMBIENT 决策预算 8 秒 | 正确 | latency-root-fix："闲聊判断必须在既有 8 秒有效期内完成"；`cognition_timeout_seconds` 默认 8 秒、范围 3–15 |
| AMBIENT 窗口内容上限（12 事件 / 4 话题 / 8 参与者） | 已实现 | latency-root-fix 的有界窗口要求 |
| `CognitiveContext` 不含 `shadow_only`，用 `no_side_effects` + `evidence_required` | 已修复（`manager.py:1164`） | participation-policy 第 6 条 |
| `DIRECT_FAST` 不等待结构化 worker | 已实现（`participation.py:47`） | participation-policy 规则 2 |

### 2.3 SHADOW 门的位置：不要动

`2026-08-24-participation-policy-and-dialogue-lease-design.md` 原文：

> 线上数据库中 408 条 Governor 结果全部为 `forced_observe`；候选意图均为空或只有 `observe_without_action`。与此同时，`AstrBotSocialRuntimeBridge` 已经在 SHADOW 下完成预览后跳过执行和发送。**因此 SHADOW 的零发送位置是正确的，本轮不移动、不削弱该门。**

`forced_observe` 的成因是认知降级与 AMBIENT 参与评估未通过，**与 SHADOW 无关**。任何"把 SHADOW 拦截下移到 DeliveryGate"的建议都已过时，不要执行。

SHADOW 必须继续保证：零 Outbox ready part、零 OneBot 调用、零外部副作用；但**允许**生成 ReplyPlan 与 ReplyPreview 以记录真实 `would_reply`。

### 2.4 验收目标（来自 participation-policy spec，非目标 bot 原始数据）

| 指标 | 目标 |
|---|---|
| DIRECT_FAST 参与决策 P50 | ≤ 1 秒 |
| DIRECT_FAST 参与决策 P90 | ≤ 3 秒 |
| AMBIENT 参与决策 P50 | ≤ 12 秒 |
| AMBIENT 参与决策 P90 | ≤ 15 秒 |
| 明确互动非零候选率 | ≥ 99%，剩余失败必须有硬门或明确诊断码 |
| AMBIENT 模型失败时可发送候选数 | 0 |
| 回复生成时延 | 单独统计，不与参与决策混算 |

spec 明确指出："这些目标先在本群 SHADOW 校准，不把目标群聊 Bot 的 4 秒 / P90 15 秒机械复制为长期 SLA。"

作为背景参考（**不作为验收线**），目标 bot 的行为分布是：连续对话 48.4%、被点名或被回复 27.1%、自主定向回复 12.4%、环境加入 11.5%、冷启动 0.1%；输出中位数 14 字、P90 53 字；72.1% 含媒体。来源 `analysis/target_bot_20260824/report.md`。

### 2.5 当前实测（故障状态）

| 指标 | 目标 | 当前 |
|---|---|---|
| 非 OBSERVE 决策 | 明确互动 ≥99% 有候选 | **0**（780/780 全 OBSERVE） |
| `cognitive_observations` 表 | 非空 | **空** |
| `candidate_intentions` 表 | 非空 | **空** |
| `attention_frames` 表 | 非空 | **空** |
| `action_plans` / `reply_plans` 表 | 非空 | **空** |
| 链路时延 P50 / P90 | 见 2.4 | 约 23s / 36s |

**一句话：bot 从不说话，而且慢。**

---

## 3. 故障因果链

这是一个自锁闭环，完整理解后再动手。

```
【延迟累积】
AMBIENT 窗口 2–5s（attention.py:337-343，符合设计）
  + 认知预算 8s（符合设计）
  + 1 次 AMBIENT 认知 LLM（符合设计）
  + bridge 对每条评估无条件再调 1 次场景解析 LLM（astrbot_bridge.py:1884，纯浪费）
  ↓
链路 P50 ≈ 23s
  ↓
【决策丢弃】
认知在 actor 邮箱之外执行（scene_actor.py:175 注释自陈）
这 23 秒里群里每来一条新消息，state.scene_version 就 +1
accept_result 要求「结果的 scene_version == 当前 state 的 scene_version」（scene_actor.py:429）
活跃群里该等式必然不成立
  ↓
accepted=False → evaluation=None（scene_actor.py:436-440）
  → resolve_scene_evaluation 跳过 _insert_shadow_evaluation（event_store.py:537-539）
  → attention_frames / cognitive_observations / candidate_intentions / governor_results 四表不写
  ↓
【降级锁死】
worker 视为未完成 → CognitionService 标记 degraded=True
（cognition/service.py:211, 219, 238, 249 —— 五个独立来源，任一触发即降级）
  ↓
AMBIENT 的 allow_degraded=False（participation.py:93，符合设计：环境介入本就该保守）
  ↓
manager.py:1192-1198 设 force_observe=True
  ↓
governor.py:65-73 返回 OBSERVE + "forced_observe"
  ↓
【表达链永不触及】
ActionPlanner 只接受 ACT（actions/planner.py:31-32）
ReplyPlanner 要求 accepted=True 且 ACT（replying.py:374-378）
  → action_plans / reply_plans 必然为空
```

**闭环自我强化**：延迟 → 丢弃 → 降级 → 禁言，而延迟的一部分来自禁言路径上的浪费调用。

**一条决定性证据**：participation-policy spec 第 5 节要求把认知观察与候选意图原子持久化到 `SceneWorkResult`。核对 `manager.py:1226-1241`，该改造**已经完成**——`SceneWorkResult` 确实携带了 `cognitive_observations`、`candidates`、`participation_lane`、`participation_diagnostics`。改造做完而表仍为空，唯一解释就是 stale 判定挡在了写入之前。

**这六个环节全在纯 Python 内，与 AstrBot / NoneBot / NapCat 无关。**

---

## 4. 四个必修项

按性价比排序。每节结构：现象 → 位置与当前代码 → 为什么不合理 → 修复方案 → 验收。

---

### 4.1 沉默路径上的多余 LLM 调用

#### 现象
已决定不发言的消息仍付一次场景解析模型往返，直接推高延迟，并加剧 4.2 的版本漂移。

#### 位置与当前代码

`groupmate/adapters/astrbot_bridge.py:1756` 起的循环，到 `1884`：

```python
            for evaluation in ordered:
                ...
                else:
                    interpretation = await self._scene_interpreter.interpret(
                        scene_context
                    )
```

从 1756 到 1884 之间只有三个 `continue` 出口：`group_id` 缺失或重复（1764-1770）、`social_eligible is False`（1772-1782）、`_scene_interpreter is None`（1794-1800）。**没有任何一处检查 `governor_result.outcome`。**

对照同文件 `1399-1400`，知识富化路径**做了**这个检查：

```python
        result = getattr(evaluation, "governor_result", None)
        if str(getattr(result, "outcome", "")).upper() != "ACT":
```

说明这是遗漏，不是设计取舍。

#### 为什么不合理

Governor 输出 OBSERVE 或 SILENCE 时，后续场景解析、立场决策、社交动作规划的产物只进入 trace 摘要，不影响任何发送行为。为此付一次模型往返，与 latency-root-fix "不通过延长等待时间掩盖故障、消除重复模型调用"的精神直接冲突。

#### 修复方案

在 1756 循环体内、构建 `scene_context`（1839）之前插入短路，建议紧跟 1782 的 `continue` 之后：

```python
                governor_result = getattr(evaluation, "governor_result", None)
                outcome = str(getattr(governor_result, "outcome", "")).upper()
                if outcome not in {"ACT", "DEFER"}:
                    # 已决定不发言：场景解析、立场与社交动作都不影响投递，
                    # 不再为沉默路径支付一次模型往返。
                    self._record_trace(
                        self.trace_repository.record_evaluation,
                        evaluation,
                        int(self.clock()),
                    )
                    continue
```

沉默路径改为只记录结构化摘要（参与通道、候选数、治理原因码、认知诊断码），这些字段本就在 `evaluation` 对象上，零额外成本。

#### 验收

一个测试：`outcome="OBSERVE"` 时 `_scene_interpreter.interpret` 未被调用且 `record_evaluation` 被调用一次；`outcome="ACT"` 时仍调用。

---

### 4.2 乐观并发控制比较了错误的对象

#### 现象
五张决策表永久为空。控制面能显示"已决策"，SQL 表查不到证据。

#### 位置与当前代码

`groupmate/social_runtime/scene_actor.py:426-440`：

```python
                    accepted = bool(
                        request is not None
                        and command.result.group_id == self.group_id
                        and command.result.scene_version == state.scene_version
                        and self._compatible_result(
                            command.result,
                            request,
                            command.current_persona,
                        )
                    )
                    evaluation = (
                        self._evaluation_payload(command.result, request)
                        if accepted and request is not None
                        else None
                    )
```

`command.result.scene_version` 来自 `frame.scene_version`（冻结时刻，见 `manager.py:1229`），`state.scene_version` 是 actor 当前值。

#### 为什么不合理

`scene_version` 的合理用途是回答"这个决策依据的世界状态，现在是否还足以支撑发言"。代码把它用成了"世界状态是否一字未变"。

认知耗时 8–23 秒，活跃群里必然有新消息推进版本号。于是**决策越慢越会被丢弃，丢弃又导致降级和禁言**。它在安静的群里能工作，在活跃的群里必然失效——而活跃的群正是产品价值所在。

严格相等还把"无关的新消息"和"使决策失效的新消息"等同对待：别人聊了句不相关的话，不该让 bot 放弃回应三秒前 @ 它的人。

participation-policy spec 第 161 行的原则是"过期或 stale 结果不得写成已接受认知"——原则正确，但"stale"的判定标准被实现成了版本严格相等。

#### 修复方案

把"版本严格相等"替换为"语义新鲜度检查"。允许 `scene_version` 前进，下列任一情况判定失效：

1. 版本前进超过阈值（建议 `MAX_SCENE_VERSION_DRIFT = 12`）
2. 帧的 `focus_event_ids` 已全部滑出当前最近上下文
3. `config_version` 变化（人格已重新发布，必须失效）

建议在 `scene_actor.py` 新增方法，与现有 `_compatible_result` 并列：

```python
    def _result_still_actionable(
        self,
        result: SceneWorkResult,
        request: SceneWorkRequest,
        state: GroupWorldState,
    ) -> tuple[bool, str]:
        """判断冻结帧上的决策是否仍可安全应用。

        允许 scene_version 前进：认知在 actor 邮箱外执行，活跃群里版本号
        必然推进。需要拦截的是「使该决策失效的变化」，而非「任何变化」。
        返回 (是否可用, 失效原因码)。
        """
        drift = int(state.scene_version) - int(result.scene_version)
        if drift < 0:
            return False, "scene_version_regressed"
        if drift > MAX_SCENE_VERSION_DRIFT:
            return False, "scene_version_drift_exceeded"
        if result.config_version != state.config_version:
            return False, "config_version_changed"
        recent_ids = set(state.recent_presence.recent_event_ids)
        focus_ids = set(getattr(request, "focus_event_ids", ()) or ())
        if focus_ids and not (focus_ids & recent_ids):
            return False, "focus_events_evicted"
        return True, ""
```

然后用它替换 `accepted` 计算中的版本相等判断，并把真实原因码写入 `resolution`——当前 `scene_actor.py:469-476` 硬编码了 `"version_or_scope_mismatch"`，无法诊断。

**注意事项：**

- `MAX_SCENE_VERSION_DRIFT` 定义为模块级常量并加入 `__all__`，风格参照 `attention.py` 的 `AMBIENT_DECISION_BUDGET_SECONDS`。
- 请先确认 `GroupWorldState` 上确有 `config_version` 与 `recent_presence.recent_event_ids`（见 `world.py`），字段名不同则按实际调整。
- 保留 `_compatible_result` 的其余检查（persona 兼容性等），不要删。
- `ambient_deadline_expired`（`manager.py:1244-1250`）的过期丢弃逻辑保持不变，那是 spec 要求的有效期约束。

#### 验收

两个测试：actor 在接受结果前先消费 5 条无关新消息，`accept_result` 仍返回 True 且四表出现记录；`config_version` 变化时必须失效。

---

### 4.3 快车道因话题归类失败而静默失效，且失败时反而收紧

#### 现象
被 @ / 被回复的消息可能完全不产生候选，最终 `SILENCE` + `no_eligible_intention`。这与 spec"明确互动非零候选率 ≥99%"的验收指标直接冲突。

#### 位置与当前代码

`groupmate/social_runtime/participation.py:107-116`：

```python
        target_id = next(iter(frame.candidate_audiences), None)
        topic_id = next(iter(frame.focus_topic_ids), None)
        evidence = frame.focus_event_ids
        if not target_id or not topic_id or not evidence:
            return ParticipationProposal(
                lane=lane,
                candidates=(),
                allow_degraded=False,
                diagnostics=("deterministic_scope_missing",),
            )
```

`topic_id` 来源，`groupmate/social_runtime/attention.py:417-427`：

```python
    @staticmethod
    def _topic_id(world: GroupWorldState, event: SocialEventEnvelope) -> str:
        task_topic_id = str(event.payload.get("topic_id") or "").strip()
        if task_topic_id and any(
            topic.topic_id == task_topic_id for topic in world.active_topics
        ):
            return task_topic_id
        message_id = event.source_message_id or event.event_id
        try:
            return world.topic_for_message(message_id).topic_id
        except KeyError:
            return ""
```

空候选进 Governor 后，`governor.py:85-93` 返回 `SILENCE`。

#### 为什么不合理

两个问题，第二个更严重。

**其一**，直接互动的回应义务不该依赖话题归类成功。有人 @ 你，即使系统尚未把这条消息归入已知话题也该回应。`world.topic_for_message` 抛 `KeyError` 是完全可能的：新话题、投影延迟、消息 ID 不匹配都会导致。

**其二**，失败分支返回 `allow_degraded=False`，而成功分支返回 `True`（`participation.py:130`）。**系统越拿不到上下文，越严格禁止说话。** 逻辑应当相反：拿不到上下文时更该退回到保守但有效的行为（回应 @ 我的人）。这个反向默认值把偶发的归类失败放大成了直接互动的系统性沉默。

这也违背 participation-policy 规则 2 的意图——DIRECT_FAST 本应"直接生成候选"，由最终生成模型理解具体消息。

#### 修复方案

1. **为直接互动合成 fallback 话题。** `lane` 为 `DIRECT_FAST` 或 `CONTINUATION` 且 `topic_id` 为空时，用 `f"direct:{message_id}"` 合成稳定 id。

   **关键约束**：Governor 的 `_hard_reasons` 会检查 `candidate.topic_id not in context.allowed_topic_ids` 并拒为 `wrong_topic`（`governor.py:186-187`），而 `allowed_topic_ids` 来自 `frame.focus_topic_ids`（`manager.py:1182`）。**合成的 topic_id 必须同时进入 frame 的 `focus_topic_ids`**，否则下一关被拒。请在 `attention.py` 构造 AttentionFrame 时完成合成，而不是在 `participation.py` 临时造——frame 是决策的唯一真相源。

2. **修正失败分支的 `allow_degraded`。** FAST / CONTINUATION 失败时应返回 `True`。注意 AMBIENT 保持 `False` 不变，那是 spec 要求的保守性（"直接互动的容错不能扩散到环境插话"）。

3. **拆分诊断码**，区分 `deterministic_target_missing` / `deterministic_topic_missing` / `deterministic_evidence_missing`，当前统一为 `deterministic_scope_missing` 无法定位。

4. **`target_id` 缺失时** fallback 到 `source_event.actor_id`（谁 @ 我就回谁），这个信息一定存在。

#### 验收

一个测试：被 @ 的消息在 `world.topic_for_message` 抛 `KeyError` 时仍产出 `DIRECT_FAST` 候选、通过 Governor 的 `wrong_topic` 检查、最终 `outcome == "ACT"`。

---

### 4.4 认知降级的粒度过粗

#### 现象
任一降级来源触发即全盘降级。

#### 位置与当前代码

`groupmate/social_runtime/cognition/service.py` 有五个独立降级来源：

```python
        degraded = not rule_completed or bool(missing_workers)      # 211
        ...
                degraded = True                                     # 219（预算超限）
        ...
            degraded = degraded or not completed                    # 238（worker 未完成）
        ...
        if len(selected) > used_calls:
            degraded = True                                         # 249
```

#### 为什么不合理

`missing_workers` 这一条尤其危险：帧请求了某个 worker 但注册表没有就直接降级。生产只注册了 `ambient_social_assessor`（`astrbot_bridge.py:925-929`，符合最新 spec），而 `attention.py:357-360` 在 `safety.boundary` 和 `capability.result` 事件上仍会请求 `safety_guard`、`capability_interpreter`——这些帧会立即降级。

把"某个可选观察者缺失"与"规则 worker 崩溃"等同处理，丢失了做出正确决策所需的信息。

**注意**：AMBIENT 在降级时保持沉默是**正确的**（spec："AMBIENT 模型失败时仍保持观察"），不要改这条。本项只修降级判定的粒度，不放宽 AMBIENT 的保守性。

#### 修复方案

保留 `degraded` 布尔字段以兼容下游，新增 `degradation_reasons: tuple[str, ...]`，把 `missing_workers` 归类为轻微降级，使其不再单独触发全盘降级；规则 worker 失败、预算耗尽仍为严重降级。

`BlackboardSnapshot` 是 frozen dataclass（`cognition/blackboard.py`），修改时保持不变性。`blackboard.py:77-78` 的 `degraded` 与 `recommended_outcome` 联动保持不变。

#### 验收

一个测试：帧请求了未注册的 worker 且事件是被 @ 的消息时，最终 `outcome == "ACT"`；规则 worker 抛异常时 AMBIENT 仍沉默。

---

## 5. 执行顺序与验收

### 5.1 顺序（有依赖，不要跳步）

| 步骤 | 内容 | 理由 |
|---|---|---|
| 1 | **4.1** 跳过沉默路径的 LLM | 最简单、零风险、立即降延迟，为 4.2 争取时间窗 |
| 2 | **4.2** 语义新鲜度替代版本相等 | 让五张表有数据，否则后续改动无法观测 |
| 3 | **4.3** 快车道 fallback 话题 | 打开明确互动通道，最容易验证 |
| 4 | **4.4** 降级分级 | 依赖 4.3 的诊断码验证 |

每完成一步跑一轮 SHADOW 并记录指标，不要一次改完四项。

### 5.2 测试要求（从简）

本项目明确要求减少测试投入。participation-policy spec 自己写的是"采用针对性 TDD，不运行与本轮无关的完整 UI 或灾难恢复套件"。

**每个必修项只写第 4 节列出的那一到两个测试**，覆盖新增的行为分支即可。不要：为内部类补单元测试、为已有行为补回归测试、追求分支覆盖率、给每个诊断码单独写用例。

**但以下三个既有测试文件必须保持通过**，因为它们编码的是安全不变量而非实现细节：

- `tests/shared/test_shadow_side_effects.py` —— SHADOW 零副作用
- `tests/shared/test_group_scope_privacy.py` —— 跨群隐私隔离
- `tests/social_runtime/test_governor.py` —— 硬门不被绕过

改动涉及的模块跑对应目录即可（如改 `scene_actor.py` 就跑 `tests/social_runtime/test_scene_actor.py`），不必每次全量。

若某个既有测试的断言恰好编码了被修复的错误行为，可以改断言，但要在提交信息里说明原因。

### 5.3 最终验收

SHADOW 连续运行覆盖至少 500 条群消息后：

| 指标 | 目标 | 检查方式 |
|---|---|---|
| `cognitive_observations` / `candidate_intentions` / `attention_frames` | 非空 | SQL |
| `reply_plans` | 非空（记录 would_reply） | SQL |
| 明确互动非零候选率 | ≥ 99% | 按 `participation_lane` 分组 |
| DIRECT_FAST 决策 P50 / P90 | ≤ 1s / ≤ 3s | trace 时间戳 |
| AMBIENT 决策 P50 / P90 | ≤ 12s / ≤ 15s | trace 时间戳 |
| AMBIENT 模型失败时可发送候选 | 0 | SQL |
| Outbox `SENT` 记录 | **0**（SHADOW 零发送） | SQL |
| 沉默路径 LLM 调用 | 0 | 日志或计数器 |

查询写法可参考 `analysis/shadow_20260824.sql`。

### 5.4 明确不在本次范围

- 更换宿主框架（评估见 `analysis/framework-selection-20260831.md`，结论：先修效果）
- 架构重构与瘦身（见 `analysis/structural-refactor-plan-20260901.md`）
- 移动 SHADOW 门（最新 spec 已否决）
- 调整 AMBIENT 8 秒预算或窗口时长（spec 设计值）
- 补齐 notice 类事件（poke、群表情回应、撤回）
- `control_admin_ids` 不在 `_conf_schema.json`、Web 鉴权 fallback 到登录用户名的权限问题（`groupmate/settings.py:115-118`、`main.py:65,71`），属独立安全问题

---

## 6. 关键文件索引

| 文件 | 角色 |
|---|---|
| `groupmate/adapters/astrbot_bridge.py` | 4.1 主战场（`_handle_evaluations_locked` 在 1740） |
| `groupmate/social_runtime/scene_actor.py` | 4.2 主战场（`accept_result` 在 401-488） |
| `groupmate/social_runtime/participation.py` | 4.3 主战场（`_deterministic` 在 98-132） |
| `groupmate/social_runtime/cognition/service.py` | 4.4 主战场（降级判定在 211-249） |
| `groupmate/social_runtime/manager.py` | `_evaluate_cycle` 在 1089，force_observe 在 1192 |
| `groupmate/social_runtime/attention.py` | `_topic_id` 在 417，`_ambient_delay` 在 337 |
| `groupmate/social_runtime/governor.py` | 硬门在 170-190，force_observe 短路在 65-73 |
| `groupmate/social_runtime/persistence/event_store.py` | `resolve_scene_evaluation` 在 467，写入条件在 537 |
| `groupmate/social_runtime/control/message_traces.py` | 唯一可观测手段 |
| `docs/superpowers/specs/2026-08-24-participation-policy-and-dialogue-lease-design.md` | **需求基准** |
| `docs/superpowers/specs/2026-08-24-ambient-cognition-latency-root-fix-design.md` | **需求基准** |
