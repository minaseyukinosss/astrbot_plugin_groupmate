# AstrBot Groupmate 群聊伙伴插件

Groupmate 让 AstrBot 观察 QQ 群聊、判断何时适合加入，并按人格自然短回复。

面向 **AstrBot 4.24+** 与 **NapCat/OneBot v11**，默认内置爱弥斯人格。

## 核心能力（精简）

只保留对拟人化有直接帮助的部分：

- 每群串行 Actor：话题防抖、直接唤醒立即回复、续聊窗口
- 触发路由：原生 `@`/回复 Bot、别名呼叫、续聊、候选插话、指令旁路
- 决策门控 + 人格生成 + Guard + 拟人延迟/分段发送
- 关系称呼映射、可选按需看图、工作记忆检索（不自动“伪学习”）
- 管理命令：status / pause / resume / reset

刻意不包含：表达学习、反思合并、疲劳状态机、表情包、影子评测 WebUI。

## 安装

将插件目录放入：

```text
AstrBot/data/plugins/astrbot_plugin_groupmate/
```

至少包含 `main.py`、`metadata.yaml`、`_conf_schema.json`、`groupmate/`、`resources/`。

## 配置

| 配置项 | 说明 | 默认 |
|---|---|---|
| `enabled_groups` | 允许观察的群；空=全部 | `[]` |
| `aliases` | 直接称呼 | 爱弥斯、小爱、飞行雪绒 |
| `handle_native_wake` | Groupmate 接管 `@`/回复 Bot | `true` |
| `continuation_seconds` | 直接呼叫后同人续聊秒数 | `90` |
| `decision_provider` | 自主插话判断模型 | 空 |
| `generation_provider` | 回复模型；空=当前群模型 | 空 |
| `vision_provider` | 看图模型；空=复用回复模型 | 空 |
| `persona_id` / `persona_prompt` | AstrBot 人格或本地覆盖 | 空 |
| `history_limit` | 工作记忆/补拉条数 | `100` |
| `decision_threshold` | 自主插话最低置信度 | `0.72` |
| `spontaneous_hourly_limit` | 每小时最多自主发言 | `6` |
| `spontaneous_cooldown_seconds` | 自主发言最短间隔 | `600` |
| `debounce_min/max_seconds` | 话题聚合等待 | `4` / `8` |
| `topic_max_seconds` | 单话题最长收集窗口 | `12` |
| `humanize_delay_enabled` | 发送前拟人短延迟 | `true` |
| `max_reply_segments` | 最多拆成几段短消息 | `2` |
| `relationships` | id / relationship / address | 内置默认 |
| `vision_enabled` | 允许按需看图 | `true` |

## 唤醒路径

1. **直接唤醒**：`@`、回复 Bot、别名呼叫 → 立即回复（默认抑制 AstrBot 重复发言）
2. **续聊**：直接呼叫成功后，同一发送者在窗口内无需再叫名字
3. **主动插话**：候选消息经防抖 + 频率限制 + 决策模型，过门槛才说
4. **指令**：已有 AstrBot/插件指令旁路，不追加人格回复

## 管理命令

| 命令 | 作用 |
|---|---|
| `/groupmate_status` | 运行状态 |
| `/groupmate_pause` | 暂停观察 |
| `/groupmate_resume` | 恢复 |
| `/groupmate_reset` | 清空当前群短期上下文 |

## 数据

```text
AstrBot/data/plugin_data/astrbot_plugin_groupmate/groupmate.db
```

保存有限消息索引、记忆条目、决策轨迹与 Outbox。建议用 `enabled_groups` 限制范围，勿把库文件提交公开仓库。

## 测试

```bash
cd astrbot_plugin_groupmate
PYTHONPATH=. pytest -q
```

设计文档：

- [现行精简架构](docs/superpowers/specs/2026-07-22-groupmate-v2-architecture.md)
- [智能体设计规格](docs/superpowers/specs/2026-07-17-groupmate-agent-design.md)
