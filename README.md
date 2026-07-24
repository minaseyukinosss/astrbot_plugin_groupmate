# AstrBot Groupmate 群聊伙伴插件

基于 **Companion Core** 的 AstrBot 群聊伙伴：观察 QQ 群、按开口契约决定是否说话，并以 Persona Pack（默认爱弥斯）自然短回复。

面向 **AstrBot 4.24+** 与 **NapCat/OneBot v11**。

## 目录结构

```text
astrbot_plugin_groupmate/
├── main.py
├── metadata.yaml / _conf_schema.json
├── docs/superpowers/specs/
├── groupmate/
│   ├── core/                 # 装配 / Session / SILENCE / 口吻 / 情绪
│   ├── persona/aemeath/      # Persona Pack（persona / constraints / voice / moods）
│   ├── engine/               # workflow / runtime / triggers / topics …
│   ├── memory/               # SQLite 记忆存储
│   └── host/                 # AstrBot 适配（bridge / onebot / llm）
└── tests/
```

## 核心能力

- 稳定 system + 固定顺序动态 user
- 每轮 voice_anchor、mood、好感档位关系行；可召回自我情景
- SpeakContract（`<SILENCE>`）；OutputFirewall
- 每群 Actor：防抖、直接唤醒、续聊
- 管理命令：status / pause / resume / reset

刻意不包含：工具环全家桶、Kanban、心跳巡检、表达自学习。

## 安装

将插件目录放入 `AstrBot/data/plugins/astrbot_plugin_groupmate/`。

至少包含：`main.py`、`metadata.yaml`、`_conf_schema.json`、`groupmate/`。

## 配置

| 配置项 | 说明 | 默认 |
|---|---|---|
| `wake_group.enabled_groups` | 允许观察的群；空=全部 | `[]` |
| `wake_group.aliases` | 直接称呼 | 爱弥斯、小爱、飞行雪绒 |
| `wake_group.handle_native_wake` | Groupmate 接管 `@`/回复 Bot | `true` |
| `wake_group.continuation_seconds` | 直接呼叫后同人续聊秒数 | `90` |
| `persona_group.character_name` | 展示名（Session / 口吻） | 爱弥斯 |
| `persona_group.group_brief` | 一句话群氛围（进稳定 system） | 空 |
| `persona_group.max_reply_chars` | 单条回复字数护栏 | `60` |
| `persona_group.persona_id` | 【高级】AstrBot 人格 | 空 |
| `persona_group.persona_prompt` | 【高级】整份人格覆盖文本 | 空 |
| `relationship_group.relationships` | QQ / 关系 / 称呼（可视化增删） | 内置默认 |
| `provider_group.generation_provider` | 回复模型；空=当前群模型 | 空 |
| `provider_group.vision_provider` | 看图模型；空=复用回复模型 | 空 |
| `provider_group.vision_enabled` | 允许按需看图 | `true` |
| `limits_group.spontaneous_hourly_limit` | 每小时最多自主发言 | `6` |
| `limits_group.spontaneous_cooldown_seconds` | 自主发言最短间隔 | `600` |

## 配置入口

1. **插件 Pages（本插件页）**：WebUI → 插件 →「群聊伙伴 Groupmate」详情 → 打开 **settings** 页（运行状态 / 暂停恢复 / 当前配置摘要）。
2. **可视化配置（齿轮）**：同一插件详情里的 **配置**，改别名、人格、关系表、字数护栏等（`_conf_schema.json`）。

人格优先级：`persona_prompt`（非空）> `persona_id` > 内置 `persona/aemeath/` Pack。

改完 schema 或 `pages/` 后请在 WebUI **重载插件**；仅改静态页通常刷新即可。

## 唤醒路径

1. **直接唤醒**：`@` / 回复 Bot / 句首别名 → 立即生成（可 SILENCE）
2. **软提及 / 候选**：防抖 + 额度 → 主生成；`<SILENCE>` 则不发送
3. **续聊**：窗口内同人；Session 注入近轮对话
4. **指令**：旁路
5. **联网例外**：交回 AstrBot Agent

## 管理命令

| 命令 | 作用 |
|---|---|
| `/groupmate_status` | 运行状态 |
| `/groupmate_pause` | 暂停观察 |
| `/groupmate_resume` | 恢复 |
| `/groupmate_reset` | 清空当前群短期上下文与 Session |

## 规格

见 [`docs/superpowers/specs/2026-07-24-companion-core.md`](docs/superpowers/specs/2026-07-24-companion-core.md)。
