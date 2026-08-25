# Social Runtime v2：Shadow 运行手册

## 当前边界

首次配置默认使用 `SHADOW`。系统可以接收群事件、投影现场、运行认知与社会治理并记录“如果允许行动会怎么做”，但不会创建真实发送动作、不会写入 Outbox，也不会调用任何 QQ 发送端口。

`SOCIAL_RUNTIME` 只对配置中明确列入测试白名单的群开放；未进入白名单的群仍按 `SHADOW` 运行。这是发布门禁，不是 Provider 配置缺失。

## 主链路

```text
SocialEventEnvelope
  → GroupWorld projection
  → AttentionFrame (FAST / AMBIENT / TEMPORAL)
  → stateless Cognition Workers
  → cycle Blackboard
  → CandidateIntention
  → deterministic SocialGovernor
  → GovernorResult
  → Journal + governor_results (Shadow only)
```

GroupSceneActor 是单群单人格的现场写入者。Worker 在 Actor 邮箱之外运行，不能访问 Repository、Execution Port 或 Actor mutation。

## 周期冻结与过期结果

每个 AttentionFrame 同时冻结：

- `scene_version`
- `config_version`
- `persona_state_version`

Worker 返回后，Actor 会再次对照当前现场、当前配置与当前人格状态。任一版本不一致，工作项都会解析为 `stale`；即使 Governor 曾给出 `ACT`，也不会记录为有效 Shadow 决策，更不会产生外部动作。

AMBIENT 通道需要安静窗口到期后显式推进时钟：

```python
await manager.drain(now=current_timestamp)
```

不传 `now` 时只处理已经随事件生成的 FAST/TEMPORAL 帧，不会提前关闭“说完再答”的窗口。

AMBIENT 窗口的 deadline、聚合话题和证据事件随 pending SceneWorkRequest 一起持久化。窗口到期后，生成的 AttentionFrame 会先写回数据库，再交给 Worker；因此在窗口期间或 Frame 生成后崩溃都可以恢复。FAST Frame 已评估但同场仍有 AMBIENT 窗口时，工作项继续保持 pending，并用 `evaluated_frame_ids` 防止重放 FAST。

## 真实治理门禁

GovernorContext 不是测试常量。每周期会冻结 `RuntimeGovernanceState`，并与 PersonaSnapshot、权威 `safety.boundary` 事件共同构造：

- privacy allowed
- paused
- platform available
- capability allowed
- rate limit
- minimum utility
- boundary active

治理配置只能通过递增 `config_version` 更新；运行中变更会让旧 Worker 结果变成 stale。

## 持久化白名单

有效 GovernorResult 会在同一事务内：

1. 写入 `journal`，类型为 `shadow.governor_evaluated`；
2. 写入 `governor_results` 投影；
3. 若没有后续 AMBIENT 窗口，将 `scene_work_requests` 标记为 `accepted`；否则原子更新 pending 工作状态。

只保存以下治理摘要：outcome、selected intention IDs、rejected candidates、reason codes、reconsider time、active constraints，以及三类冻结版本。不会保存 Worker proposition、原始提示词或 Chain-of-Thought。

过期结果只把工作项标记为 `stale`，不会写入 Governor Journal/投影。

## 核查命令

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest \
  tests/social_runtime tests/scenarios tests/contracts tests/shared tests/recovery \
  -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m tests.architecture_guard
git diff --check
```

## Persona 触发机制复核

1. 分别发送主名称、确认别称、带别称的外部命令、普通正文提及和一次自然追问。
2. 运行中心应依次显示“会回应这次呼唤”“由外部能力处理”“普通群聊观察”“继续当前对话”。
3. 主名称、别称和续聊不得出现 `ambient_social_assessor`；普通正文提及可以出现。
4. SHADOW 只预览表达，不建立真实对话租约；正式运行仅在可用回复成功发送后建立租约。
5. 技术详情中不得出现 API Key、原始模型异常、内部提示词、Persona 背景或原始成员 ID。

## Persona 当前状态与素材门

Persona 的剧情事实分为当前状态、历史、能力、日常生活、价值和表达禁区。当前爱弥斯预设以“已重归现世并拥有能够现实行动的躯壳”为当前事实；“电子幽灵”只作为历史经历，不得覆盖当前状态。

当前事实进入回复模型是为了约束事实正确性，不代表回复必须复述这些内容。普通群聊默认不选择显式人设素材；只有消息本身与课程、音乐、游戏、保护、机兵或相关历史存在明确词义关联时，表达计划才允许携带最多一条素材。最近八条 Bot 输出已经使用过相关素材时，本轮继续使用会被冷却。

运行中心的表达摘要只展示三个面向人的结果：

- 当前成员关系阶段，例如“陌生”或“熟悉”；
- 本轮回应方式，例如“直接回应当前内容”或“承接上一轮内容”；
- 是否使用了当前话题相关的人设素材。

摘要不会展示完整 Persona 素材池、内部提示词或具体禁区文本。SHADOW 模式可以用以下消息核查：

1. `小爱，陪我聊会儿`：应显示“未使用显式人设素材”，候选回复不应随机提报告、歌曲、游戏、机兵、信号或频道。
2. `你最近在玩什么游戏`：允许显示“已使用当前话题相关素材”，但只使用一处，不堆叠课程、唱歌和机兵。
3. 连续询问 `说说你的机兵`：首轮允许使用相关素材，近期重复轮次应回到“未使用显式人设素材”。
4. `你现在还是电子幽灵吗`：可以说明这是过去经历，并以当前实体状态回答；不能继续自称仍以电子幽灵形态存在。

## 关系事件与好感度校准

关系变化只来自可追溯的关系事件。普通群聊复用现有 `ambient_social_assessor` 的同一次模型请求提取事件，不会为了好感度再调用一次模型；模型只能提议事件类型、对象、程度和证据，不能给出分数。具体增减、置信度门槛、去重和每日正向上限全部由本地规则决定。

运行中心的“关系变化”只展示事件、处理结果、当前阶段和面向人的说明，不展示原始模型摘要、内部关系维度或增减计算。处理结果含义如下：

- `SUGGEST`：SHADOW 认为事件可采用，但仅记录建议，不更新真实关系投影；
- `ACCEPT`：正式运行已通过本地规则并原子写入事件与关系投影；
- `REJECT`：证据置信度、对象范围或每日正向预算不满足，未计入；
- `DUPLICATE`：同一关系事件已经处理，本次不会重复累计。

校准时至少核查：

1. 直接呼唤或明确互动只生成 `interaction`，单次公开好感度变化不超过 `0.2`；
2. 延续已建立的对话只生成一次 `reciprocal_action`，重放同一消息不得重复累计；
3. SHADOW 中的友好、信任、玩笑、关心、越界与修复事件可以显示为 `SUGGEST`，但查询到的真实好感度保持不变；
4. 正式运行中只有 `ACCEPT` 改变关系阶段，模型超时、无证据、对象不明或无效输出都不得改变好感度；
5. 关系变化不得修改参与判断、回复归属、能力权限或 Governor 结果。

运行中还应核查：

- `outbox_count() == 0`
- `execution_port.calls == ()`
- `journal(correlation_id)` 只出现 world projection 与 Shadow evaluation
- stale 周期不存在 `shadow.governor_evaluated`

## 故障语义

- Worker 超时、异常或非法 JSON：记录诊断并降级为 OBSERVE/SILENCE，不绕过 Governor。
- Cognition 预算耗尽：Blackboard 标记 degraded，Governor 强制 OBSERVE。
- 场景、配置或人格状态变化：丢弃旧周期结果。
- 进程在结果前退出：pending SceneWorkRequest 可在重启后重发；已提交事件通过 Cursor/Snapshot 重放。
- Shadow evaluation 事务失败：工作项不会被部分接受，Journal 与投影不会出现半写状态。
- 相同结果重试：按持久化 identity 返回原成功；同 ID 不同内容硬失败。
- 多帧周期：耐久请求保留全部合法 Frame，每次 flush 只派发新到期 Frame；窗口与未评估 Frame 均为空后才能终结工作项。
- 显式丢弃或 stale：`scene_work_requests.resolution_json` 保存类别与 reason code，不能无理由消失。
- 关闭：Manager 先停止接收新工作，等待在途 Cognition（受 Worker timeout 限制），再关闭 Fabric 与 Supervisor。
- 投影和事件上下文查询：必须同时提供 `persona_id + group_id`，禁止跨人格或跨群读取。

## 进入 Phase C 前的硬条件

只有在固定场景回放、恢复测试、架构依赖守卫和 Shadow 零副作用检查全部通过后，才能设计 `ActionPlan → DeliveryBundle → Outbox`。Phase B 的 Governor `ACT` 不能被当作发送授权。
