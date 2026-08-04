# HostEventAdapter Phase B 设计

日期：2026-08-03

状态：已实施并完成验证（2026-08-04）

上位设计：`docs/superpowers/specs/2026-07-31-groupmate-extension-spi-design.md`

适用范围：AstrBot AIOCQHTTP 群聊特殊互动事件进入 Groupmate 的宿主边界

## 1. 目标

Phase A 已经通过 `CapabilityProvider` 解决 Groupmate 如何扩展“会做什么”，但当前宿主
入口仍只理解普通群消息。AIOCQHTTP 可以把 `Poke`（戳一戳）作为消息链组件交给插件，
现有 `OneBotTranslator` 虽然会保留 `poke` segment type，却会生成空文本消息；随后
`TriggerRouter` 将其判为忽略，核心无法理解这次互动。

Phase B 增加一个静态、显式、默认关闭的 `HostEventAdapter` 边界，把受支持的宿主特殊
事件翻译为不包含原始 AstrBot 对象的合成 `ChatMessage`，再复用每群 Actor、参与决策、
人格、防火墙、Composer 和 Delivery 主链路。

首个生产适配器只支持 AIOCQHTTP 中目标为 Bot 的 `Poke`。接口保持平台无关，但本阶段
不追求跨平台特殊事件兼容。

## 2. 非目标

本阶段不实现：

- 动态扫描、Python entry point 或第三方 adapter 自动发现；
- 把 AstrBot 原生插件直接安装到 Groupmate；
- 接入具体第三方戳一戳回复插件或调用其 slash command；
- 表情回应、入群通知、撤回等第二种生产事件；
- Capability Provider、Integration Adapter、Tool Gateway、MCP 或 Action Adapter；
- 通过反射判断其他插件是否也会回复同一事件；
- 修改数据库 schema；
- 为合成互动建立长期人物记忆或持久化社会关系事件。

## 3. 方案选择

### 3.1 采用：显式互动领域模型

新增 `HOST_INTERACTION` 触发类型和 `DIRECT_INTERACTION` 场景。`Poke` 保持空文本并通过
白名单 metadata 表达语义，不伪装成 `@`、回复消息或用户说过的一句话。

优点：

- 调试、评测和运行状态能准确区分文字直接唤醒与宿主互动；
- 压力、参与和回复动作可以对互动事件使用明确规则；
- 后续事件可以复用相同适配结果契约；
- 不污染现有 `NATIVE_DIRECT` 的稳定语义。

### 3.2 拒绝：复用 `NATIVE_DIRECT`

把 `Poke` 设置为 `mentions_bot=True` 虽然改动更少，但会把戳一戳误记为平台 `@`，导致
场景、压力原因码、评测与可观测性失真。

### 3.3 拒绝：独立 Poke 回复链路

从宿主入口直接生成或发送戳一戳回复会绕过 Actor、参与决策、Persona、OutputFirewall、
Composer 和 DeliveryService，并引入第二个回复出口。

## 4. 总体架构

```text
AstrBot AIOCQHTTP GROUP_MESSAGE
  -> HostEventGate
       -> command / wake prefix / ignored: 保持现有旁路
       -> GROUPMATE_MESSAGE
  -> HostEventAdapterRuntime
       -> NOT_MATCHED: 继续普通消息链路
       -> BYPASSED: 结束，不进入 Groupmate
       -> ADMITTED: 取得 SYSTEM_SYNTHETIC ChatMessage
  -> AstrBotBridge
  -> 每群 GroupActor
  -> HOST_INTERACTION / DIRECT_INTERACTION
  -> ParticipationDecisionEngine
  -> CognitiveWorkflow
  -> Persona / OutputFirewall / ResponseComposer
  -> DeliveryService / Outbox
```

顺序不可反转：`HostEventGate` 必须先处理宿主归属，确保已注册命令、宿主唤醒前缀、未
启用群、已停止事件和 Bot 自身事件不会进入特殊事件适配器。

## 5. HostEventAdapter 契约

### 5.1 Manifest

每个适配器声明不可变 manifest：

```text
HostEventAdapterManifest
  name: str
  event_kinds: tuple[str, ...]
```

`name` 和 `event_kinds` 必须是非空、规范化字符串。运行时拒绝重复 name，也拒绝两个
适配器声明同一个 event kind，避免依赖注册顺序决定最终事件所有者。

Phase B 的生产 manifest 为：

```text
name = "aiocqhttp_poke"
event_kinds = ("poke",)
```

### 5.2 适配结果

```text
HostEventAdapterStatus
  NOT_MATCHED
  BYPASSED
  ADMITTED

HostEventAdapterResult
  status: HostEventAdapterStatus
  reason_code: str
  message: Optional[ChatMessage]
```

不变量：

- `NOT_MATCHED` 和 `BYPASSED` 不能携带 message；
- `ADMITTED` 必须携带 `SYSTEM_SYNTHETIC` message；
- 适配结果不能保存 AstrBot Event、Context、Bot 或任意发送接口；
- reason code 只能是稳定枚举式字符串，不包含原始消息内容。

三种结果的含义：

| 状态 | 含义 | Ingress 行为 |
|---|---|---|
| `NOT_MATCHED` | 不是该适配器负责的事件 | 继续现有普通消息链路 |
| `BYPASSED` | 已识别但未启用、目标非 Bot 或字段非法 | 直接结束，不写入 Groupmate |
| `ADMITTED` | 已安全翻译 | 提交合成消息 |

### 5.3 Adapter 接口

```text
HostEventAdapter
  manifest
  adapt(event) -> HostEventAdapterResult
```

Adapter 可以读取原始宿主事件，但只能在调用栈内使用。它不能：

- 调用 `stop_event()` 或 `should_call_llm()`；
- 调用 Bridge、Actor、Provider、Persona 或 Delivery；
- 直接发送消息；
- 修改原始事件；
- 返回任意原始 payload 或对象引用。

### 5.4 静态运行时

`HostEventAdapterRuntime` 由代码显式接收 adapter tuple。它在构造时验证 manifest 和事件
所有权，处理事件时依次调用 adapter，直到出现第一个非 `NOT_MATCHED` 结果。

由于 event kind 不允许重复，正常情况下最多一个 adapter 能认领事件。adapter 抛异常、
返回错误类型或违反结果不变量时，运行时统一返回 `BYPASSED(adapter_error)`，不得把异常
传播到 Actor。

本阶段 adapter 无外部资源，因此不增加 start/close 生命周期。未来需要资源的外部
集成仍由 Integration Adapter 或 Provider 生命周期管理，不能把连接管理塞入事件翻译器。

## 6. PokeEventAdapter

### 6.1 识别范围

适配器只接受同时满足以下条件的事件：

- 已经通过 `HostEventGate`；
- 平台入口是 AIOCQHTTP；
- AstrBot message component 或 OneBot raw segment 明确表示 `poke`；
- group ID、sender ID、Bot ID、target ID 和 timestamp 可规范化；
- target ID 等于 Bot ID。

识别到 poke 后：

- `poke_enabled=false`：`BYPASSED(disabled)`；
- 目标不是 Bot：`BYPASSED(target_not_bot)`；
- 必需字段缺失或目标不明确：`BYPASSED(invalid_event)`；
- 满足接纳条件：`ADMITTED(admitted)`。

不是 poke 的普通消息返回 `NOT_MATCHED(not_matched)`。

### 6.2 合成消息

接纳结果使用：

```text
ChatMessage
  message_id = 稳定事件 ID
  group_id = 群 ID
  sender_id = 发起者 ID
  sender_name = 发起者公开显示名或 sender ID
  text = ""
  timestamp = 事件时间
  mentions_bot = false
  reply_to_bot = false
  is_bot = false
  is_command = false
  segment_types = ("poke",)
  origin = SYSTEM_SYNTHETIC
  platform = "aiocqhttp"
  bot_id = Bot ID
  metadata = {
    "interaction_kind": "poke",
    "target_id": Bot ID,
    "source_adapter": "aiocqhttp_poke"
  }
```

稳定事件 ID 优先使用宿主提供的 message/event ID。宿主没有 ID 时，对 platform、group、
sender、target、timestamp 和固定 subtype 的规范化组合计算确定性摘要。完全相同且不可
区分的重复回调会被现有消息幂等语义折叠。

metadata 不允许包含 `raw`、原始 message chain、昵称以外的账号资料、插件实例或任何
可调用对象。

## 7. Ingress 与 Bridge

`AstrBotEventIngress.handle_group_message()` 的顺序调整为：

1. 调用 `HostEventGate.classify(event)`；
2. 非 `GROUPMATE_MESSAGE` 立即返回原 disposition；
3. 调用 `HostEventAdapterRuntime.adapt(event)`；
4. `BYPASSED` 返回新的宿主互动旁路 disposition；
5. `ADMITTED` 将最终回复所有权标记为 Groupmate，并调用 Bridge 的合成消息入口；
6. `NOT_MATCHED` 继续现有 owner、observe-only 和普通 event 翻译链路。

Bridge 新入口只负责复用 `_prepare_actor(event)` 建立群、UMO、Provider 和历史基线，再把
已经适配好的 `ChatMessage` 提交给 Actor。它不能重新读取或修改合成消息 metadata。

Groupmate 接管 poke 时复用现有 `call_llm=True` 抑制 AstrBot 默认 Agent 的语义，但不调用
`stop_event()`。这可以避免本插件制造 AstrBot Agent 双回复，同时不阻断其他插件的
handler。

系统无法可靠探测另一个插件是否也会直接回复 poke。启用配置代表管理员明确选择
Groupmate 为最终回复所有者；其他直接回复同一事件的插件必须关闭对应 handler，或改为
service-only/advisor 模式。

## 8. Actor、触发与参与

### 8.1 来源保持

`GroupActor._stamp_message()` 对 `SYSTEM_SYNTHETIC` 保留原 origin，只补全 `ingested_at`。
普通实时、历史和 Bot delivery 的现有来源规则保持不变。

### 8.2 显式触发

`TriggerRouter` 只在满足以下条件时返回 `HOST_INTERACTION`：

- origin 为 `SYSTEM_SYNTHETIC`；
- `interaction_kind` 是受支持枚举；
- segment type 与互动类型一致；
- 消息不是 command 或 bot delivery。

未知、矛盾或缺失 metadata 的合成消息 fail closed 为 `IGNORE`。

### 8.3 场景与参与

`HOST_INTERACTION` 映射到 `DIRECT_INTERACTION`，属于硬优先级场景，不走普通候选消息
防抖。目标为 Bot 的 poke 使用 `DIRECT_REQUIRED` 参与义务，并选择短社交回应：

- 正常状态优先 `PLAYFUL_REPLY`；
- 重复互动复用现有直接压力窗口；
- 警惕或敌对关系下的高压力可以选择 `BOUNDARY`；
- pause 或 dispatch disabled 时只观察，不回复；
- 不打开或续期 continuation；
- quote mode 固定为 `NEVER`。

压力状态只服务当前参与判断，不提升为长期关系事实。

### 8.4 Prompt 表达

`format_history_block()` 对白名单合成互动使用固定映射，例如：

```text
[互动：戳一戳]
```

映射文本由 Groupmate 代码生成，不读取任意 metadata 文本，因此不会把宿主字段变成 prompt
指令，也不会伪造用户说过的话。未知互动只显示通用 `[互动]` 或直接不进入生成上下文。

## 9. 数据、记忆与投影边界

合成互动允许进入短期 `TopicWindow` 和 messages ledger，以便当前轮生成、幂等和审计。
它不得形成长期人物事实：

- `StateProjector` 不把 `SYSTEM_SYNTHETIC` 恢复成 `GroupSession` 对话轮次；
- `MemoryWriter` 在当前回复由合成互动触发时不提取用户或 Bot 长期记忆候选；
- 社会事件提取和关系投影跳过合成互动；
- continuation grant 只保留既有文字直接唤醒触发集合；
- TopicWindow 可以在当前短期窗口保留 poke，话题轮转后按现有规则淘汰。

现有 schema v11 已支持 `SYSTEM_SYNTHETIC`，Phase B 不增加表、列或迁移版本。

## 10. 配置与状态

新增配置组：

```text
interaction_group.poke_enabled = false
```

`false` 是安全默认值。配置解析继续拒绝错误类型并报告未知字段；旧配置无需迁移。

状态输出增加 `poke_adapter`：

- `disabled`：适配器已注册但不接纳事件；
- `enabled`：目标为 Bot 的 AIOCQHTTP poke 可被接纳。

状态不报告第三方插件兼容性，也不声称已验证唯一外部回复所有者。

## 11. 失败语义

| 失败 | 行为 |
|---|---|
| adapter 未匹配 | 继续普通消息链路 |
| adapter 关闭 | 旁路，不写入 Groupmate |
| 目标非 Bot | 旁路，不写入 Groupmate |
| 字段缺失或类型非法 | fail closed，旁路 |
| adapter 抛异常或返回非法值 | 运行时隔离为 `adapter_error` |
| 群未启用、事件已 stopped、sender 为 Bot | HostEventGate 提前忽略 |
| Groupmate paused | Actor 可观察合成事件，但不调度回复 |
| 参与或生成失败 | 沿用现有直接回应降级和 Firewall |
| 发送失败或回执未知 | 沿用 DeliveryService 与 Outbox |

任何 adapter 失败都不能调用 `stop_event()`，不能影响下一条普通群消息，也不能产生部分
合成消息。

## 12. 文件边界

```text
groupmate/host/
├── event_gate.py                 # 现有宿主归属，保持第一道门
├── ingress.py                    # gate -> adapters -> bridge 编排
├── event_adapters/
│   ├── __init__.py               # 公共导出
│   ├── base.py                   # manifest/result/ABC
│   ├── runtime.py                # 静态校验、异常隔离与分派
│   └── poke.py                   # AIOCQHTTP PokeEventAdapter
├── bridge.py                     # 合成消息进入 Actor 的宿主装配入口
└── onebot.py                     # 普通消息翻译，保持职责不变
```

领域语义分别修改 `groupmate/models.py`、`groupmate/engine/triggers.py`、
`groupmate/core/scenes.py`、`groupmate/engine/participation.py`、
`groupmate/engine/runtime.py`、`groupmate/core/history_format.py` 和必要的记忆边界。

不把 Poke 识别继续塞入 `OneBotTranslator`：普通消息翻译与特殊事件所有权是不同职责。

## 13. 测试与验收

### 13.1 契约与运行时

- manifest、状态和结果不可变且验证严格；
- 重复 adapter name 和重复 event kind 拒绝装配；
- adapter 异常和非法返回 fail closed；
- `NOT_MATCHED`、`BYPASSED`、`ADMITTED` 分派语义明确；
- 适配结果不包含原始 Event 或可调用对象。

### 13.2 Poke 适配

- 默认关闭时 poke 零 Groupmate 副作用；
- poke 其他群友时零 Groupmate 副作用；
- 目标为 Bot 时只生成一个白名单合成消息；
- component 和受支持 raw segment fixture 均可识别；
- 必需字段缺失时旁路；
- 相同宿主事件生成相同 message ID；
- 普通文本返回 `NOT_MATCHED`。

### 13.3 宿主归属

- 已注册 `/取名` 类命令不调用 adapter、Bridge、`stop_event()` 或 Groupmate owner 标记；
- 未知宿主唤醒前缀保持 AstrBot 所有权；
- 接纳 poke 时只抑制 AstrBot 默认 Agent，不停止其他插件；
- Groupmate pause 时不发送；
- adapter 异常后普通消息仍可进入同一群 Actor。

### 13.4 核心链路

- Actor 后 origin 仍为 `SYSTEM_SYNTHETIC`；
- poke 映射为 `HOST_INTERACTION` / `DIRECT_INTERACTION`；
- 互动立即评估，正常情况下选择受人格约束的短社交回应；
- 高频互动可进入现有压力边界；
- 回复经过 OutputFirewall、Composer、DeliveryService 和 Outbox；
- 不创建 continuation、长期记忆候选或持久化社会事件；
- Phase 2 重建不把互动恢复成文字 session turn。

### 13.5 回归组

实施完成后至少运行：

1. Host event gate、ingress、plugin loading 和 native wake ownership；
2. runtime、participation、workflow、memory writer、social event 和 Phase 2 projections；
3. 完整 pytest；
4. deterministic 120 条离线评测；
5. 宿主暂停场景；
6. `git diff --check` 与旧入口残留扫描。

## 14. 实施阶段边界

Phase B 完成门槛是：默认关闭不改变当前行为；开启后 AIOCQHTTP 的“戳 Bot”可以通过
唯一 Groupmate 主链路得到人格化回应，并且不污染命令、长期记忆、社会状态或外部插件
事件传播。

Phase B 完成后才能进入 Phase C。Phase C 必须针对一个具体外部插件重新评估：是直接在
Groupmate 内实现能力更便宜，还是要求目标插件抽出稳定 service 并编写专用 Integration
Adapter。Phase B 不预先承诺第三方插件兼容。

## 15. 完成证据

在实现提交 `909a4d8` 基础上执行 Task 7 收口验证：

- Host focused：`96 passed`，其中端到端互动边界新增 `5 passed`；
- Core focused：`164 passed`；
- Full pytest：`727 passed in 4.53s`；
- deterministic evaluation：`120/120`，`pass_rate=1.0`，`errors=0`，
  guard、privacy、trigger 检查均无回退；
- host pause 与 Phase 2 projection 精确回归：`2 passed`；pause 向 Actor 传递
  `schedule=False` 且无发送，projection 保留 synthetic poke 审计消息但不恢复 user
  session turn 或 continuation；
- adapter event-control/send scan 无匹配；raw host access 仅存在于 `poke.py` 的提取逻辑，
  合成消息 metadata 仍为白名单字符串字段；
- `PokeEventAdapter` 的生产实例化仅位于 `main.py`，核心显式 interaction 语义只出现在
  计划的 host、domain、Actor、workflow、memory/projection 边界；
- `git diff --check` 退出码为 0，无输出。

Phase A 与 Phase B 均已实施。具体第三方插件仍需在 Phase C 暴露稳定、无发送副作用的
service，并由专用 Integration Adapter 接入；Phase B 不声明自动兼容任何第三方 poke
回复插件。
