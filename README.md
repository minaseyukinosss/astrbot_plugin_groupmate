# AstrBot Groupmate 群聊伙伴插件

基于 **Companion Core** 的 AstrBot 群聊伙伴：观察 QQ 群、按开口契约决定是否说话，并以固定爱弥斯 Persona Pack 自然短回复。

面向 **AstrBot 4.24+** 与 **NapCat/OneBot v11**。

## 当前发布

- 版本：`0.3.0`
- V3 迁移：Phase 0-5 已落地并有测试覆盖；Phase 6+（能力层、表情包、多模态、True Proactive）尚未开始。
- 发布包只包含运行时、Pages、配置、README 和规格文档；不包含 `.git`、`.venv`、测试、离线评测语料或缓存。

## 目录结构

```text
astrbot_plugin_groupmate/
├── main.py
├── metadata.yaml / _conf_schema.json
├── docs/superpowers/
├── groupmate/
│   ├── core/                 # 装配 / 投影 / 归属 / Session / SILENCE / 口吻
│   ├── persona/aemeath/      # Persona Pack（persona / constraints / voice）
│   ├── engine/               # workflow / runtime / opportunity / delivery …
│   ├── memory/               # SQLite ledger / 投影 / 记忆候选 / 隐私仲裁
│   ├── social/               # 社会事件与关系投影
│   └── host/                 # AstrBot 适配（bridge / onebot / llm）
└── tests/                    # 仓库测试；发布包不包含
```

## 核心能力

- 稳定 system + 固定顺序动态 user
- 每轮 voice_anchor 与五档好感关系姿态；可召回自我情景
- SpeakContract（`<SILENCE>`）；OutputFirewall
- 每群 Actor：非阻塞观察、防抖、直接唤醒、续聊
- 统一 Delivery / Outbox：正常回复、copied-at、发送失败和未知回执同一路径
- 多人归属：回复目标、记忆主体、社会状态目标分离
- 保守长期记忆：候选抽取、敏感拦截、authority 仲裁、TTL、删除 tombstone
- 管理命令：status / pause / resume / reset

刻意不包含：工具环全家桶、Kanban、心跳巡检、表达自学习。

## 安装

将插件目录放入 `AstrBot/data/plugins/astrbot_plugin_groupmate/`。

至少包含：`main.py`、`metadata.yaml`、`_conf_schema.json`、`groupmate/`。

仓库根目录的 `astrbot_plugin_groupmate-v0.3.0-clean.zip` 是干净安装包，可直接解压到 AstrBot 插件目录。

## 配置

| 配置项 | 说明 | 默认 |
|---|---|---|
| `wake_group.enabled_groups` | 允许观察的群；空=全部 | `[]` |
| `wake_group.aliases` | 直接称呼 | 爱弥斯、小爱、飞行雪绒 |
| `wake_group.handle_native_wake` | Groupmate 接管 `@`/回复 Bot | `true` |
| `wake_group.continuation_seconds` | 直接呼叫后同人续聊秒数 | `90` |
| `persona_group.group_brief` | 一句话群氛围（进稳定 system） | 空 |
| `persona_group.max_reply_chars` | 单条回复字数护栏 | `60` |
| `relationship_group.relationships` | QQ / 关系 / 称呼（可视化增删） | 内置默认 |
| `provider_group.generation_provider` | 回复模型；空=当前群模型 | 空 |
| `provider_group.vision_provider` | 看图模型；空=复用回复模型 | 空 |
| `provider_group.vision_enabled` | 允许按需看图 | `true` |
| `limits_group.spontaneous_hourly_limit` | 每小时最多自主发言 | `6` |
| `limits_group.spontaneous_cooldown_seconds` | 自主发言最短间隔 | `600` |

## 配置入口

1. **插件 Pages（本插件页）**：WebUI → 插件 →「群聊伙伴 Groupmate」详情 → 打开 **settings** 页（运行状态 / 暂停恢复 / 当前配置摘要）。
2. **可视化配置（齿轮）**：同一插件详情里的 **配置**，改别名、群氛围、关系表、字数护栏等（`_conf_schema.json`）。身份固定为爱弥斯。

改完 schema 或 `pages/` 后请在 WebUI **重载插件**；仅改静态页通常刷新即可。

## 唤醒路径

1. **直接唤醒**：`@` / 回复 Bot / 句首别名 → 立即生成（可 SILENCE）
2. **软提及 / 候选**：`ParticipationDecisionEngine（统一参与决策引擎）`按场景判断；路过提名、复读和无参与动机消息直接沉默，明确群体求助才允许短答
3. **续聊**：窗口内同人；Session 注入近轮对话
4. **指令**：旁路
5. **联网例外**：交回 AstrBot Agent

## V3 回滚开关

以下开关默认开启，仅用于故障定位或阶段回滚：

| 配置项 | 回退行为 |
|---|---|
| `wake_group.v3_scheduler_enabled` | 关闭 V3 非阻塞调度，回退旧串行调度 |
| `wake_group.v3_memory_writer_enabled` | 停止接受记忆候选，仅只读既有 memories |

## 管理命令

| 命令 | 作用 |
|---|---|
| `/groupmate_status` | 运行状态 |
| `/groupmate_pause` | 暂停观察 |
| `/groupmate_resume` | 恢复 |
| `/groupmate_reset` | 清空当前群短期上下文与 Session |

## 规格

见 [`docs/superpowers/specs/2026-07-24-companion-core.md`](docs/superpowers/specs/2026-07-24-companion-core.md)。

V3 目标架构与分阶段实施门槛见
[`docs/superpowers/plans/2026-07-24-groupmate-humanlike-roadmap.md`](docs/superpowers/plans/2026-07-24-groupmate-humanlike-roadmap.md)。

## 离线评测

Phase 0 提供 120 条脱敏场景以及 deterministic/OpenAI-compatible 两种运行模式：

```bash
python3 -m eval.runner \
  --mode deterministic \
  --output eval/results/baseline.json
```

真实模型环境变量、评分字段、成本和数据治理要求见
[`eval/README.md`](eval/README.md)。评测系统不连接生产数据库，模型结果目录默认不提交 Git。
