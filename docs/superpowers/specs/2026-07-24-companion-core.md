# Companion Core 框架规格（Phase 1 + 拟人化上下文体系）

日期：2026-07-24  
状态：**现行生效**  
前置：[`2026-07-22-groupmate-v2-architecture.md`](./2026-07-22-groupmate-v2-architecture.md)

## 1. 目标

在 AstrBot 宿主上建设**宿主无关**的群聊伙伴运行时（Companion Core），支撑拟人化群聊产品。第一产品为爱弥斯（Aemeath）。

向早柚 AI Core **学习纪律，不照搬体量**：稳定/动态装配、口吻锚点、情绪进 user、关系行、自我情景；不迁入 tools / Kanban / Heartbeat / budget。

## 2. 目录约定

```text
groupmate/
├── models.py / config.py / ports.py
├── core/                       # 宿主/人格无关
│   ├── context_assembly.py     # 唯一装配入口
│   ├── speak_contract.py
│   ├── session.py
│   ├── relationships.py
│   ├── history_format.py
│   ├── voice_anchor.py
│   ├── mood.py
│   └── self_episodes.py
├── persona/aemeath/
│   ├── persona.md
│   ├── constraints.md
│   ├── voice_anchor.md
│   ├── moods.md
│   ├── memory_guide.md
│   ├── provider.py
│   ├── output_firewall.py
│   └── relationships.py
├── engine/                     # 调度管线
│   ├── workflow.py / runtime.py / triggers.py / topics.py
│   ├── delivery.py / rate_limit.py / external_knowledge.py
├── memory/store.py
└── host/                       # bridge / onebot / llm
```

规则：

- Core 不依赖 AstrBot，也不依赖 `persona/`。
- 新人设 = 新 `persona/<name>/`，由 Host 注入。
- 不保留兼容门面。

## 3. 装配契约

**System（稳定）**：ROLE_START → persona.md → constraints → group_brief → 收尾钉  
（不含 mood / per-user 关系）

**User（动态，顺序锁定）**：

```text
recent_messages → session_turns → mood → relationship_line → voice_anchor
→ self_episodes → relevant_memories → memory_guide → speak_note → reply_task
```

常量：`core.context_assembly.DYNAMIC_BLOCK_ORDER`（测试锁定）。

## 4. 能力与验收

| 模块 | 行为 |
|------|------|
| GroupSession | 近轮 user/assistant；character_name 注入 |
| SpeakContract | `<SILENCE>` 可不发送 |
| VoiceAnchor | 每轮从 Pack 注入；口吻≠行为 |
| Mood | 括号内心状态，只进 user |
| Favorability | 早柚三档：[-100,-1] 厌恶/警惕；[0,49] 陌生/社交距离；[50,100] 熟人/亲昵。只进 user；框架加减分 |
| Self episodes | 回指「你之前」时召回 assistant 轮 |
| OutputFirewall | 产品实现 `OutputGuard`（`AemeathOutputFirewall`） |

验收：

- 任意回复可指出稳定/动态各块。
- Core 源码无产品名硬编码。
- 软触发 SILENCE 不发送；续聊含自己上一句。
- 好感档位注入 relationship_line；发送/冒犯后分数按规则变化。
- `pytest` 全绿。

## 5. 非目标

Kanban、Heartbeat、自动 LEARN、完整工具环、迁 gsuid_core、向量 RAG、把好感做成可刷榜的数值游戏（对用户暴露分数）。
