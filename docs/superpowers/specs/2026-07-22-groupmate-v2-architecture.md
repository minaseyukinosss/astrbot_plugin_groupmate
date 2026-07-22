# Groupmate 现行架构（精简核心）

日期：2026-07-22  
状态：**现行生效**  
前置：[`2026-07-17-groupmate-agent-design.md`](./2026-07-17-groupmate-agent-design.md)（已按本决议收敛）

## 1. 产品目标

1. 直接唤醒时可可靠聊天。
2. 能根据群话题适当加入，不刷屏。
3. 表现得像群聊伙伴：短、自然、有人格，而不是指令客服。

## 2. 明确舍弃（不再实现、不再文档化）

| 已舍弃 | 原因 |
|---|---|
| 表达学习 / style_hints | 易学噪音，冲击人格硬规则 |
| 反思合并 reflection | 假成长、漂移风险 |
| 发送后自动 LEARN 写回 | 短闭环伪记忆 |
| 疲劳 / presence 状态机 | 与小时限额+冷却重叠，难调 |
| 表情包 stickers | 资源与时机成本高，非核心 |
| 影子模式 / evaluation / WebUI 控制中心 | 运维评测，不直接服务拟人聊天 |

若要再加能力，须有真实群聊缺口证据，且不得重新膨胀为上述整包子系统。

## 3. 现行管线

```text
NapCat/AstrBot 群消息
        ↓
OneBot 规范化
        ↓
每群串行 Actor
        ↓
触发路由 → 话题窗口 / 防抖 / 续聊
        ↓
OBSERVE → RECALL → GATE → [VISION] → PLAN → GENERATE → GUARD → SCHEDULE → SEND
```

- **直接唤醒 / 续聊**：跳过决策模型，立即生成。直接唤醒含平台 `@`/回复、**句首别名**、显式召唤动词。
- **软提及 / 候选插话**：别名在句中或普通消息 → 防抖聚合 → 频率限制 → 决策模型过门槛才生成。
- **指令**：旁路，不追加人格回复。
- **失败默认沉默**（fail-closed）。
- **触发器不做口语穷举**：不维护「在吗/在不」类白名单；句首点名即呼叫。

无 LEARN / REFLECT / EMOJI / SHADOW 阶段。

## 4. 模块边界

`groupmate/` 不依赖 AstrBot。适配仅在 `astrbot_adapter.py` + `main.py`。

| 模块 | 职责 |
|---|---|
| `triggers` | 触发分类 |
| `topics` | 工作窗口 |
| `runtime` | 每群 Actor、防抖、续聊 |
| `workflow` | 认知管线 |
| `persona` / `relationships` | 人格与称呼 |
| `guardrails` | 输出硬约束 |
| `delivery` | 拟人延迟与分段 |
| `rate_limit` | 自主发言额度 |
| `memory` | 消息索引、可选记忆检索、轨迹、Outbox |
| `config` | 配置校验 |

## 5. 配置面（与 `_conf_schema.json` 一致）

对外只暴露：启用群、别名、原生唤醒接管、续聊秒数、模型 Provider、人格、自主发言限额与冷却、关系映射、看图开关。

防抖、`topic_max_seconds`、决策门槛、历史补拉条数、拟人延迟、分段数等为内部常量（见 `groupmate/config.py`），不进配置页；旧配置键会被忽略。

管理命令仅：`status` / `pause` / `resume` / `reset`。

## 6. 成功标准

- 指令不产生第二条 Groupmate 回复。
- `@`、回复 Bot、别名呼叫可靠；同人续聊自然。
- 主动插话有额度与门槛，不刷屏。
- 模型失败时沉默，不降级闲聊。
- 核心测试不依赖 AstrBot / NapCat / 网络。
