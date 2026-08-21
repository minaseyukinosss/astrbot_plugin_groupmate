# Groupmate P0 闲聊主线设计

日期：2026-08-21

## 目标

本阶段只完成 Groupmate 作为 AstrBot 插件参与群聊的最小真实闭环：正确观察群消息，识别话题与交互对象，保守判断是否参与，生成一条符合人格和上下文的短回复，经校验后可靠发送，并把发送结果回流到群场景。

成功标准不是“尽可能多说”，而是“在明确有价值、对象与话题可靠时才说”。对象、话题或上下文不确定时，正式结果应为 `SILENCE`。

## 本阶段不做

- 不修改 Groupmate 插件管理页面。
- 不内置视频解析、群管理、表情包生成等功能插件。
- 不启用自治机会、复杂任务、主动追问和媒体选择。
- 不扩展通用 ActionPlan DAG 能力。
- 不迁移或读取旧设计、旧数据库和旧内部链路。

上述功能继续由 AstrBot 或其他插件承担。后续迁入 Groupmate 时必须通过独立 Capability 边界接入，不改变闲聊核心。

## 设计原则

1. **平台事实优先**：回复链、@、消息作者和 Bot 自身 ID 是权威事实；模型不得覆盖。
2. **保守参与**：宁可少说，不误判对象，不跨话题插话，不打断成员之间的对话。
3. **单一回复所有权**：外部插件拥有的指令或链接，Groupmate 只观察，不竞争响应。
4. **认知与表达分离**：先产生结构化观察和参与决策，再生成自然语言。
5. **失败即沉默**：模型缺失、超时、结构无效、场景过期或输出校验失败时，Ambient 场景保持沉默；只有明确直接呼叫允许固定安全降级文本。
6. **核心保持窄小**：闲聊核心只依赖场景、认知、治理、回复执行接口，不依赖任务、媒体和功能插件实现。

## 目标链路

```text
AstrBot group event
  -> Fact Adapter
  -> Ownership Gate
      -> EXTERNAL_PLUGIN: observe only
      -> SOCIAL_ELIGIBLE:
           Conversation Scene
           -> Attention Wakeup
           -> Structured Perception
           -> Candidate Intention
           -> Social Governor
               -> SILENCE: persist decision
               -> ACT:
                    ReplyPlan
                    -> Text Generation
                    -> Output Firewall
                    -> Transactional Outbox
                    -> OneBot Delivery
                    -> Delivery/Self-message Feedback
```

## 1. 事件事实与对话场景

### 1.1 事件适配器

`AstrBotEventTranslator` 继续只做事实转换，并补齐以下字段：

- `reply_to_message_id`
- `reply_to_actor_id`
- `reply_to_bot`
- `mentions`
- `mentions_bot`
- `bot_id`
- `is_self`

回复作者优先从 AstrBot/OneBot 已提供的数据获得；如果当前事件只包含被回复消息 ID，则通过持久化事件索引解析作者。解析不到时保留未知，不允许模型猜测。

### 1.2 Conversation Scene

P0 群场景只维护：

- 活跃话题及其最近消息
- 参与者
- 回复与提及关系
- Bot 最近一次发言时间和所在话题
- 群消息速率

普通非回复消息不再永久创建独立话题。话题归属按以下顺序确定：

1. 平台回复链；
2. 明确 @ 对象与最近相关话题；
3. 短时间窗口内的同参与者连续对话；
4. 无可靠归属时创建新话题。

活跃话题必须有 TTL 和数量上限，防止状态无限增长。任务、机会、文化和媒体状态不进入该投影。

## 2. Attention 与可靠唤醒

保留两条群聊通道：

- `FAST`：@Bot、回复 Bot、平台 poke、明确安全事件。
- `AMBIENT`：普通群消息经过安静窗口合并后评估。

Bridge/Manager 增加单一后台 wakeup task。每次 Ambient deadline 变化时通知该 task；到期后调用 `drain(now=deadline)`。新消息只能更新窗口，不能创建多个计时任务。

关闭插件时必须取消并等待 wakeup task，重启后从持久化 pending window 恢复。过期场景不得发送。

## 3. 结构化认知

使用 AstrBot 配置的 `generation_provider` 构造一个严格 JSON 模型端口，并注册三个 P0 Worker：

- `direct_interaction`：识别直接请求、招呼、求助、情绪和边界信号。
- `scene_interpreter`：识别当前话题、对话对象、社会行为和上下文连续性。
- `participation_assessor`：评估 Groupmate 是否有新增价值、是否可能打断、对象/话题是否可靠。

Worker 只输出有证据的结构化观察，不输出回复正文。必要 Worker 未注册时，启动状态必须明确 degraded；不得静默当作正常认知完成。

P0 可产生的社交意图限定为：

- `ACKNOWLEDGE`
- `HELP`
- `CARE`
- `PLAY`
- `BOUNDARY`

能力调用和任务接受不属于 P0 闲聊意图。

## 4. 参与决策

`SocialGovernor` 保留现有硬门控，并补齐 P0 决策输入：

- 是否明确对 Bot 说话
- 目标用户置信度
- 话题置信度
- 回应义务
- 新增价值
- 上下文连续性
- 打断成本
- 不确定性
- Bot 最近发言频率
- 重复表达风险

硬门控先于效用评分。以下情况必须 `SILENCE`：

- 外部插件拥有响应权
- 回复目标更可能是其他成员
- 话题无法可靠确定
- Ambient 没有新增价值
- 场景或候选意图已过期
- Bot 近期参与过密
- 认知结果降级且不是明确直接呼叫

一次周期最多选择一个主要社交意图，避免生成多目标、多话题回复。

## 5. 回复生成与投递

### 5.1 ReplyPlan

闲聊核心输出窄化的 `ReplyPlan`：

- `group_id`
- `topic_id`
- `target_id`
- `source_event_ids`
- `social_act`
- `required`
- `expires_at`
- `style_directive`

`ReplyExecutor` 在核心外部将 `ReplyPlan` 转换为已有的生成、验证和 Outbox 操作。普通闲聊不直接感知通用任务 DAG。

### 5.2 生成上下文

文本模型只接收：

- 当前话题的有限消息窗口
- 必要的相邻上下文摘要
- 当前社交意图
- 目标用户与回复关系
- 当前人格快照
- 最近 Bot 输出，用于抑制重复

默认输出为一条短消息。禁止默认助手式长答、总结式复述和无依据的功能成功声明。

### 5.3 安全与投递

复用现有 `OutputFirewall`、Transactional Outbox、Dispatcher 和 OneBot Adapter。输出校验顺序为安全、事实一致性、风格、重复。

发送成功后发布可观察反馈事件，更新 Bot presence 和话题关系。平台回显的 Bot 消息继续写入世界状态，但 `social_eligible=false`，不得触发自回复。

`UNKNOWN` 投递状态不得盲目重发。

## 6. 外部插件边界

现阶段维持两级响应所有权：

1. AstrBot 普通插件优先处理并停止传播；
2. 对无法停止传播的插件，`ExternalTriggerPolicy` 根据命令前缀和链接域名标记 `EXTERNAL_PLUGIN`。

外部触发规则必须能够通过运行时配置提供，不能只存在于隐藏设置字段。外部插件结果可以作为事实事件回流，但不会直接进入 Groupmate 的社交判断代码。

## 7. 组合根调整

`AstrBotSocialRuntimeBridge` 是唯一生产组合根，负责注入：

- AstrBot 模型端口
- 三个 P0 cognition workers
- Ambient wakeup
- Reply planner/executor
- Delivery dispatcher/OneBot transport

`SocialRuntimeManager` 负责场景、认知和决策编排；发送实现不放入 Manager 内部。`SHADOW` 与 `SOCIAL_RUNTIME` 使用同一决策链，区别仅在 ACT 之后：前者记录将要发送的 ReplyPlan，后者提交给 ReplyExecutor。

## 8. 最小验收与测试策略

不扩大测试矩阵，不为每个内部类补重复单元测试。开发中只运行受影响模块，完成时覆盖三个主场景：

1. **直接呼叫**：成员 @ 或回复 Bot，识别正确对象与话题，产生 ACT，并发送一条消息。
2. **Ambient 参与/沉默**：安静窗口能自动到期；有明确价值时 ACT，对象或价值不确定时 SILENCE。
3. **外部插件分流**：配置的命令与视频链接不运行 cognition、不产生 ReplyPlan、不进入 Outbox。

补充一个轻量自循环断言：Bot 发送回显会更新 presence，但不产生新的注意力帧。

验收关注端到端结果，不以组件数量或测试数量作为完成标准。

## 9. 实施顺序

1. 补齐平台事实、消息索引与 Conversation Scene。
2. 实现 Ambient 单一 wakeup。
3. 接入 AstrBot 模型端口和三个 P0 Worker。
4. 完善参与意图与保守 Governor 输入。
5. 实现 ReplyPlan/ReplyExecutor，接通生成与 Outbox。
6. 接通 OneBot Delivery 与反馈回流。
7. 运行最小端到端验收，在一个测试群先保持 SHADOW，再启用 SOCIAL_RUNTIME。

插件管理页面在以上链路稳定后另行设计和开发。
