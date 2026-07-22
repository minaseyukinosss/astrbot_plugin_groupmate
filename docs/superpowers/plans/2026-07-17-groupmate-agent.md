# Groupmate 智能体实施计划

> **状态（2026-07-22）：** 核心已落地。影子评测 / WebUI 控制中心 / 表达学习等后续草案已废弃，勿再按任务 9 的 evaluation 路径实施。现行架构见 `docs/superpowers/specs/2026-07-22-groupmate-v2-architecture.md`。

**目标：** 构建可用于生产的 AstrBot 群聊伙伴插件：观察 QQ 群、补拉历史、结构化门控、人格短回复、有限持久化。

**架构：** 端口与适配器 + 确定性认知工作流；每群 Actor 串行；领域不依赖 AstrBot。

**技术栈：** Python 3.7+ 可测 / 运行适配 AstrBot 4.24+、NapCat/OneBot v11、pytest。

---

## 文件职责

- `main.py`：AstrBot 插件类、Handler、Hook 和管理命令。
- `metadata.yaml`：AstrBot 插件元数据与版本兼容声明。
- `_conf_schema.json`：WebUI 配置定义。
- `groupmate/models.py`：不可变领域事件、决策、策略和记忆类型。
- `groupmate/ports.py`：模型、视觉、存储、平台、历史和时间协议。
- `groupmate/triggers.py`：确定性触发与指令路由。
- `groupmate/topics.py`：有限工作上下文与话题聚合。
- `groupmate/rate_limit.py`：每群主动参与额度。
- `groupmate/memory.py`：SQLite 事件、档案、记忆、决策与 Outbox 存储。
- `groupmate/persona.py`：人格提示词组合与爱弥斯预设加载。
- `groupmate/guardrails.py`：确定性回复校验。
- `groupmate/workflow.py`：强类型认知状态机。
- `groupmate/runtime.py`：每群 Actor 邮箱与防抖调度。
- `groupmate/astrbot_adapter.py`：AstrBot 事件翻译和具体端口实现。
- `resources/aemeath_persona.md`：爱弥斯默认人格预设。
- `tests/`：离线单元测试与契约测试。

## 任务 1：插件骨架与领域类型

**文件：**

- 创建 `metadata.yaml`
- 创建 `requirements.txt`
- 创建 `groupmate/__init__.py`
- 创建 `groupmate/models.py`
- 创建 `groupmate/ports.py`
- 测试 `tests/test_models.py`

实施步骤：

1. 先编写失败测试，验证 `ChatMessage` 文本规范化、消息身份以及 `Decision.ignore()` 的安全默认值。
2. 运行 `pytest tests/test_models.py -q`，确认因领域模块不存在而失败。
3. 实现 `TriggerKind`、`DecisionAction`、`Urgency`、`MemoryKind` 枚举，以及 `ChatMessage`、`TopicSnapshot`、`Decision`、`ReplyPlan`、`MemoryItem`、`GroupPolicy` 等不可变类型。
4. 定义 `DecisionModelPort`、`GenerationModelPort`、`PlatformPort`、`MemoryRepository`、`Clock` 等协议，领域模块不得导入 `astrbot`。
5. 再次运行测试，确认通过后提交：`feat: add groupmate domain contracts`。

## 任务 2：触发路由、话题窗口与频率限制

**文件：**

- 创建 `groupmate/triggers.py`
- 创建 `groupmate/topics.py`
- 创建 `groupmate/rate_limit.py`
- 测试 `tests/test_triggers.py`
- 测试 `tests/test_topics.py`
- 测试 `tests/test_rate_limit.py`

需要验证的行为：

- 既有指令被标记为 `COMMAND` 并旁路；
- 原生 `@` 和回复 Bot 被标记为 `NATIVE_DIRECT`；
- “小爱，在吗”属于 `ALIAS_DIRECT`；
- “小爱是不是挺难调的”属于 `ALIAS_MENTION`；
- 话题窗口有最大容量并按消息身份去重；
- 第七条主动消息被每小时六条的额度拦截；
- 过期额度能够释放，冷却时间仍然生效。

完成后运行：

```bash
pytest tests/test_triggers.py tests/test_topics.py tests/test_rate_limit.py -q
```

预期全部通过，提交信息：`feat: add group participation policies`。

## 任务 3：SQLite 记忆与回放存储

**文件：**

- 创建 `groupmate/memory.py`
- 测试 `tests/test_memory.py`

测试必须覆盖：

- 相同消息重复写入时保持幂等；
- 最近消息按时间正序返回；
- 记忆过期后不再被检索；
- 低权威数据不能覆盖高权威人工关系；
- Outbox 使用 `decision_id` 保证幂等。

数据库启用外键与 WAL，并创建带版本的 `messages`、`profiles`、`memories`、`decisions`、`outbox` 表。记忆检索综合关键词重叠、时效性、重要性、置信度和权威级别。

完成后运行 `pytest tests/test_memory.py -q`，提交信息：`feat: add persistent social memory`。

## 任务 4：人格组合与输出 Guardrail

**文件：**

- 创建 `groupmate/persona.py`
- 创建 `groupmate/guardrails.py`
- 创建 `resources/aemeath_persona.md`
- 测试 `tests/test_persona.py`
- 测试 `tests/test_guardrails.py`

人格模块需把稳定系统提示词与动态 `<group_context>` 分离，并对群友名称、消息文本和记忆进行转义与长度限制。

输出校验必须拒绝：

- “没人叫我，不回复”等决策旁白；
- “有什么可以帮你的吗”等客服模板；
- `prompt`、模型输出、插件配置等系统词汇；
- 超长或超过两句的闲聊；
- 与近期输出高度重复的回复；
- 内部 ID 泄露。

只有风格问题允许修复；重复、空输出和内部数据泄露不可修复。完成后运行相关测试并提交：`feat: enforce persona response boundaries`。

## 任务 5：认知工作流

**文件：**

- 创建 `groupmate/workflow.py`
- 创建 `tests/fakes.py`
- 测试 `tests/test_workflow.py`

工作流必须显式执行：

```text
OBSERVE → RECALL → GATE → PLAN → GENERATE → GUARD → SEND
```

需要验证：

- 决策模型失败时默认沉默；
- 合法决策会生成、校验并发送回复；
- 别名直接唤醒不调用主动参与决策模型；
- 低于置信度阈值的决策不发送；
- 视觉模型只在 `needs_vision` 为真时调用；
- 风格修复最多执行一次；
- Outbox 入队后才允许发送，发送成功后标记完成。

运行 `pytest tests/test_workflow.py -q` 后提交：`feat: add cognitive response workflow`。

## 任务 6：每群 Actor 运行时

**文件：**

- 创建 `groupmate/runtime.py`
- 测试 `tests/test_runtime.py`

Actor 使用 `asyncio.Queue` 串行处理以下内部消息：

- `Ingest`
- `EvaluateTopic`
- `Flush`
- `Stop`

防抖任务只能向邮箱投递 `EvaluateTopic`，不能直接修改 Actor 状态。测试验证连续消息爆发只触发一次判断、原生唤醒取消等待中的主动插话、别名直接唤醒无需防抖，以及不同群之间状态隔离。

运行 `pytest tests/test_runtime.py -q`，不得出现残留异步任务警告。提交信息：`feat: serialize per-group agent state`。

## 任务 7：AstrBot 与 NapCat 适配层

**文件：**

- 创建 `groupmate/astrbot_adapter.py`
- 创建 `main.py`
- 测试 `tests/test_astrbot_translation.py`

实现内容：

- `OneBotTranslator`：翻译实时事件和 NapCat 历史字典；
- `NapCatHistoryPort`：调用 `get_group_msg_history`，默认补拉 100 条；
- `AstrBotDecisionModel`：调用指定决策 Provider，并解析严格 JSON；
- `AstrBotGenerationModel`：调用当前群聊天模型；
- `AstrBotPlatformPort`：通过 `Context.send_message()` 主动发送；
- `AstrBotPersonaProvider`：读取 AstrBot 人格，失败时使用内置预设；
- `AstrBotBridge`：管理群、UMO、Provider、历史补拉和运行时。

`main.py` 注册 AIOCQHTTP 群消息观察器、`on_llm_request` 上下文增强 Hook，以及以下管理员命令：

- `/groupmate_status`
- `/groupmate_pause`
- `/groupmate_resume`
- `/groupmate_reset`

完成后执行：

```bash
pytest tests/test_astrbot_translation.py -q
python -m compileall -q main.py groupmate
```

预期均成功，提交信息：`feat: integrate AstrBot and NapCat`。

## 任务 8：WebUI 配置与诊断

**文件：**

- 创建 `_conf_schema.json`
- 创建 `groupmate/config.py`
- 测试 `tests/test_config.py`

WebUI 暴露以下配置：

- 群白名单和 Bot 别名；
- 决策、回复、视觉 Provider；
- AstrBot 人格或本地人格文本；
- 历史窗口、置信度阈值、每小时额度和冷却；
- 防抖范围、图片理解和记忆保留时间。

配置对象需要规范化群号与别名，并对数字范围进行限制。Provider 与人格字段只使用 AstrBot 官方 `_special` 选择器。

验证命令：

```bash
python -m json.tool _conf_schema.json >/dev/null
pytest tests/test_config.py -q
```

提交信息：`feat: add groupmate WebUI configuration`。

## 任务 9：说明文档与最终验证

**计划文件：**

- `README.md`
- `.gitignore`

README 说明安装位置、NapCat 要求、精简配置、隐私、管理命令与现行架构。不包含影子评测 / evaluation 子系统。

最终验证：

```bash
PYTHONPATH=. pytest -q
python -m json.tool _conf_schema.json >/dev/null
```

所有命令必须成功。

