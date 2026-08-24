# Groupmate 参与策略与对话租约设计

日期：2026-08-24

## 目标

在不改变 AstrBot 外部插件归属、不放宽 Social Governor 硬约束、不破坏 SHADOW 零发送保证的前提下，使 Groupmate 能够：

1. 对明确点名、回复和直接称呼稳定地产生可评估的候选回复。
2. 在一次明确互动后维持短程对话连续性，而不是把后续每条消息重新当作环境插话。
3. 让环境参与继续保持保守，并在模型失败时默认沉默。
4. 将认知观察、候选意图、参与通道和 `would_reply` 结果可靠持久化。
5. 降低参与决策时延，使 SHADOW 数据能真实评估 Groupmate 的智能性。

## 证据与问题边界

本设计只处理已经被当前代码和线上数据共同确认的问题。

### 1. `forced_observe` 来自认知降级，不是 SHADOW 发送门

当前 `SocialRuntimeManager._evaluate_cycle()` 在 Blackboard degraded 或 AMBIENT 未通过参与评估时设置 `GovernorContext.force_observe`。`SocialGovernor` 随后返回 `OBSERVE / forced_observe`。

线上数据库中 408 条 Governor 结果全部为 `forced_observe`；候选意图均为空或只有 `observe_without_action`。与此同时，`AstrBotSocialRuntimeBridge` 已经在 SHADOW 下完成预览后跳过执行和发送。因此 SHADOW 的零发送位置是正确的，本轮不移动、不削弱该门。

### 2. 模型认知链路存在结构性时延

当前 Worker 超时默认是 10 秒。AMBIENT 同时请求 `scene_interpreter` 和 `participation_assessor`，但 `CognitionService.evaluate()` 串行执行 Worker，最坏仅认知阶段就需要约 20 秒。

线上已完成链路 P50 约 23 秒、P90 约 36 秒，且只有约 0.3% 在 15 秒内完成。该分布与串行的两次 10 秒 Worker 等待相符。

当前 CognitiveContext 还传入完整 GroupWorld，包括所有参与者、互动边和活跃话题。随着群状态增长，模型输入会持续膨胀。

### 3. 直接互动完全依赖模型成功

`LevelZeroRuleWorker` 只产生 `fact.target`，而 `IntentionEngine` 不会把它转换成可行动候选。FAST Worker 一旦超时或输出无效，Blackboard 就进入 degraded，最终只产生 OBSERVE。

这意味着平台已经确定的“用户明确 @ Bot / 回复 Bot / 直接称呼 Bot”仍要等待模型再次决定是否值得响应，既增加时延，也把可靠事实降格成不可靠推断。

### 4. 当前没有对话连续性状态

`GroupWorldState` 只记录 `last_bot_event_at`，没有当前对话对象、话题、过期时间或剩余轮次。AttentionScheduler 因而只能把没有再次 @ Bot 的后续消息放入 AMBIENT 窗口，无法区分“对话中的自然接话”和“Bot 主动插入群聊”。

目标群聊数据中 48.4% 的 Bot 发言轮次属于连续对话，多轮链中位数为 3 个 Bot 轮次、P90 约 6 个轮次。缺失该状态会直接损害群友感。

### 5. 认知与候选表没有进入写入主链

数据库已经包含 `attention_frames`、`cognitive_observations` 和 `candidate_intentions`，但线上对应认知与候选表均为空。当前 SceneWorkResult 只将 Governor 结果和 Worker 诊断写入评估事件，没有将安全裁剪后的认知观察和候选意图原子持久化。

因此运行中心只能看到最终 OBSERVE，无法回答“识别到了什么”“原本准备做什么”“为何没有继续”。

## 方案选择

### 采用：策略优先，模型增强

平台确定的直接互动和有效对话租约由确定性 Participation Policy 识别；这两条通道不再等待结构化认知模型，最终回复生成模型直接结合消息上下文完成语义理解与表达。结构化认知模型只承担环境话题理解与 AMBIENT 参与判断。模型失败不会抹掉直接互动事实，但 AMBIENT 在模型失败时仍保持观察。

该方案复用现有 Attention、Cognition、Governor、ReplyPlan 和 DeliveryGate 边界，只增加缺失的参与策略与对话状态，改动可控。

### 不采用：只调整 Prompt、Schema 或超时

这可以降低部分失败，但直接互动和连续对话仍完全依赖模型，无法解决模型抖动导致的零候选，也不能形成对话连续性。

### 不采用：重写完整运行时

现有外部插件归属、硬约束、ReplyPlan、Outbox 和 SHADOW 发送隔离已经具备正确边界。整体重写会增加迁移风险，却不会带来与风险相称的智能性收益。

## 核心设计

### Participation Policy

新增独立的参与策略模块，输入为：

- 冻结的 AttentionFrame；
- BlackboardSnapshot；
- 当前 ConversationLease（如有）；
- 当前时间。

输出为：

- `lane`：`DIRECT_FAST`、`CONTINUATION` 或 `AMBIENT`；
- 候选意图集合；
- 结构化诊断码；
- 是否允许在认知降级时继续治理。

规则如下：

1. FAST 且包含明确点名、回复、直接称呼或 poke 时进入 `DIRECT_FAST`。
2. `DIRECT_FAST` 不调用、不等待 `direct_interaction` 结构化 Worker，直接生成 `ACKNOWLEDGE / respond_to_direct_interaction` 候选。ReplyExecutor 的最终生成模型负责理解具体消息并组织表达。
3. 有效租约命中同一对象与话题时进入 `CONTINUATION`，同样不等待结构化 Worker，直接生成 `CONTINUE / continue_dialogue` 候选。ReplyExecutor 使用冻结上下文和租约对象完成表达。
4. 其他普通群消息仍进入 `AMBIENT`。AMBIENT 必须同时具备高置信参与评估与可行动语义候选；模型缺失、超时、冲突或低置信时保持 OBSERVE/SILENCE。
5. Privacy、paused、platform、boundary、target、topic、rate limit 和 minimum utility 仍完全由 Social Governor 决定，Participation Policy 不能绕过这些硬门。

确定性候选只表示“应该响应这个明确互动”，不包含回复正文，也不授权发送。

### Conversation Lease

在 GroupWorld 中增加可持久化的 `ConversationLease`：

- `target_id`
- `topic_id`
- `source_plan_id`
- `opened_at`
- `expires_at`
- `remaining_turns`

默认租约窗口为 180 秒，最多覆盖 6 个 Bot 发言轮次。新直接互动可以替换旧租约；同一对象和话题的连续回复会推进租约并减少剩余轮次。

租约只在以下情况建立或续期：

- SHADOW 下 ReplyPreview 状态为 READY；
- SOCIAL_RUNTIME 下回复已成功生成并形成可交付计划。

单纯产生候选但生成失败时不建立租约，避免虚构一段用户实际上没有体验到的对话。

租约事件通过 SocialEventFabric 写入 Journal，并由 GroupWorldProjector 投影。旧 Snapshot 缺少租约字段时按空租约恢复，保持向后兼容。

普通消息只有同时满足下列条件才命中租约：

- 发送者等于租约 target；
- 当前消息所属 topic 等于租约 topic；
- 当前时间未超过 expires_at；
- remaining_turns 大于 0；
- 消息未被外部插件取得响应归属。

其他成员插话或其他话题不会错误继承租约。

### 有界且并行的认知

`CognitionService` 保持 LevelZero 规则 Worker 先执行。DIRECT_FAST 与 CONTINUATION 在 LevelZero 后直接进入 Participation Policy，不调用结构化模型 Worker；AMBIENT 在预算内并行执行 `scene_interpreter` 和 `participation_assessor`。并发仍受现有全局 WorkerAdmissionQueue 和 worker concurrency limit 控制。

送入模型的 GroupWorld 改为有界视图，只包含：

- 当前 frame 涉及的话题；
- candidate audiences 的必要参与统计；
- 群活动摘要；
- 最近 Bot 发言时间；
- 当前租约摘要；
- 冻结的人格配置。

不再传入全量参与者、全量互动边或无关活跃话题。Focus events 继续只来自冻结 frame，并保留证据范围校验。

该改动不承诺模型生成总时延一定低于 5 秒，但会消除 AMBIENT Worker 的串行等待，并使参与决策不再随群历史无限变慢。

### 安全持久化

SceneWorkResult 增加：

- participation lane；
- 安全裁剪后的 CognitiveObservation；
- CandidateIntention；
- Participation Policy 诊断。

Actor 接受结果时，在与 GovernorResult 相同的 SQLite 事务中写入 attention frame、认知观察、候选意图和 Governor 结果。过期或 stale 结果不得写成已接受认知。

观察 proposition 使用白名单字段持久化，包括 subject、topic、decision、confidence 相关分数和非推理摘要。`chain_of_thought`、模型原始响应、Prompt、平台原始 URL 和未经裁剪的成员资料不得写入。

重复处理使用确定性 ID 和内容一致性检查；同 ID 不同内容必须报 identity conflict，不能静默覆盖。

### SHADOW 与外部插件边界

本轮明确不改变以下行为：

- SHADOW 永远不写 Outbox ready part、不调用 OneBot 发送，也不执行外部副作用。
- SHADOW 可以生成 ReplyPlan 和 ReplyPreview，用于记录真实 `would_reply`。
- 由 AstrBot 外部命令或链接插件拥有的事件保持零 AttentionFrame、零 cognition worker、零候选和零 Groupmate 回复。
- SOCIAL_RUNTIME 仍只对现有测试群 allowlist 开放。

## 数据流

```text
NapCat / AstrBot event
  → External ownership gate
  → GroupWorld projection
  → AttentionScheduler
      → DIRECT_FAST（平台明确事实）
      → CONTINUATION（有效租约）
      → AMBIENT（安静窗口）
  → bounded Cognition workers
  → ParticipationPolicy
  → SocialGovernor
  → ReplyPlan
  → ReplyPreview / generation
  → ConversationLease event
  → SHADOW stop 或 SOCIAL_RUNTIME delivery
  → Outcome / trace
```

## 智能性收益与验证方式

“提高智能性”不能用代码存在来证明，必须用行为结果验证。

### 直接互动可靠性

明确 @、回复或直接称呼不再调用结构化认知模型，应立即产生 `DIRECT_FAST` 候选并进入 Governor。最终回复生成模型失败时记录 `MODEL_FAILED`，不建立租约。安全硬门仍能拒绝候选。

这解决当前“用户明确找 Bot，但模型判断阶段失败后完全没有回应意图”的问题。

### 对话连续性

一次 READY 回复后，同一成员在同一话题 180 秒内自然接话应进入 `CONTINUATION`，无需再次 @。不同成员、不同话题、过期或耗尽租约必须回到 AMBIENT。

这直接对应目标 Bot 中占比最高的连续对话行为，同时避免无关抢话。

### 环境克制

AMBIENT 在模型超时、低置信或目标/话题不明确时仍不得产生可发送计划。直接互动的容错不能扩散到环境插话。

### 可解释性

下一份 SHADOW 数据库中必须出现非零的 attention frame、cognitive observation 和 candidate intention 记录；运行中心应能区分：

- DIRECT_FAST / CONTINUATION / AMBIENT；
- 模型环境候选 / 确定性直接或连续候选；
- Governor ACT / SILENCE / OBSERVE / DEFER；
- ReplyPreview READY / MODEL_FAILED / REJECTED；
- 最终 SHADOW 未发送。

### 时延

自动测试不使用脆弱的绝对墙钟断言，而是用同步屏障证明 AMBIENT Worker 并发进入，并检查输入视图大小有明确上限。

新的线上 SHADOW 验收目标为：

- DIRECT_FAST 参与决策 P50 不超过 1 秒，P90 不超过 3 秒；
- AMBIENT 参与决策 P50 不超过 12 秒，P90 不超过 15 秒；
- 完整候选回复生成时延单独统计，不与参与决策混为一个指标；
- 明确互动非零候选率达到 99% 以上，剩余失败必须有硬门或明确诊断码；
- AMBIENT 模型失败时的可发送候选数为 0。

这些目标先在本群 SHADOW 校准，不把目标群聊 Bot 的 4 秒/P90 15 秒机械复制为长期 SLA。

## 测试范围

采用针对性 TDD，不运行与本轮无关的完整 UI 或灾难恢复套件。

必须覆盖：

1. FAST 明确互动不调用结构化模型 Worker，并生成确定性候选。
2. FAST 最终回复生成失败时保留 would-reply 决策、记录失败诊断且不建立租约。
3. 确定性候选仍会被 privacy、paused、boundary 和 wrong target/topic 拦截。
4. AMBIENT 模型失败时保持 OBSERVE/SILENCE。
5. AMBIENT Worker 确实并发启动，预算和全局并发上限仍有效。
6. CognitiveContext 只包含有界相关场景。
7. READY ReplyPreview 建立租约；MODEL_FAILED/REJECTED 不建立。
8. 同对象同话题命中 CONTINUATION；对象、话题、过期和轮次耗尽均不命中。
9. 认知、候选、lane 和 Governor 结果原子持久化，stale 结果不持久化为 accepted。
10. SHADOW 下 Outbox 和 OneBot 调用仍为零。
11. 外部插件拥有的消息仍为零 Frame、零 Worker、零候选。
12. Snapshot 在增加租约字段前后的数据均可恢复。

## 非目标

本轮不实现：

- 表情包、视频解析或群管理能力；
- 冷启动主动开场；
- 长期关系模型重构；
- 人格编辑页面；
- 多模态回复生成；
- 放开 SOCIAL_RUNTIME 的生产群门禁；
- 基于目标群聊数据硬编码发言比例。

## 完成条件

只有同时满足以下条件才算完成：

- 所有新增行为均有先失败后通过的针对性测试；
- SHADOW 与外部插件边界回归测试通过；
- 新增认知和候选数据不包含 CoT、Prompt、原始媒体 URL 或私密身份数据；
- 可从一次直接互动走通 `DIRECT_FAST → ACT → ReplyPlan → READY preview → lease`；
- 可从下一条自然接话走通 `CONTINUATION → ACT/治理结果`；
- DIRECT_FAST 不受结构化认知 Worker 稳定性影响，回复生成失败可诊断且不建立租约；AMBIENT 模型失败时不冒进；
- 线上验收指标的采集字段已经进入 trace/projection，能够用下一份 SHADOW 数据复核智能性收益。
