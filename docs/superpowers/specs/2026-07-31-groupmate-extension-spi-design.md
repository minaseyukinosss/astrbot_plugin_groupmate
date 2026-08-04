# Groupmate Extension SPI 设计

日期：2026-07-31

状态：Phase A 静态 CapabilityProvider SPI 与 Phase B HostEventAdapter 已实施；具体 IntegrationAdapter 属于 Phase C

适用范围：Groupmate 内部能力扩展、AstrBot 特殊事件适配、第三方插件能力复用

## 1. 设计目的

Groupmate 本身是 AstrBot 插件，但未来需要吸收更多能力，例如图片理解、外部事实、
媒体检索，以及“智能回复戳一戳”这类非文本互动能力。

这些扩展存在两种不同问题：

1. Groupmate 如何理解一种新的 AstrBot 宿主事件，例如戳一戳、表情回应或入群通知；
2. Groupmate 如何复用一个内置模块、第三方插件或外部服务提供的能力逻辑。

原有 Capability Governance 已解决第二类问题的执行治理，但没有把第一类问题定义成
独立扩展点。本设计在现有架构前补充 Host Event Adapter，并把两者合称为
Groupmate Extension SPI。

本设计不把 Groupmate 变成第二套 AstrBot 插件系统，也不假设 AstrBot 原生插件可以
直接安装进 Groupmate。它只定义静态、显式、受治理的适配接口。

## 2. 核心判断

Groupmate 的默认扩展方式是自行实现内部 Provider。只有目标插件提供稳定服务入口时，
才通过专用 Integration Adapter 复用它的“能力逻辑”。Groupmate 不吸收原插件的宿主
命令、事件控制权、配置页面或发送出口。

因此扩展必须遵守以下不变量：

- 其他 AstrBot 插件命令继续由 HostEventGate 旁路；
- Groupmate 不模拟 slash command 调用其他插件；
- Capability Provider 不接收 AstrBot 原始事件；
- Provider 不直接发送消息，不调用 `stop_event()` 或 `should_call_llm()`；
- Provider 只返回结构化事实、媒体候选、状态或 handoff；
- 最终表达仍经过 Persona、OutputFirewall、Composer 和 DeliveryService；
- 同一宿主事件只能有一个最终回复所有者；
- 第一阶段不动态扫描目录、不加载未知代码、不接 MCP 或 Tool Gateway。

### 2.1 接入姿态

扩展来源按优先级分为：

1. **Groupmate 自有能力**：直接编码为内部 CapabilityProvider，是大多数新增能力的
   默认方式；
2. **可协作的外部插件**：插件侧暴露稳定、无发送副作用的服务入口，Groupmate 侧编写
   专用 Integration Adapter；
3. **不可协作的外部插件**：只有命令、事件处理器或直接发送逻辑，保持独立运行，不接入
   Groupmate。

因此“便于额外插件接入”指的是降低 adapter 开发和注册成本，不是承诺第三方 AstrBot
插件可以无修改、零配置地装入 Groupmate。

### 2.2 决策依据

这个判断来自三个约束：

1. AstrBot 原生插件的入口通常是命令处理器、事件监听器和配置项，而不是稳定库接口；
2. Groupmate 必须保留唯一参与决策、唯一人格表达和唯一发送出口，不能让外部插件直接
   回复同一轮事件；
3. 很多新能力只服务 Groupmate 自身，直接编码为内部 Provider 比改造外部插件更低成本。

因此设计目标不是“最大兼容外部插件形态”，而是“让值得复用的外部逻辑有清晰入口，
同时让自有能力扩展保持低摩擦”。

## 3. 当前架构与缺口

当前运行链路是：

```text
AstrBot Host
  -> HostEventGate
  -> HostEventAdapterRuntime / PokeEventAdapter
  -> AstrBotBridge
  -> GroupRuntimeManager / 每群 Actor
  -> ParticipationDecisionEngine
  -> CognitiveWorkflow
  -> CapabilityGovernor
  -> CapabilityRegistry / 内置能力
  -> Persona / OutputFirewall
  -> ResponseComposer
  -> DeliveryService
```

当前架构总体合理：

- HostEventGate 已保护其他 AstrBot 插件命令；
- 每群 Actor、话题、续聊和人格状态相互隔离；
- CapabilityGovernor 已成为内部能力唯一执行入口；
- DeliveryService 已成为唯一发送出口。

Phase A 与 Phase B 已关闭原先两个缺口：Bridge 通过 `CapabilityProviderRuntime` 静态装配
Provider 生命周期与健康状态；非文本宿主事件通过 `HostEventAdapterRuntime` 的白名单合成
消息进入统一 Actor 链路。剩余缺口是针对具体外部插件的稳定 service 与专用
Integration Adapter，这属于 Phase C，不通过动态发现或命令模拟补齐。

## 4. Extension SPI 组成

```text
Groupmate Extension SPI
├── HostEventAdapter
│   └── 宿主特殊事件 -> Groupmate 内部互动信号
│
├── CapabilityProvider
│   └── 受治理请求 -> 结构化 CapabilityResult
│
├── IntegrationAdapter
│   └── 外部插件服务入口 -> Event Adapter / Capability Provider
│
└── ActionAdapter（后期）
    └── 经过授权的副作用执行
```

本设计分阶段实施：先落 CapabilityProvider 生命周期与静态装配，再落 HostEventAdapter
最小接口。IntegrationAdapter 是外部可协作入口的包装方式，不作为可动态加载插件系统。
ActionAdapter 只保留边界说明，不进入代码。

### 4.1 HostEventAdapter

HostEventAdapter 属于 `groupmate/host/`，只负责识别和翻译 AstrBot 特殊事件。

它可以读取宿主事件，但输出中不能保留原始 AstrBot Event 或 Context。第一阶段复用
现有 `ChatMessage` 运行链路，输出显式的合成互动消息：

```text
ChatMessage
  origin = SYSTEM_SYNTHETIC
  text = ""
  segment_types = ("poke",)
  metadata = {
    "interaction_kind": "poke",
    "target_id": "bot-id",
    "source_adapter": "poke",
  }
```

这样可以复用 TopicWindow、每群 Actor、参与决策、人格和发送链路，同时避免伪造
人类文本。合成互动默认只参与短期话题，不生成长期人物记忆；是否形成社会事件由
参与决策后的显式规则决定。

HostEventAdapter 不能：

- 自行调用 Provider；
- 自行决定回复内容；
- 自行发送消息；
- 注册或模拟 AstrBot 命令；
- 把任意宿主字段塞入 metadata。

### 4.2 CapabilityProvider

CapabilityProvider 属于 `groupmate/capabilities/`，统一内部能力的声明、执行和生命周期：

```text
CapabilityProvider
  manifest -> CapabilityManifest
  start() -> None
  health() -> CapabilityHealth
  execute(request, context) -> CapabilityResult
  close() -> None
```

`CapabilityHealth` 是不可变值，只包含：

```text
CapabilityHealth
  available: bool
  reason_code: str
  checked_at: int
```

Provider 不拥有 Registry 或 Governor。静态装配器读取 manifest，启动 Provider，取得
健康状态，再构造现有 `CapabilitySpec` 注册到 `CapabilityRegistry`。执行时仍由
`CapabilityGovernor` 校验权限、deadline、timeout、并发、媒体策略和结果大小。

第一阶段健康状态在启动时采样。动态探活、自动恢复和后台刷新不在本阶段实现，避免
引入新的常驻任务和关闭顺序问题。

### 4.3 静态装配器

新增 `CapabilityProviderRuntime` 作为 Bridge 的显式装配对象：

```text
AstrBotBridge
  -> 创建内置 providers
  -> CapabilityProviderRuntime.start(providers)
  -> runtime.registry
  -> CapabilityGovernor(runtime.registry)
  -> 注入 CognitiveWorkflow
  -> bridge.close() 时 runtime.close()
```

Provider 列表由代码显式构造，不扫描目录。重复 manifest 名称、启动异常、非法健康状态
和非法结果都 fail closed。

### 4.4 IntegrationAdapter

IntegrationAdapter 是写在 Groupmate 侧的专用胶水代码，不是另一个可动态安装的插件。
它只负责把外部插件暴露的稳定服务接口包装为 Groupmate 的 Provider 或 Event Adapter。

```text
External plugin
  -> stable service entry
  -> Groupmate IntegrationAdapter
  -> CapabilityProvider / HostEventAdapter
  -> CapabilityProviderRuntime / HostEventGate
```

外部插件侧通常需要做最小改造：

- 把可复用逻辑从 AstrBot 命令或事件处理器中抽到独立 service；
- 暴露稳定的异步调用入口；
- service 不直接发送消息，不控制事件传播；
- 若原插件会回复同一事件，提供 service-only 模式或允许关闭原回复处理器。

Groupmate 侧需要新增：

- 一个专用 IntegrationAdapter；
- 一个 CapabilityManifest 和权限声明；
- 一处静态装配注册；
- 契约、失败和唯一回复所有者测试。

首阶段不定义通用二进制 ABI、Python entry point 扫描或第三方包自动发现。每个外部插件
是否值得适配，由具体价值和维护成本决定。

## 5. 戳一戳插件接入示例

### 5.1 插件提供稳定程序接口

原生插件默认不能直接接入。首先需要把其回复逻辑从 AstrBot 事件处理器中抽出，并暴露：

```text
suggest_poke_reply(group_id, actor_id, recent_context) -> suggestions
```

Groupmate 再新增一个专用 IntegrationAdapter，按需组合两个薄适配器：

```text
PokePluginIntegration
  external PokeReplyService -> PokeReplyProvider

PokeReplyProvider
  CapabilityRequest + CapabilityContext
  -> CapabilityResult(facts=("语气建议：轻微调侃", ...))

PokeEventAdapter（需要 Groupmate 接管 poke 事件时）
  AstrBot poke event -> SYSTEM_SYNTHETIC ChatMessage
```

完整链路为：

```text
poke event
  -> PokeEventAdapter
  -> 每群 Actor / TopicWindow
  -> ParticipationDecisionEngine 决定是否回应
  -> CognitiveWorkflow 选择 poke_reply
  -> CapabilityGovernor
  -> PokeReplyProvider
  -> Persona 生成爱弥斯回复
  -> OutputFirewall / Composer / DeliveryService
```

核心 Workflow、Persona 和 DeliveryService 不因第三方插件而增加专用调用逻辑。第三方
插件本体仍由 AstrBot 管理；Groupmate 只依赖其稳定 service，而不导入命令处理器。

### 5.2 插件只有 slash command

Groupmate 不发送 `/戳戳回复` 等模拟命令。Slash command 是用户与宿主插件的交互协议，
不是稳定程序接口。应由插件补充服务接口，或由 adapter 调用其可复用的纯逻辑函数。

### 5.3 插件自己监听事件并直接发送

这类插件默认保持独立运行，不能同时作为 Groupmate Provider 使用，否则同一戳一戳
可能出现双回复。

只有满足以下任一条件才能集成：

- 插件可以切换为 service-only / advisor 模式；
- 插件的原始回复处理器可以关闭，只保留可调用能力；
- 双方通过明确服务 API 约定唯一回复所有者。

无法满足时，Groupmate 不接管该事件，也不阻止原插件运行。

## 6. 兼容级别

| 能力来源 | 接入方式 | 结论 |
|---|---|---|
| Groupmate 自有能力 | 直接实现 CapabilityProvider | 默认方式 |
| 稳定 Python Service/API | 薄 CapabilityProvider adapter | 推荐 |
| 可复用纯逻辑函数 | adapter 包装函数 | 可接入 |
| AstrBot Tool / Function Calling | 后期 Tool Gateway | 本阶段不做 |
| 只有 slash command | 不模拟命令 | 暂不接入 |
| 监听事件并直接发送 | 保持独立或先切 service-only | 默认不接入 |
| 动态下载或未知代码包 | 不加载 | 禁止 |

“最小改动”不是让任何插件都能零配置接入，而是让符合边界的插件只需抽出 service、
新增 adapter 和一处静态注册，不修改 Groupmate 的参与、人格和发送主链路。对于多数
额外能力，直接在 Groupmate 内实现 Provider 比改造外部插件更简单，也应优先采用。

## 7. 回复归属与冲突处理

HostEventGate 继续负责宿主命令和普通消息归属。特殊事件适配器还必须声明显式启用
状态，默认关闭。

对于 poke 等可能被其他插件同时监听的事件：

- Groupmate adapter 未启用：完全交给宿主和其他插件；
- Groupmate adapter 启用且外部能力为 service-only：Groupmate 是最终回复所有者；
- 无法确认唯一所有者：Groupmate 只观察或完全旁路，不形成第二条回复。

不通过反射猜测其他插件是否会回复，也不依赖插件加载顺序解决冲突。

## 8. 数据与安全边界

HostEventAdapter 输出只允许包含白名单字段：互动类型、群、发起者、目标、稳定事件 ID、
时间戳和必要的公开摘要。

CapabilityProvider 只能获得 `CapabilityRequest` 和 `CapabilityContext`，不得获得：

- AstrBot 原始事件或 Context；
- PlatformPort、DeliveryService 或发送回执；
- MemoryRepository 写接口；
- GroupActor、TopicWindow 或 continuation 修改接口；
- PersonaRegistry 修改接口；
- AstrBot 命令注册器或事件控制函数。

合成互动事件默认不产生长期人物记忆，避免把“戳了一下”错误提升为稳定人物事实。

## 9. 生命周期与失败语义

- Provider 启动失败：记录不可用健康状态，不注册可执行能力；
- Provider health 不可用：Governor 返回 `capability_unavailable`；
- Provider 执行异常：沿用 `execution_error`，不得越过人格层生成完成声明；
- Provider timeout/cancel：沿用 Governor 现有语义；
- EventAdapter 识别失败：fail closed，交回宿主或忽略，不创建部分消息；
- Bridge 关闭：按启动逆序关闭 Provider，单个关闭失败不阻止其他 Provider 关闭；
- 重复能力名：启动失败并报告明确配置错误，不使用后注册覆盖前注册。

## 10. 文件边界建议

```text
groupmate/
├── capabilities/
│   ├── contracts.py            # 现有请求、结果、Manifest、Context
│   ├── provider.py             # CapabilityProvider / CapabilityHealth
│   ├── provider_runtime.py     # 静态生命周期与 Registry 装配
│   ├── governor.py             # 唯一执行治理入口
│   └── providers/
│       ├── vision.py           # 首个内置 Provider
│       └── external_handoff.py # 内置 handoff Provider
├── integrations/
│   └── <target_plugin>/         # 目标插件专用 adapter；按需增加
└── host/
    ├── bridge.py               # 装配根
    └── event_adapters/
        ├── base.py             # HostEventAdapter 窄接口
        └── poke.py             # Phase B 已实施的 AIOCQHTTP poke adapter
```

不新建通用 `plugins/` 目录，避免与 AstrBot Plugin 概念混淆。

## 11. 分阶段实施

### Phase A：静态 CapabilityProvider SPI（已实施）

- 定义 `CapabilityProvider`、`CapabilityHealth` 和 `CapabilityProviderRuntime`；
- 把 vision 与 external handoff 迁移为 Provider；
- Bridge 通过 runtime 静态装配 Registry 和 Governor；
- 覆盖启动、关闭、健康失败、重复名称和取消测试。

### Phase B：HostEventAdapter 最小接口（已实施）

- 定义特殊事件到合成 `ChatMessage` 的窄接口；
- 增加白名单 metadata 与 `SYSTEM_SYNTHETIC` 持久化限制；
- 用 fake poke event 验证 Actor、参与决策和唯一发送链路；
- 不绑定具体第三方插件。

### Phase C：具体插件 adapter

- 评估目标插件价值是否高于自行实现能力的成本；
- 若值得复用，先要求插件暴露稳定 service 或 service-only 模式；
- 通过 `groupmate/integrations/<target_plugin>/` 中的专用 adapter 接入；
- 若只能模拟命令或无法保证唯一回复所有者，则不接入。

每个 Phase 独立设计、计划、测试和提交。Phase A 不依赖 Phase B；Phase B 不要求存在
真实第三方插件。

## 12. 测试与验收

Provider SPI 至少覆盖：

- manifest 与 provider 一致；
- start/close 各执行一次，关闭逆序；
- 健康不可用时 executor 不运行；
- 重复 capability name 拒绝启动；
- Provider 无法接触平台、记忆写接口和发送接口；
- 所有执行仍经过 CapabilityGovernor；
- Provider 返回结果仍经过 Persona、Firewall、Composer 和 Delivery。

Event Adapter SPI 至少覆盖：

- 其他插件命令零副作用回归；
- 未启用 adapter 时特殊事件不进入 Groupmate；
- adapter 输出不保留原始 AstrBot Event；
- 合成互动不自动进入长期人物记忆；
- adapter 异常不阻塞后续每群 Actor 消息；
- poke 示例不会与宿主插件形成双回复；
- 正常文本消息、宿主暂停和 Phase 2 投影语义不回退。

## 13. 当前架构评价

当前 Groupmate 的架构适合继续扩展：宿主归属、每群运行时、参与决策、人格、能力治理
和发送出口已经分层。新增 Provider 时无需修改 TriggerRouter 或 DeliveryService，说明
主边界是合理的。

Phase A 与 Phase B 已补齐 Provider 生命周期和非文本事件适配入口，同时拒绝动态发现、
命令模拟和直接发送。当前仍不是通用第三方插件平台：具体外部能力必须在 Phase C 针对
目标插件的稳定 service 单独设计 Integration Adapter，并继续满足唯一回复所有者约束。

对“智能回复戳一戳”这类插件，最低安全接入成本通常包括插件侧抽出 service、Groupmate
侧新增专用 IntegrationAdapter、一个可选 Event Adapter 和一处静态装配。如果插件不
提供稳定程序接口，优先由 Groupmate 自行实现相同能力；若还无法保证唯一回复所有权，
则保持独立运行是正确结果，而不是强行兼容。
