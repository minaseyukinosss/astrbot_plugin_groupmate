# Unified Participation Decision Engine Design（统一参与决策引擎设计）

## 1. Goal（目标）

实现 `ParticipationDecisionEngine`（统一参与决策引擎），用全新的确定性机制替代旧 `OpportunityArbiter`（机会仲裁器）的连续效用分数。新机制一次性决定：

- 是否参与；
- 因为什么参与；
- 回复目标是谁；
- 使用什么 `ResponseAct`（回应动作）；
- 使用什么 `ResponsePosture`（回应姿态）；
- 是否引用、是否允许表情/图片反应；
- 为什么沉默。

运行时禁止把目标导出数据里的总体占比转成随机概率。目标数据只用于发现差距和验证场景条件是否覆盖。

## 2. Data Evidence（数据依据）

`eval/results/phase3-shadow.md`（第三阶段影子报告）显示：

- 高置信样本：6,720；
- 目标实际回复且当前机制也回复：40；
- 目标实际回复但当前机制沉默：0；
- 目标实际沉默但当前机制回复：3,590；
- 目标实际沉默且当前机制沉默：3,090；
- 直接称呼与社交场景存在明显过度引用：`target_unquoted_projected_quote`（目标未引用但当前预测引用）为 34。

结论：

1. 当前系统不是“叫不醒”，而是普通群消息被旧分数机制大面积放行；
2. 新机制重点应降低 `AMBIENT_CONTRIBUTION`（开放群聊参与）误插话；
3. `DIRECT_ADDRESS`（明确点名）仍必须可靠回应；
4. 引用策略不能再是直接称呼默认引用，应改为有交错上下文或真实 reply 证据时才引用；
5. 好感度不应回到旧连续分数加成，而应改变可成立的贡献类型和回复姿态。

## 3. Architecture Decision（架构决策）

### 3.1 Option A：继续调 `OpportunityArbiter`（机会仲裁器）

拒绝。目标差距来自“普通群消息被分数误放行”，继续调 `UTILITY_THRESHOLD`（效用阈值）只会把人格、好感和过度 @ 再次塞回分数项。

### 3.2 Option B：旧机制外加过滤器

拒绝。外层 `AmbientFilter`（开放参与过滤器）或 `AffinityFilter`（好感过滤器）会让“该不该说”被多次判断，后续解释困难。

### 3.3 Option C：新建 `ParticipationDecisionEngine`（统一参与决策引擎）

采用。保留 `TriggerRouter`（触发路由器）、`AddresseeResolver`（对象解析器）、`PresenceProjection`（在场投影）和 `PersonaParticipationProfile`（人格参与档案）这类可观察输入组件，但移除旧 `_score_utility`（效用打分）在线上决策中的作用。

## 4. Decision Contract（决策契约）

### 4.1 `ParticipationInput`（参与输入）

`ParticipationInput`（参与输入）是新引擎唯一输入对象，包含：

- `topic`（话题快照）：当前 `TopicSnapshot`（话题快照）；
- `trigger`（触发类型）：来自 `TriggerRouter.classify`（触发分类）；
- `scene`（交互场景）：来自新版 `classify_scene`（场景分类）；
- `targeting`（对象决策）：来自 `AddresseeResolver.resolve`（对象解析）；
- `presence`（在场投影）：来自 `project_presence`（群聊节奏投影）；
- `relationship_state`（关系状态）：当前目标用户的 `RelationshipState`（关系状态）；
- `affinity_snapshot`（好感快照）：来自 `snapshot_for_relationship`（关系转好感快照）；
- `persona_profile`（人格参与档案）：`AEMEATH_PARTICIPATION_PROFILE`（爱弥斯参与档案）；
- `recent_outputs`（近期输出）：用于重复检测和不垄断判断；
- `direct_address_state`（直接呼叫状态）：同一用户短时间内过度 @ 的状态；
- `task_resolution`（任务解析）：当前任务是否支持、是否缺信息；
- `policy`（群策略）：只读取限流、长度、功能开关等运行配置。

`ParticipationInput`（参与输入）不包含随机数、概率、连续好感加分或旧 `favorability`（旧好感度）参数。

### 4.2 `ParticipationDecision`（参与决策）

`ParticipationDecision`（参与决策）输出：

- `action`（动作）：`SPEAK`（发言）或 `SILENCE`（沉默）；
- `obligation`（回应义务）：`DIRECT_REQUIRED`（明确点名必答）、`OPEN_OPTIONAL`（开放场景可选）、`NONE`（无义务）；
- `scene`（交互场景）：最终采用的 `InteractionScene`（交互场景）；
- `act`（回应动作）：最终采用的 `ResponseAct`（回应动作）；
- `posture`（回应姿态）：最终采用的 `ResponsePosture`（回应姿态）；
- `audience_ids`（回复对象）：应该面向的用户；
- `target_message_id`（目标消息 ID）：可引用或续聊的消息；
- `quote_mode`（引用模式）：`ALWAYS`（总是引用）、`WHEN_INTERLEAVED`（交错时引用）、`NEVER`（不引用）；
- `media_policy`（媒体策略）：是否允许装饰表情、视觉反应或能力结果媒体；
- `contribution`（贡献说明）：给生成模型的中文任务句；
- `reason_codes`（原因码）：可审计的离散原因；
- `expires_at`（过期时间）：候选回复时效。

实施期可以先提供兼容转换器，让 `CognitiveWorkflow.evaluate`（认知工作流评估）少改动；稳定后删除旧 `SpeakOpportunity`（发言机会）和 `ReplyIntent`（回复意图）的重复判断。

## 5. Decision Order（决策顺序）

### 5.1 `BypassGate`（旁路门）

直接沉默：机器人自身消息、命令消息、空内容、平台重复事件和已过期开放候选。这些不是人格沉默，而是系统旁路。

发送/生成预算不在 `BypassGate`（旁路门）中取消 `DIRECT_REQUIRED`（明确点名必答）。明确点名必须先形成 `SPEAK`（发言）决策；模型不可用或生成预算耗尽时，改由 `DirectFallbackComposer`（直接回应降级组装器）按照回应动作和好感姿态生成一句最小回应。开放场景仍可因预算不足沉默。

### 5.2 `ConversationOwnershipGate`（对话归属门）

判断这句话是不是爱弥斯该接：

- 明确问另一个群友：沉默，原因 `owned_by_other_user`（归属其他用户）；
- 多人点名且目标不清：沉默，原因 `ambiguous_target`（目标歧义）；
- 回复爱弥斯或接续爱弥斯上一轮：进入 `DirectObligationGate`（明确回应义务门）；
- 面向群体的普通消息：进入 `OpenParticipationGate`（开放参与门）。

人格和好感不得覆盖对话归属。

### 5.3 `DirectObligationGate`（明确回应义务门）

平台真实 @、复制文本 @、句首别名、回复爱弥斯、有效续聊都属于 `DIRECT_REQUIRED`（明确点名必答）。除系统旁路外，不因低好感、无心情或普通沉默策略跳过。

直接回应分三类：

1. `contentful_direct`（有内容直接呼叫）：按内容正常回答、澄清、执行任务或守边界；
2. `bare_direct`（空点名/只叫名字）：短应声，不扩展话题；
3. `over_direct`（过度直接呼叫）：根据好感档位和过度程度改变姿态。

### 5.4 `DirectAddressPressure`（直接呼叫压力）

`DirectAddressPressure`（直接呼叫压力）描述同一用户短时间内重复 @ 爱弥斯的程度。它不是情绪值，而是可观察行为状态。

| 状态 | 条件 | 中文说明 |
|---|---|---|
| `NORMAL`（正常） | 第一次明确 @，或 @ 后带有实质内容 | 正常回应 |
| `NUDGE`（轻戳） | 短窗口内第二次空 @ / 只叫名字 | 简短提醒“有事直接说” |
| `PESTER`（纠缠） | 短窗口内三次及以上空 @，或刚回应后继续无内容 @ | 视好感生成不同行为姿态 |
| `AFTER_BOUNDARY`（边界后继续） | 已经明确提示后继续无内容 @ | 进入更强边界或更明显玩笑 |

计数规则：

- 只统计 `NATIVE_DIRECT`（平台 @）、`ALIAS_DIRECT`（别名句首）、`COPIED_AT`（复制 @）和 `REPLY_TO_BOT`（回复机器人）；
- 带完整问题、任务对象或新信息的 @ 不算过度 @；
- 用户提出新事实或新任务后重置压力；
- 超过 10 分钟无同类行为后重置压力；
- 计数按 `group_id + sender_id`（群 ID + 发送者 ID）隔离。

### 5.5 `AffinityPostureResolver`（好感姿态解析器）

`AffinityPostureResolver`（好感姿态解析器）把 `AffinityBand`（好感档位）、`DirectAddressPressure`（直接呼叫压力）和 `ResponseAct`（回应动作）合成回复姿态。它用于所有直接回应：正常 @ 时决定冷静、疏离、礼貌、温暖或亲近；过度 @ 时再把姿态放大为边界或戏谑。

| 好感档位 | `NUDGE`（轻戳） | `PESTER`（纠缠） | `AFTER_BOUNDARY`（边界后继续） |
|---|---|---|---|
| `HOSTILE`（敌对） | `BOUNDARY`（边界），冷静短句 | `BOUNDARY`（边界），坚定拒绝继续空耗 | `BOUNDARY`（边界），不接玩笑、不延长 |
| `WARY`（警惕） | `ACKNOWLEDGE`（应声）+ 疏离提醒 | `BOUNDARY`（边界），礼貌但明显降温 | `BOUNDARY`（边界），结束话题 |
| `NEUTRAL`（中性） | `ACKNOWLEDGE`（应声） | `ACKNOWLEDGE`（应声）或轻边界 | `BOUNDARY`（边界） |
| `FRIENDLY`（友好） | `PLAYFUL_REPLY`（轻玩笑） | `PLAYFUL_REPLY`（戏谑提醒），不伤人 | `ACKNOWLEDGE`（应声）+ 明确别继续刷 |
| `CLOSE`（亲近） | `PLAYFUL_REPLY`（亲近玩笑） | `PLAYFUL_REPLY`（更松弛的戏谑），仍让对方说正事 | `ACKNOWLEDGE`（应声）+ 亲近但清楚的停止提示 |

这实现用户确认的效果：低好感过度 @ 会表现出“有情绪”的冷淡或边界；高好感过度 @ 可以像熟人一样戏谑接住。但该“情绪”来自可审计的好感档位和行为压力，不恢复旧 `mood_key`（情绪键）或随机心情。

### 5.6 `OpenParticipationGate`（开放参与门）

普通群消息不再通过分数阈值。必须满足至少一个 `ParticipationMotive`（人格参与动机），且没有 `ParticipationInhibition`（人格参与抑制）。

允许成立的动机：

- `HELP_WHEN_CONCRETE`（有具体帮助时参与）：问题面向群体，爱弥斯能给具体短答；
- `CARE_WITH_EVIDENCE`（有证据关心）：用户状态明确，且关系/记忆支持关心不显得突兀；
- `PLAY_WHEN_INVITED`（被邀请玩笑）：玩笑明确冲爱弥斯或群体，且不是冲别人；
- `CONNECT_GROUP_CONTEXT`（连接群上下文）：能把前文多条信息自然接起来；
- `CONTINUE_OWNED_THREAD`（延续自己话题）：爱弥斯刚参与过且对方在接她；
- `EXPRESS_RELEVANT_PREFERENCE`（表达相关偏好）：只在话题与爱弥斯已设定兴趣或当前任务相关时成立。

必须抑制：

- `AVOID_EMPTY_ECHO`（避免空附和）：只能说“哈哈/确实/好耶”时沉默；
- `AVOID_MONOPOLY`（避免垄断）：近期机器人密度高时沉默；
- `AVOID_CROSS_THREAD_INTRUSION`（避免串线插话）：明确问别人或另一条线时沉默；
- `AVOID_GENERIC_CARE`（避免泛化关心）：没有关系证据时不输出万能安慰；
- `AVOID_FORCED_PLAY`（避免强行接梗）：任务、冲突、边界场景不接梗；
- `AVOID_UNEARNED_INTIMACY`（避免未证实亲密）：低关系/低好感不使用亲密话术。

`OpenParticipationGate`（开放参与门）输出的是因果判定，不是概率。

## 6. Scene And Act Updates（场景与动作调整）

### 6.1 `classify_scene`（场景分类）

保留 `InteractionScene`（交互场景）枚举，但新版 `classify_scene`（场景分类）不再只靠触发类型和少量正则。它应读取触发类型、回复链、任务动词和对象、空点名、视觉输入、感谢/称赞/礼物/玩笑、边界风险和对话归属冲突。

### 6.2 `ResponseAct`（回应动作）

保留当前 `ResponseAct`（回应动作），但来源改为统一引擎：`ACKNOWLEDGE`（应声）、`ANSWER`（回答）、`CLARIFY`（澄清）、`RECIPROCATE`（回应善意）、`PLAYFUL_REPLY`（轻玩笑）、`BOUNDARY`（边界）、`TASK_HANDOFF`（任务交接）、`TASK_UNSUPPORTED`（任务不支持）、`VISUAL_REACTION`（视觉反应）。

### 6.3 `QuotePolicy`（引用策略）

调整默认引用策略：

- 平台真实 reply 爱弥斯：允许引用；
- 交错多人上下文：允许引用；
- 句首点名但上下文未交错：默认不引用；
- 社交短回应：默认不引用，除非中间被多人插话；
- 开放群聊参与：不引用，除非明确承接某条消息。

这样修正影子报告中的过度引用问题。

## 7. Runtime Integration（运行时接入）

建议新增：

- `groupmate/engine/participation.py`：`ParticipationDecisionEngine`（统一参与决策引擎）主流程；
- `groupmate/engine/direct_pressure.py`：`DirectAddressPressureTracker`（直接呼叫压力跟踪器）；
- `groupmate/engine/participation_types.py`：`ParticipationInput`（参与输入）和 `ParticipationDecision`（参与决策）；
- `tests/test_participation_decision.py`：参与决策单元测试；
- `tests/test_direct_pressure.py`：过度 @ 与好感姿态测试；
- `tests/test_workflow_participation.py`：`CognitiveWorkflow.evaluate`（认知工作流评估）集成测试。

建议修改：

- `groupmate/engine/workflow.py`：调用新引擎，停止直接调用 `OpportunityArbiter.evaluate`（机会仲裁评估）；
- `groupmate/engine/opportunity.py`：第一阶段保留兼容，后续删除；
- `groupmate/engine/planner.py`：第一阶段保留兼容，后续将动作计划迁入新引擎；
- `groupmate/core/scenes.py`：扩展场景证据；
- `eval/shadow_projector.py`：使用新引擎投影；
- `eval/results/phase3-shadow.md`：重新生成，作为效果对比。

运行时数据流：

```text
TriggerRouter（触发路由器）
    -> AddresseeResolver（对象解析器）
    -> ParticipationDecisionEngine（统一参与决策引擎）
    -> ReplyPlan（回复计划）
    -> ResponseComposer（回复组装器）
    -> DeliveryService（投递服务）
```

## 8. Config（配置）

建议新增配置：

- `direct_pressure_window_seconds`（直接呼叫压力窗口秒数）：默认 600；
- `direct_pressure_nudge_count`（轻戳次数阈值）：默认 2；
- `direct_pressure_pester_count`（纠缠次数阈值）：默认 3。

必须移除：

- `v3_opportunity_enabled`（旧机会仲裁开关）：不再作为长期回退；
- `UTILITY_THRESHOLD`（效用阈值）：不再作为运行时参与条件。

## 9. Failure Behavior（失败策略）

新引擎失败默认沉默，但明确点名场景需要区分：

- 输入缺失或消息无效：沉默；
- 关系状态读取失败：使用 `NEUTRAL`（中性）好感快照，不读取连续分数；
- 直接呼叫压力读取失败：按 `NORMAL`（正常）处理；
- 任务能力解析失败：如实 `TASK_UNSUPPORTED`（任务不支持）或 `CLARIFY`（澄清），不得声称完成；
- 明确点名生成失败或生成预算耗尽：由 `DirectFallbackComposer`（直接回应降级组装器）给出与好感姿态一致的最小回应；
- 开放参与生成失败：沉默；
- Guard（输出防火墙）拒绝：沉默或修复后再发。

## 10. Test Strategy（测试策略）

按 TDD（测试驱动开发）实施。

### 10.1 Direct Required（明确点名必答）

覆盖：平台 @ 带问题必须发言、别名句首只叫名字应声、回复机器人必须回应、复制文本 @ 按明确点名处理、机器人自身消息/命令/空内容沉默、直接回应生成失败时使用人格化最小降级。

### 10.2 Over Direct And Affinity（过度 @ 与好感）

覆盖：

- `HOSTILE`（敌对）用户三次空 @：`BOUNDARY`（边界）+ `FIRM`（坚定）；
- `WARY`（警惕）用户三次空 @：`BOUNDARY`（边界）+ `RESERVED`（疏离）；
- `NEUTRAL`（中性）用户二次空 @：`ACKNOWLEDGE`（应声）+ `POLITE`（礼貌）；
- `FRIENDLY`（友好）用户三次空 @：`PLAYFUL_REPLY`（轻玩笑）+ `WARM`（温暖）；
- `CLOSE`（亲近）用户三次空 @：`PLAYFUL_REPLY`（轻玩笑）+ `CLOSE`（亲近）；
- 用户带新问题或新任务：压力重置，不按过度 @ 处理。

### 10.3 Open Participation（开放参与）

覆盖：面向别人的问题沉默、面向群体且有具体帮助可发言、只能空附和沉默、机器人近期密度高沉默、关系证据不足的泛化关心沉默、高好感用户明确邀请玩笑可轻玩笑、低好感用户未点名闲聊默认不主动靠近。

### 10.4 Quote And Media（引用与媒体）

覆盖：句首点名默认不引用、真实 reply 或多人交错时引用、边界场景禁止装饰媒体、歧义目标禁止装饰媒体、视觉反应按图片证据允许。

### 10.5 Shadow Evaluation（影子评估）

重新运行 `eval.shadow_export`（导出影子评估）后，重点观察：

- `target_silence_projected_reply`（目标沉默但当前预测回复）应显著下降；
- `target_reply_projected_silence`（目标回复但当前预测沉默）不能明显上升；
- `target_unquoted_projected_quote`（目标未引用但当前预测引用）应下降；
- `boundary_media`（边界媒体违规）保持 0；
- `false_completion_eligibility`（虚假完成资格）保持 0。

## 11. Acceptance Criteria（验收标准）

本阶段完成后应满足：

1. 线上参与决策不再读取 `UTILITY_THRESHOLD`（效用阈值）或旧 `_score_utility`（效用打分）；
2. 明确点名、回复爱弥斯和有效续聊稳定回应；
3. 明确点名不会被开放参与预算或旧回退开关取消，生成失败时仍有最小人格化回应；
4. 普通群消息必须有明确 `ParticipationMotive`（人格参与动机）才参与；
5. `AffinityBand`（好感档位）影响开放参与类型和直接回应姿态，但不变成分数加成；
6. 过度 @ 被 `DirectAddressPressure`（直接呼叫压力）识别，并按好感档位输出边界、应声或戏谑；
7. 爱弥斯仍保持爱弥斯人格，不复制小维身份、口癖、素材或媒体；
8. 所有新增文档中的函数名和关键字都配中文说明；
9. 单元测试和影子评估能解释每一个主要回复/沉默差异。

## 12. Implementation Sequence（实施顺序）

后续实施计划应按以下顺序展开：

1. 写失败测试：`DirectAddressPressure`（直接呼叫压力）和好感姿态；
2. 实现 `DirectAddressPressureTracker`（直接呼叫压力跟踪器）；
3. 写失败测试：`ParticipationDecisionEngine`（统一参与决策引擎）的明确点名门；
4. 实现明确回应义务；
5. 写失败测试：开放参与动机与抑制；
6. 实现开放参与门；
7. 写失败测试：引用策略；
8. 实现新版引用策略；
9. 接入 `CognitiveWorkflow.evaluate`（认知工作流评估）；
10. 更新 `eval.shadow_projector`（影子投影器）；
11. 运行测试与影子评估；
12. 删除或停用旧 `OpportunityArbiter`（机会仲裁器）运行路径。
