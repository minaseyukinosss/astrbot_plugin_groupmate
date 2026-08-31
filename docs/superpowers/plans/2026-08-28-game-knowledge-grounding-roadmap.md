# Groupmate 游戏知识认知与时效根据 Roadmap

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按可独立验收的四个子项目，把共享游戏语义、版本事实核验、根据回复和持续扩展接入 Groupmate，同时保证它不编造、不抢答、不跨群泄漏约定。

**Architecture:** 一个总设计约束四个串行交付面。先建立无网络的 schema v4、知识契约、四款 seed、本地解析和 SHADOW 评测；再接入公开事实、版本三轨和每日官方核验；随后接入 AstrBot 搜索、冻结快照和确定性事实回复；最后才开放 AMBIENT 即时搜索、非预装游戏学习与完整控制面。每个子项目在前一个子项目的公开接口上工作，不允许用临时自由搜索绕过后续安全边界。

**Tech Stack:** Python 3.11+、asyncio、SQLite/WAL、AstrBot 4.24–4.x AI ToolSet、JSON seed assets、pytest、原生 ES Modules

**Spec:** `docs/superpowers/specs/2026-08-27-world-knowledge-and-game-version-grounding-design.md`

## Global Constraints

- 不使用 git worktree；在当前工作目录实施，并保留所有无关未提交改动。
- 四份子计划必须按顺序执行；后续阶段不能把未完成的安全边界替换成 Prompt 约定。
- 知识业务键不包含 `persona_id`：稳定语义与公开事实全局共享，群约定只按 `group_id` 隔离。
- Member Profile、关系记忆、Persona Canon 和知识存储保持独立；检索查询禁止携带前三者的私密内容。
- Governor 仍是唯一行动授权边界；知识命中不能提高参与概率或制造新候选意图。
- 外部插件命令、自身输出、已知 Bot、未知自动化来源和转发默认不能强化知识。
- 版本、日期、清单、数值、官方状态和爆料状态没有新鲜证据时必须失败关闭。
- 网络 I/O 不能发生在全局或按群回复锁内；搜索后必须重验 scene、target、lease 和知识时效。
- 所有正式发送能力先经过 SHADOW；DIRECT / CONTINUATION 先于 AMBIENT 放量。

---

## 子项目与依赖

| 顺序 | 计划 | 交付后可独立证明的能力 | 正式发送状态 |
|---|---|---|---|
| 1 | `2026-08-28-game-knowledge-local-cognition.md` | 四款预装游戏和群约定可在参与判断前被本地理解，观察来源被正确隔离 | 仅 SHADOW，不使用知识生成事实回复 |
| 2 | `2026-08-28-game-version-public-facts.md` | 官方版本状态按天刷新，三轨状态、冲突与负向核验可审计 | 仍仅 SHADOW |
| 3 | `2026-08-28-game-knowledge-grounded-replies.md` | DIRECT / CONTINUATION 可有界即时核验，并只用冻结证据回答高风险事实 | SHADOW 通过后开放 DIRECT / CONTINUATION |
| 4 | `2026-08-28-game-knowledge-ambient-expansion.md` | AMBIENT 有界搜索、非预装游戏持续学习、人工纠正和完整运营能力 | 单群 canary 后逐步开放 |

依赖方向固定为：

```text
local contracts + schema v4 + resolver
  -> public claims + release state + official probe
    -> discovery search + enrichment + grounded reply
      -> ambient search + long-tail learning + control plane
```

禁止的反向依赖：

- `knowledge/` 域模块不能导入 `adapters/astrbot_*`；AstrBot 只实现 Port。
- seed 与 repository 不能导入 cognition、scene、reply executor。
- resolver 不能调用网络或写入 claim；它只读有效投影并输出冻结 frame。
- reply renderer 不能读取活库补充事实；它只消费本轮 `KnowledgeSnapshot`。

---

## 设计覆盖矩阵

| 设计章节 | 实施落点 |
|---|---|
| §4 四层知识、§10 持续学习、§11 四款 seed | 本地认知 Task 2–5；持续扩展 Task 2–3 |
| §5 TopicUnderstandingFrame、§7 同步链路、§8 通道策略 | 本地认知 Task 5–7；根据回复 Task 3–4；持续扩展 Task 1 |
| §9 持久化模型 | 本地认知 Task 2 一次性建 schema v4；后三阶段分别启用预建表的 repository API |
| §12 版本与时效 | 版本公开事实 Task 1–6 |
| §13 回复根据与不编造 | 根据回复 Task 1、5–7 |
| §14 搜索与网页安全 | 版本公开事实 Task 1、4；根据回复 Task 1–3 |
| §15 并发、预算、恢复、保留 | 根据回复 Task 3–4；持续扩展 Task 1–2、5 |
| §16 配置与控制面 | 本地认知 Task 7；持续扩展 Task 3–4 |
| §17 故障语义 | 四阶段固定 diagnostic code 与各自 SHADOW 场景 |
| §18 测试评估、§19 分阶段交付 | 四个 Gate、固定 corpus、installed-live SHADOW 与 canary |
| §20 非目标、§21 完成判定 | 本路线图 Global Constraints 与总体验证 |

---

## 跨阶段固定接口

四份计划共享以下公开类型；首次定义在子项目 1，后续只做加法扩展：

```python
@dataclass(frozen=True)
class TopicUnderstandingFrame:
    frame_id: str
    game_ids: tuple[str, ...]
    resolved_entities: tuple[ResolvedEntity, ...]
    resolved_terms: tuple[ResolvedTerm, ...]
    discourse_referents: tuple[DiscourseReferent, ...]
    version_reference: VersionReference | None
    conversation_intent_hint: str | None
    ambiguity_codes: tuple[str, ...]
    confidence: float
    supporting_knowledge_ids: tuple[str, ...]

@dataclass(frozen=True)
class KnowledgeNeed:
    outcome: Literal[
        "none", "local_sufficient", "fresh_evidence_required",
        "background_learning", "unresolvable",
    ]
    gap_codes: tuple[str, ...]
    entity_ids: tuple[str, ...]
    query_intents: tuple[str, ...]
    expires_at: int

@dataclass(frozen=True)
class KnowledgeSnapshot:
    snapshot_id: str
    topic_understanding_frame_id: str
    allowed_knowledge_facts: tuple[KnowledgeFact, ...]
    strict_fact_fragments: tuple[StrictFactFragment, ...]
    source_ids: tuple[str, ...]
    checked_at: int
    expires_at: int
    version_state_revision: int
```

类型一致性规则：所有 ID 都是非空 `str`；Unix 时间均为整数秒；有序输出使用 `tuple`；跨异步边界的对象均为 `frozen=True`；数据库枚举使用小写，社交动作枚举保持现有大写风格。

---

## 全局发布门

- [ ] **Gate 1 — 本地认知：** 四款固定理解集正确率 ≥95%，高置信歧义误归并 <1%，跨群约定泄漏为 0，本地解析 P95 <50ms。
- [ ] **Gate 2 — 版本事实：** 24 小时成功时间只在完整官方探测成功后更新；超时、部分失败和空结果不产生“官方没有”结论；三轨迁移和来源冲突全覆盖。
- [ ] **Gate 3 — 根据回复：** 无新鲜证据的高风险断言为 0，片段外数字/日期/版本/专名为 0，搜索后过期场景发送为 0，外部插件工具所有权回归通过。
- [ ] **Gate 4 — AMBIENT：** 非知识 AMBIENT 搜索为 0，搜索硬超时 ≤2s，额度与 single-flight 生效，单群 canary 无参与率显著抬升。

每个 Gate 使用固定 fixture、pytest 和人工 SHADOW 复核三类证据。未通过时只保留已通过阶段，不提前开放下一阶段发送。

---

## 总体验证

完成四份子计划后运行：

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest -q tests/evaluation/test_game_knowledge.py tests/scenarios/test_game_knowledge_shadow.py
.venv/bin/python -m pytest -q tests/scenarios/test_game_grounded_reply.py tests/scenarios/test_ambient_game_knowledge.py
```

Expected: 全量 pytest 和两组固定场景通过；断言覆盖 `unsupported_temporal_claims=0`、`cross_group_leaks=0`、`stale_scene_sends=0`，四款 seed 理解准确率不低于 0.95。

最后执行：

```bash
git diff --check
git status --short
```

Expected: `git diff --check` 无输出；`git status --short` 只包含本路线图授权的改动和实施前已经存在的用户文件。
