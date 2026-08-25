# Groupmate 单一回复所有权设计

## 目标

避免同一条直接指向 Bot 的群消息同时触发 AstrBot 默认 LLM 回复和 Groupmate 回复，同时保留 SHADOW 观察、AstrBot 指令插件和外部能力插件的原有链路。

## 已确认的问题

当前 Groupmate 的低优先级群消息处理器会进入 Social Runtime，但不会调用 `AstrMessageEvent.stop_event()`。当消息包含 `@bot` 时，AstrBot 默认 LLM 链仍然具备回复资格；若 Groupmate 在 `SOCIAL_RUNTIME` 模式下也生成并通过 OneBot 发送回复，就可能出现两次回复。

## 方案比较

### 方案一：所有 `@bot` 一律停止 AstrBot 传播

实现最简单，但会误伤 `@bot /命令`、外部插件能力和 SHADOW 模式，不采用。

### 方案二：Groupmate 成功发送后再停止传播

可以避免生成失败时无回复，但回复所有权确认过晚，且把 AstrBot 事件传播与 OneBot 发送结果耦合，不利于形成干净链路，不采用。

### 方案三：处理前进行确定性的回复所有权判定

在调用模型和发送消息之前，根据运行模式、群范围、交互归属和直接称呼信号确定是否由 Groupmate 独占回复。该方案边界清晰、无模型依赖，采用。

## 所有权规则

Groupmate 仅在以下条件全部成立时接管 AstrBot 默认回复：

1. 当前群的实际运行模式为 `SOCIAL_RUNTIME`；
2. 归属判定为 `GROUPMATE`，而不是 `EXTERNAL_PLUGIN`；
3. 消息明确指向当前人格，包括 `@bot`、回复 Bot、人格名称或已配置别名；
4. 消息具备 Social Runtime 参与资格。

满足条件时，入口必须在进入耗时的认知与生成链路之前调用 `event.stop_event()`，再交给 Groupmate 处理。

以下情况不得停止 AstrBot 传播：

- `OFF`；
- `SHADOW`；
- 非目标群；
- 外部指令或链接能力；
- 普通环境群聊观察；
- 无法可靠完成归属判定的消息。

## 数据流

```text
NapCat 群消息
  -> AstrBot 插件调度
  -> Groupmate 无副作用所有权预判
       -> 外部插件 / SHADOW / OFF：不停止，维持 AstrBot 原链路
       -> Groupmate 直接交互：stop_event，独占回复所有权
  -> Groupmate Social Runtime
  -> 回复生成与安全校验
  -> OneBot 单次发送
```

早期观察器仍只负责记录消息到达，不修改事件、不调用模型、不发送消息。

## 失败处理

直接交互一旦由 Groupmate 接管，就不能再回退到 AstrBot 默认 LLM 链，否则会重新引入竞态和双回复。现有 `DIRECT_FAST` 必答计划继续负责生成失败时的安全降级；若运行时尚未就绪，则所有权预判必须返回“不接管”。

## 验收条件

- `SOCIAL_RUNTIME + @bot/人格名/别名/回复 Bot`：调用一次 `stop_event()`，Groupmate 保持原处理链；
- `SHADOW + @bot`：不调用 `stop_event()`，Groupmate 只记录和预览；
- `OFF` 或非目标群：不调用 `stop_event()`；
- 外部指令和外部链接：不调用 `stop_event()`；
- 普通环境消息：不调用 `stop_event()`；
- 所有权预判无模型调用、无数据库写入、无消息发送。

测试仅运行相关入口与桥接层定向用例，不扩大到无关套件。
