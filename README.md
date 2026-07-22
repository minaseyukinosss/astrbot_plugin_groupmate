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
| `spontaneous_hourly_limit` | 每小时最多自主发言 | `6` |
| `spontaneous_cooldown_seconds` | 自主发言最短间隔 | `600` |
| `relationships` | id / relationship / address | 内置默认 |
| `vision_enabled` | 允许按需看图 | `true` |

防抖窗口、决策门槛、历史补拉条数、拟人延迟与分段数等为内部常量，不在配置页暴露。

## 唤醒路径

1. **直接唤醒**（立即回复；闲聊默认抑制 AstrBot 重复发言）
   - 平台 `@` / 回复 Bot
   - **句首别名**：消息以配置别名开头（`爱弥斯你在不`、`小爱，帮我看下`）
   - 显式召唤：`叫/喊/问问` + 别名
   - **例外**：`@` / 回复 Bot 若明显需要联网或外部事实（如「某某怎么了」「帮我查一下」），Groupmate 只观察、不回复，交回 AstrBot 默认 Agent（可使用 `web_search_tavily` 等工具），避免双回复也避免丢搜索能力
   - **复制的伪 @**：句首是纯文本 `@别名`（非平台有效 At 段）时，不走闲聊生成，提示「不能复制哦，复制的@为纯文本而非有效@」
2. **软提及**：别名只出现在句中/句尾（`我觉得爱弥斯挺难调`）→ 防抖后走决策，不保证回复
3. **续聊**：直接呼叫成功后，同一发送者在窗口内无需再叫名字
4. **主动插话**：普通候选经防抖 + 频率限制 + 决策模型，过门槛才说
5. **指令**：已有 AstrBot/插件指令旁路，不追加人格回复

句首点名按中文群聊惯例视为呼叫，不维护口语白名单（「在吗/在不/在么」等）。

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
