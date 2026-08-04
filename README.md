# AstrBot Groupmate 群聊伙伴插件

基于 **Companion Core** 的 AstrBot 群聊伙伴：观察 QQ 群、按开口契约决定是否说话，并以固定爱弥斯 Persona Pack 自然短回复。

面向 **AstrBot 4.24+** 与 **NapCat/OneBot v11**。

## 当前发布

- 版本：`0.3.0`
- V3 核心迁移、宿主命令隔离、静态 CapabilityProvider SPI 和默认关闭的 HostEventAdapter Phase B 已落地并有测试覆盖；具体外部插件 Integration Adapter 仍属于 Phase C，Tool Gateway 不在当前实现范围。
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
│   ├── capabilities/         # 类型化能力契约 / Registry / 内置 Provider
│   └── host/                 # AstrBot 适配（gate / event adapters / bridge / onebot / llm）
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
- 静态 CapabilityProvider 生命周期、启动健康采样与 Bridge 统一装配
- 静态 HostEventAdapter：默认关闭的 AIOCQHTTP 戳一戳适配与显式互动语义

刻意不包含：工具环全家桶、Kanban、心跳巡检、表达自学习。

## 安装

将插件目录放入 `AstrBot/data/plugins/astrbot_plugin_groupmate/`。

至少包含：`main.py`、`metadata.yaml`、`_conf_schema.json`、`groupmate/`。

仓库根目录的 `astrbot_plugin_groupmate-v0.3.0-clean.zip` 是干净安装包，可直接解压到 AstrBot 插件目录。

## 配置

| 配置项 | 说明 | 默认 |
|---|---|---|
| `scope_group.enabled_groups` | 启用群列表；空列表表示所有群 | `[]` |
| `persona_group.persona_aliases` | 按人格配置文本称呼；显式空列表不会补回默认称呼 | `aemeath: [爱弥斯, 小爱, 飞行雪绒]` |
| `persona_group.relationships` | 按人格配置初始关系；只影响尚无关系状态的群友 | `aemeath: []` |
| `provider_group.generation_provider` | 回复模型；空=当前群模型 | 空 |
| `provider_group.vision_enabled` | 允许按需看图 | `true` |
| `provider_group.vision_provider` | 看图模型；空=复用最终文本模型 | 空 |
| `interaction_group.poke_enabled` | 接管 AIOCQHTTP 戳一戳（戳 Bot 必回应路径；他人互戳可跟风观察） | `false` |
| `interaction_group.poke_back_enabled` | 允许回戳 / 跟风戳出站；关闭时仅可能文字回应 | `false` |

当前只注册 `aemeath` 人格，管理界面不提供未实现的人格切换入口。称呼和初始关系必须放在 `aemeath` 键下；旧扁平配置和未知字段不会参与运行时，并会在状态页的配置健康信息中报告。

文本模型优先使用显式 `generation_provider`；只有留空时才读取当前群模型。图片理解关闭时不调用视觉模型；开启后优先使用显式 `vision_provider`，留空则复用已解析的文本模型。

启用 `interaction_group.poke_enabled` 表示管理员选择 Groupmate 作为戳一戳的预期最终
回复所有者。若另一个插件也会直接回复同一戳一戳，必须关闭其回复处理器，或将其切换为
不发送消息的 service-only 模式；Groupmate 不自动探测或关闭第三方插件。
开启 `poke_back_enabled` 后才会对平台发出戳一戳（回戳与跟风）；冷却、概率沉默与暴戳语气由代码内置策略控制。

## 状态归属与数据库升级

- `schema v11（人格隔离数据库版本）` 为消息、话题、续聊、记忆、关系、决策与投递状态增加显式人格归属。同一群和同一用户在不同人格下不会共享短期窗口或长期状态。
- `aemeath（爱弥斯人格 ID）` 拥有从受支持旧版本迁移的全部既有数据。未来注册其他人格时，新人格从独立空状态开始，不读取或修改 `aemeath` 的状态。
- 升级已有数据库前会在原目录创建 `groupmate.db.pre-migrate-v<旧版>-to-v11.<时间戳>（迁移前备份）`。数据库与备份默认位于 `AstrBot/data/plugin_data/astrbot_plugin_groupmate/（插件数据目录）`。
- 如需恢复，先停止或卸载插件，保留当前 `groupmate.db` 供排查，再将选定的迁移前备份复制为 `groupmate.db`。重新加载当前插件会再次执行 v11 迁移；如需停留在旧 schema，应同时恢复与该 schema 匹配的旧插件版本。

## 配置入口

1. **插件 Pages（本插件页）**：WebUI → 插件 →「群聊伙伴 Groupmate」详情 → 打开 **settings** 页（运行状态 / 暂停恢复 / 当前配置摘要）。
2. **可视化配置（齿轮）**：同一插件详情里的 **配置**，设置启用群、人格称呼、初始关系和模型 Provider（`_conf_schema.json`）。当前人格固定为爱弥斯。

改完 schema 或 `pages/` 后请在 WebUI **重载插件**；仅改静态页通常刷新即可。

## 唤醒路径

1. **真实直接唤醒**：平台 `@` / 回复 Bot / 句首别名进入 `DIRECT_REQUIRED（必须回应）`；模型失败、预算耗尽或返回 `<SILENCE>` 时由 `DirectFallbackComposer（直接回应降级组装器）`给出爱弥斯短句
2. **复制文本 `@`**：`CopiedAtGuard（复制文本 @ 旁路）`固定回复“复制出来的 @ 不算数哦，要叫爱弥斯的话，用真正的 @。”，不进入参与、好感、过度 `@` 或续聊决策
3. **软提及 / 候选**：`ParticipationDecisionEngine（统一参与决策引擎）`按场景判断；路过提名、复读和无参与动机消息直接沉默，明确群体求助才允许短答
4. **续聊**：窗口内同人；Session 注入近轮对话
5. **AstrBot 指令**：已注册命令和使用宿主唤醒前缀的输入在进入 Groupmate Actor 前旁路；不写入话题、记忆或 outbox，也不阻止其他插件处理
6. **宿主互动**：配置开启后，AIOCQHTTP 戳一戳以 `SYSTEM_SYNTHETIC` / `HOST_INTERACTION` 进入同一 Actor、Persona、Firewall、Delivery 和 Outbox 链路；戳 Bot 走冷却 / 概率沉默 / 暴戳压力；他人互戳可按策略跟风；`poke_back_enabled` 时才出站回戳。不引用伪造文本，不创建长期人物记忆或续聊授权
7. **联网例外**：交回 AstrBot Agent

## 管理命令

| 命令 | 作用 |
|---|---|
| `/groupmate_status` | 运行状态 |
| `/groupmate_pause` | 暂停决策与回复，继续观察并记录群消息 |
| `/groupmate_resume` | 恢复 |
| `/groupmate_reset` | 清空当前群短期上下文与 Session |

## 规格

见 [`docs/superpowers/specs/2026-07-24-companion-core.md`](docs/superpowers/specs/2026-07-24-companion-core.md)。

AstrBot 其他插件命令共存、Groupmate 内部 Capability Provider 扩展和未来外部能力
接入边界见
[`docs/superpowers/specs/2026-07-31-host-command-capability-boundary-design.md`](docs/superpowers/specs/2026-07-31-host-command-capability-boundary-design.md)。

内部能力通过 `CapabilityManifest`、`CapabilityContext` 和 `CapabilityGovernor` 显式治理。Provider 只能返回结构化事实、媒体候选或 handoff 状态；最终表达和发送仍由人格、OutputFirewall、Composer 和 DeliveryService 统一处理。

静态 CapabilityProvider Phase A、HostEventAdapter Phase B 与未来 Phase C 的具体外部插件
Integration Adapter 边界见
[`docs/superpowers/specs/2026-07-31-groupmate-extension-spi-design.md`](docs/superpowers/specs/2026-07-31-groupmate-extension-spi-design.md)
和
[`docs/superpowers/specs/2026-08-03-host-event-adapter-phase-b-design.md`](docs/superpowers/specs/2026-08-03-host-event-adapter-phase-b-design.md)。

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
