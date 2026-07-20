# AstrBot Groupmate 群聊伙伴插件

Groupmate 让 AstrBot 不再只在收到指令时工作，而是能够观察 QQ 群聊、理解最近话题、判断何时适合加入，并按照配置的人格自然回复。

插件面向 **AstrBot 4.24+** 与 **NapCat/OneBot v11**，默认提供爱弥斯人格预设，同时保留通用配置能力。

## 主要能力

- 观察群聊并为每个群维护独立的最近消息窗口。
- 首次接触群聊时，通过 NapCat 尽力补拉最近 100 条消息。
- 区分原生唤醒、别名直接呼叫、间接提及、普通候选消息和既有指令。
- 使用独立的小模型判断是否应该主动插话。
- 使用当前群聊天模型生成最终回复。
- 仅在话题确实依赖图片时调用视觉模型。
- 使用 SQLite 保存消息索引、社交档案、近期事件、决策轨迹和 Outbox。
- 通过长度、句数、客服腔、决策旁白、系统词汇和重复内容 Guardrail 约束输出。
- 每群 Actor 串行处理状态，避免并发消息造成重复回复。

## 安装

将整个插件目录放入 AstrBot 的插件目录：

```text
AstrBot/data/plugins/astrbot_plugin_groupmate/
```

插件目录至少应包含：

```text
astrbot_plugin_groupmate/
├── main.py
├── metadata.yaml
├── _conf_schema.json
├── requirements.txt
├── groupmate/
└── resources/
```

随后在 AstrBot WebUI 的插件管理页面重载或启用插件。

## 前置条件

1. AstrBot 版本满足 `>=4.24,<5`。
2. QQ 通过 AIOCQHTTP 适配器连接 NapCat/OneBot v11。
3. AstrBot 已配置可用的聊天模型。
4. 为获得稳定的主动参与判断，建议额外配置一个快速、便宜的决策模型。
5. 如需图片理解，需要选择支持图片输入的 Provider。

## 配置说明

所有配置都可以在 AstrBot WebUI 中编辑。

| 配置项 | 说明 | 默认值 |
|---|---|---|
| `enabled_groups` | 允许观察的群号；留空表示所有群 | `[]` |
| `aliases` | Bot 昵称和直接称呼 | 爱弥斯、小爱、飞行雪绒 |
| `decision_provider` | 判断是否主动插话的小模型 | 空 |
| `generation_provider` | 自主回复模型；留空时使用当前群模型 | 空 |
| `vision_provider` | 图片理解模型；留空时复用回复模型 | 空 |
| `persona_id` | AstrBot 人格；留空时使用内置爱弥斯预设 | 空 |
| `persona_prompt` | 可选的本地人格覆盖文本 | 空 |
| `history_limit` | 每群最近消息数量和补拉数量 | `100` |
| `decision_threshold` | 主动插话最低置信度 | `0.72` |
| `spontaneous_hourly_limit` | 每群每小时最多主动回复数 | `6` |
| `spontaneous_cooldown_seconds` | 两次主动回复的最短间隔 | `600` 秒 |
| `debounce_min_seconds` | 话题聚合等待下限 | `4` 秒 |
| `debounce_max_seconds` | 话题聚合等待上限 | `8` 秒 |
| `vision_enabled` | 是否允许按需理解图片 | `true` |
| `memory_retention_days` | 近期事件默认保留时间 | `30` 天 |

如果没有配置 `decision_provider`，插件不会为了主动插话强行调用未知模型；直接 `@`、回复 Bot 和 AstrBot 原生唤醒仍由 AstrBot 正常处理。

## 唤醒与指令兼容

插件区分三类回复路径：

1. **AstrBot 原生唤醒**：`@Bot`、回复 Bot、唤醒前缀。由 AstrBot 生成一次回复，Groupmate 只补充群聊上下文。
2. **插件直接唤醒**：例如“小爱，在吗”。由 Groupmate 立即进入人格回复流程。
3. **主动参与**：普通消息先经过话题聚合、频率控制和决策模型，只有达到阈值才回复。

已经注册的 AstrBot 或第三方插件指令会旁路 Groupmate，不会出现工具结果后又追加一条人格回复的情况。

## 管理命令

以下命令默认需要 AstrBot 管理员权限：

| 命令 | 作用 |
|---|---|
| `/groupmate_status` | 查看插件状态、已初始化群和等待任务 |
| `/groupmate_pause` | 暂停观察和自主回复 |
| `/groupmate_resume` | 恢复运行 |
| `/groupmate_reset` | 清空当前群的短期工作上下文 |

## 数据存储与隐私

运行数据默认保存在：

```text
AstrBot/data/plugin_data/astrbot_plugin_groupmate/groupmate.db
```

数据库包含有限消息记录、群友档案、近期事件、决策原因码和待发送消息。模型的隐式思维链不会写入数据库。

建议：

- 只在获得群管理者和成员知情的群中启用；
- 使用 `enabled_groups` 明确限制范围；
- 定期清理不再需要的数据库备份；
- 不要把数据库、日志或带有真实群号的配置提交到公开仓库；
- Provider 密钥继续由 AstrBot 管理，本插件不单独保存密钥。

## 架构概览

```text
NapCat/AstrBot 事件
        ↓
OneBot 消息规范化
        ↓
每群串行 Actor
        ↓
触发路由 → 话题聚合 → 记忆检索 → 决策门控
                                      ↓
                        人格生成 → 输出校验 → Outbox → 发送
```

领域模块不依赖 AstrBot，模型、视觉、历史、平台和存储通过端口注入。后续替换模型、存储后端或增加平台时，无需重写核心认知工作流。

完整设计见：

- [智能体设计规格](docs/superpowers/specs/2026-07-17-groupmate-agent-design.md)
- [实施计划](docs/superpowers/plans/2026-07-17-groupmate-agent.md)

## 测试

在插件仓库根目录运行：

```bash
pytest -q
python -m compileall -q main.py groupmate tests
python -m json.tool _conf_schema.json >/dev/null
```

核心测试不需要连接 AstrBot、NapCat 或真实 QQ 账号。

## 首版限制

- 仅支持 AIOCQHTTP/NapCat 群聊路径。
- 不分析语音和视频内容。
- 不包含向量数据库和无约束长期记忆。
- 不执行自主工具循环或多智能体协作。
- 现有学习素材只有 Bot 自身发言，能够评估语言风格，但无法评估真实触发准确率；后续需要完整群聊窗口做回放标注。

