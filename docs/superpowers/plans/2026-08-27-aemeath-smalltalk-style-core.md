# 爱弥斯闲聊风格主链实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将已获准的群聊回复改造成“具体场景—爱弥斯立场—社交动作—自然文本”的可执行风格体系，并覆盖主动互动、关系差异、画像记忆、复读靶心分流和成品社交检查。

**Architecture:** 保留 Ownership、Participation、Governor、Persona Canon、关系仓库和 Outbox；命令归属完成后，`SocialRuntimeManager` 先用 `ChorusDetector` 固化复读链证据并交给 Participation 与 Governor，再由 AstrBot 桥接层对获准评价执行结构化场景解析。纯函数策略随后形成 `StanceDecision` 和 `SocialMovePlan`；普通回复由真实 `StyleDirector` 与 `ReplyExecutor` 完成，`JOIN_CHORUS` 则使用冻结原文的确定性通道，两者都依次通过 `SocialOutputReviewer` 与现有 `OutputFirewall`。

**Tech Stack:** Python 数据类与 Enum、asyncio、现有 SQLite 仓库、AstrBot 模型端口、pytest。

**Spec:** `docs/superpowers/specs/2026-08-27-aemeath-smalltalk-style-system-design.md`

## Global Constraints

- 全局默认身份保持“爱弥斯”；Persona Canon 是名称、现实、经历和能力事实的唯一权威来源。
- 不复制小维的自称、经历、专属关系、固定句式或词频。
- 精确命令和错字命令不进入本计划的闲聊场景；它们由后续命令计划处理。
- 关系影响态度、意愿、投入和主动性，但绝不授予权限。
- 无具体事件、画像或记忆证据时不得编造旧事。
- 生成提示不得包含“先接住”“人格化补充”“续聊接口”“关系尾钩”等抽象任务。
- 复读内容指向爱弥斯时只能做群体回应；指向群友时只有安全、关系相容的复读才能原样加入。
- 单人刷屏、爱弥斯自身输出、命令、插件结果、链接、媒体和平台 mention 段不得构成可加入的复读链。
- `JOIN_CHORUS` 每条链最多一次，不调用文本模型，但仍经过社交审查、防火墙、幂等和群级冷却。
- 每个实现任务先写失败测试，随后做最小实现、运行定向测试并独立提交。

---

## 文件职责

- 新建 `groupmate/social_runtime/social_context.py`：相关性驱动的场景上下文与预算构建。
- 新建 `groupmate/social_runtime/chorus.py`：确定性复读链检测、链 ID、冻结原文和参与者证据。
- 新建 `groupmate/social_runtime/social_scenes.py`：场景枚举、冻结契约、模型端口和严格解析器。
- 新建 `groupmate/social_runtime/stances.py`：权限只读输入与关系驱动的爱弥斯立场、意愿策略。
- 新建 `groupmate/social_runtime/social_moves.py`：带来源的 `DecisionFact` 与具体社交动作规划。
- 新建 `groupmate/social_runtime/social_review.py`：`RealizedReply` 解析和社交一致性检查。
- 修改 `groupmate/social_runtime/actions/style.py`：让场景、立场和动作参与样式选择。
- 修改 `groupmate/social_runtime/replying.py`：持久化新决策、生成结构化成品、执行两级检查。
- 修改 `groupmate/social_runtime/expression.py`：仅保留旧计划解码与追踪兼容，不再主导生成。
- 修改 `groupmate/social_runtime/manager.py`：提供冻结关系投影和结构化画像、记忆上下文。
- 修改 `groupmate/adapters/astrbot_bridge.py`：编排场景解析、立场、动作、样式、预览和执行。
- 修改 `groupmate/social_runtime/control/message_traces.py`：记录安全、可读的新决策摘要。
- 新建 `tests/social_runtime/test_social_context.py`、`test_chorus.py`、`test_social_scenes.py`、`test_stances.py`、`test_social_moves.py`。
- 新建 `tests/social_runtime/actions/test_social_review.py`。
- 修改 `tests/social_runtime/actions/test_style.py`、`test_replying.py`、`tests/contracts/test_message_traces.py`。
- 新建 `tests/scenarios/test_aemeath_smalltalk_style.py`，修改 `tests/scenarios/test_chat_mainline.py`。

### Task 1: 建立冻结的场景、立场和动作契约

**Files:**
- Create: `groupmate/social_runtime/social_scenes.py`
- Create: `groupmate/social_runtime/stances.py`
- Create: `groupmate/social_runtime/social_moves.py`
- Create: `tests/social_runtime/test_social_scenes.py`
- Create: `tests/social_runtime/test_stances.py`
- Create: `tests/social_runtime/test_social_moves.py`

**Interfaces:**
- Consumes: `SocialEventEnvelope` 事件 ID 和现有关系投影字段。
- Produces: `SocialScene`, `ChorusTarget`, `ChorusTone`, `PermissionSnapshot`, `StanceDecision`, `DecisionFact`, `SocialMovePlan`, `RealizationMode`。

- [ ] **Step 1: 写入失败的契约测试**

```python
def test_social_scene_requires_concrete_subject_and_bounded_confidence():
    with pytest.raises(ValueError, match="literal_subject"):
        SocialScene.create(
            scene_kind="technical_help",
            target_scope="INDIVIDUAL",
            target_id="u1",
            literal_subject="",
            user_move="asks_for_diagnosis",
            continuity_event_ids=("m1",),
            confidence=0.9,
        )


def test_decision_fact_keeps_source_evidence():
    fact = DecisionFact.create(
        category="required_input",
        text="需要报错首段和版本号",
        source_event_ids=("m1",),
    )
    assert fact.fact_id.startswith("decision-fact:")
    assert fact.source_event_ids == ("m1",)


def test_social_move_question_requires_a_real_information_gap():
    with pytest.raises(ValueError, match="question ending"):
        SocialMovePlan(
            primary_move=SocialMove.DIRECT_ANSWER,
            secondary_move=None,
            must_say=(), may_say=(), must_not_say=(),
            mention_event_ids=(), ask_for=(),
            ending=Ending.QUESTION, media_intent=MediaIntent.NONE,
        )


def test_join_chorus_requires_frozen_payload_and_exact_mode():
    with pytest.raises(ValueError, match="verbatim_payload"):
        SocialMovePlan(
            primary_move=SocialMove.JOIN_CHORUS,
            secondary_move=None,
            must_say=(), may_say=(), must_not_say=(),
            mention_event_ids=("m1", "m2"), ask_for=(),
            ending=Ending.STOP, media_intent=MediaIntent.NONE,
            realization_mode=RealizationMode.EXACT_CHORUS,
            verbatim_payload=None,
        )


def test_self_targeted_chorus_keeps_evidence_but_not_member_id():
    scene = SocialScene.create(
        scene_kind="group_chorus",
        target_scope="GROUP",
        target_id=None,
        literal_subject="爱弥斯",
        user_move="chorus_about_self",
        continuity_event_ids=("m1", "m2"),
        repetition_count=2,
        chorus_target="SELF",
        chorus_target_id=None,
        chorus_chain_id="chorus:abc",
        chorus_payload="爱弥斯又嘴硬",
        chorus_event_ids=("m1", "m2"),
        chorus_participant_ids=("u1", "u2"),
        chorus_tone="SAFE_BANTER",
        confidence=0.95,
    )
    assert scene.chorus_target is ChorusTarget.SELF
    assert scene.chorus_target_id is None
```

- [ ] **Step 2: 运行契约测试并确认红灯**

Run: `pytest -q tests/social_runtime/test_social_scenes.py tests/social_runtime/test_stances.py tests/social_runtime/test_social_moves.py`

Expected: FAIL during collection because the three modules do not exist.

- [ ] **Step 3: 实现不可变契约与校验**

```python
class TargetScope(str, Enum):
    INDIVIDUAL = "INDIVIDUAL"
    GROUP = "GROUP"
    AMBIENT = "AMBIENT"


class ChorusTarget(str, Enum):
    NONE = "NONE"
    SELF = "SELF"
    MEMBER = "MEMBER"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class ChorusTone(str, Enum):
    NONE = "NONE"
    SAFE_BANTER = "SAFE_BANTER"
    SENSITIVE = "SENSITIVE"
    ATTACK = "ATTACK"
    DANGEROUS = "DANGEROUS"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class SocialScene:
    scene_kind: str
    target_scope: TargetScope
    target_id: str | None
    literal_subject: str
    user_move: str
    continuity_event_ids: tuple[str, ...]
    repetition_count: int = 0
    chorus_target: ChorusTarget = ChorusTarget.NONE
    chorus_target_id: str | None = None
    chorus_chain_id: str | None = None
    chorus_payload: str | None = None
    chorus_event_ids: tuple[str, ...] = ()
    chorus_participant_ids: tuple[str, ...] = ()
    chorus_already_joined: bool = False
    chorus_tone: ChorusTone = ChorusTone.NONE
    constraints: tuple[str, ...] = ()
    information_gaps: tuple[str, ...] = ()
    capability_request: str = "NONE"
    confidence: float = 0.0

    @classmethod
    def create(cls, **values: object) -> "SocialScene":
        normalized = dict(values)
        normalized["target_scope"] = TargetScope(normalized["target_scope"])
        normalized["continuity_event_ids"] = tuple(normalized.get("continuity_event_ids", ()))
        normalized["chorus_target"] = ChorusTarget(normalized.get("chorus_target", "NONE"))
        normalized["chorus_tone"] = ChorusTone(normalized.get("chorus_tone", "NONE"))
        normalized["chorus_event_ids"] = tuple(normalized.get("chorus_event_ids", ()))
        normalized["chorus_participant_ids"] = tuple(normalized.get("chorus_participant_ids", ()))
        normalized["constraints"] = tuple(normalized.get("constraints", ()))
        normalized["information_gaps"] = tuple(normalized.get("information_gaps", ()))
        return cls(**normalized)


class Attitude(str, Enum):
    WARM = "WARM"
    AMUSED = "AMUSED"
    NEUTRAL = "NEUTRAL"
    FOCUSED = "FOCUSED"
    GUARDED = "GUARDED"
    IRRITATED = "IRRITATED"


@dataclass(frozen=True)
class PermissionSnapshot:
    allowed: bool
    reason_code: str


@dataclass(frozen=True)
class StanceDecision:
    attitude: Attitude
    willingness: Willingness
    boundary: Boundary
    concession: Concession
    effort: Effort
    initiative: Initiative
    reason_event_ids: tuple[str, ...]
    permission: PermissionSnapshot
```

`SocialMove` 增加 `JOIN_CHORUS`，`RealizationMode` 定义 `GENERATED` 与 `EXACT_CHORUS`。`DecisionFact.create()` 使用 category、规范化文本和来源 ID 的 canonical JSON 计算 SHA-256 前 24 位。所有元组去重且有界；`GROUP` 场景允许 `target_id=None`，`INDIVIDUAL` 必须有目标；置信度限制为 0..1。`MEMBER` 必须有 `chorus_target_id`，其余靶心禁止该 ID；`chorus_event_ids` 必须是 `continuity_event_ids` 的非空子集，所有 chorus 字段要么一起为空，要么形成完整一致的复读证据。`JOIN_CHORUS` 必须同时使用 `EXACT_CHORUS` 和非空 `verbatim_payload`，其他动作必须使用 `GENERATED` 且禁止携带原文。

- [ ] **Step 4: 运行契约测试并确认绿灯**

Run: `pytest -q tests/social_runtime/test_social_scenes.py tests/social_runtime/test_stances.py tests/social_runtime/test_social_moves.py`

Expected: PASS.

- [ ] **Step 5: 提交契约**

```bash
git add groupmate/social_runtime/social_scenes.py groupmate/social_runtime/stances.py groupmate/social_runtime/social_moves.py tests/social_runtime/test_social_scenes.py tests/social_runtime/test_stances.py tests/social_runtime/test_social_moves.py
git commit -m "feat: define social style decision contracts"
```

### Task 2: 用相关性预算构建完整场景上下文

**Files:**
- Create: `groupmate/social_runtime/social_context.py`
- Modify: `groupmate/social_runtime/manager.py`
- Modify: `groupmate/social_runtime/profile/retrieval.py`
- Test: `tests/social_runtime/test_social_context.py`
- Test: `tests/social_runtime/profile/test_profile_retrieval.py`

**Interfaces:**
- Consumes: evaluation 的 `source_event`, `context_events`, frame evidence/target/topic，爱弥斯 actor ID 与别名、群成员目录、`ProfileRetrieval` 和关系记忆记录。
- Produces: `SceneContextBuilder.build -> SceneContext`；`SocialRuntimeManager.relationship_projection`；`relationship_memory_records`；`member_profile_retrieval`；`group_member_refs`；`await persona_snapshot`。

- [ ] **Step 1: 写入失败的上下文优先级测试**

```python
def test_scene_context_preserves_source_and_reply_parent_before_background():
    context = SceneContextBuilder(max_chars=600).build(
        source_event=_event("m4", "Few-Shot 太占提示词了，换个短方案", reply_to="m2"),
        context_events=(
            _event("m1", "普通群聊背景" * 40, actor_id="u2"),
            _event("m2", "可以放三条 Few-Shot", actor_id="aemeath"),
            _event("m3", "另一个话题" * 40, actor_id="u3"),
        ),
        focus_event_ids=("m2", "m4"),
        target_id="u1",
        topic_id="m2",
        persona_actor_id="aemeath",
        persona_aliases=("爱弥斯",),
        member_refs={"u1": ("霞月",)},
        profile=None,
        relationship_memories=(),
    )
    assert context.current_text == "Few-Shot 太占提示词了，换个短方案"
    assert [item.event_id for item in context.events[:2]] == ["m2", "m4"]
    assert "普通群聊背景" not in context.to_model_facts()["events"][0]["text"]
```

- [ ] **Step 2: 运行测试并确认红灯**

Run: `pytest -q tests/social_runtime/test_social_context.py tests/social_runtime/profile/test_profile_retrieval.py`

Expected: FAIL because `SceneContextBuilder` and structured manager accessors are absent.

- [ ] **Step 3: 实现分区预算和结构化访问器**

```python
@dataclass(frozen=True)
class SceneEventFact:
    event_id: str
    actor_id: str | None
    text: str
    reply_to: str | None
    parts: tuple[str, ...]
    occurred_at: int
    origin_kind: str


@dataclass(frozen=True)
class SceneContext:
    source_event_id: str
    current_text: str
    target_id: str | None
    topic_id: str | None
    events: tuple[SceneEventFact, ...]
    persona_actor_id: str
    persona_aliases: tuple[str, ...]
    member_refs: Mapping[str, tuple[str, ...]]
    profile_fact_ids: tuple[str, ...]
    memory_ids: tuple[str, ...]
    facts: Mapping[str, object]


class SceneContextBuilder:
    def build(self, *, source_event, context_events, focus_event_ids,
              target_id, topic_id, persona_actor_id, persona_aliases,
              member_refs, profile, relationship_memories) -> SceneContext:
        ranked = sorted(
            self._deduplicate((*context_events, source_event)),
            key=lambda event: self._priority(
                event, source_event=source_event,
                focus_event_ids=set(focus_event_ids), target_id=target_id,
            ),
        )
        return self._pack(ranked, source_event, target_id, topic_id,
                          persona_actor_id, persona_aliases, member_refs,
                          profile, relationship_memories)
```

`_priority` 固定按回复父链、当前消息、focus evidence、目标成员、同话题、普通背景排序。每个分区有独立上限；当前消息不使用现有 32 字符截断。Manager 新增：

```python
def relationship_projection(self, group_id: str, subject_id: str) -> RelationshipProjection:
    state, _ = self.society.relationship_snapshot(self.persona_id, group_id, subject_id)
    return state

def member_profile_retrieval(self, event: SocialEventEnvelope) -> ProfileRetrieval:
    return self.profile_retriever.for_message(event, max_chars=1200)

def relationship_memory_records(self, group_id: str, subject_id: str) -> tuple[RelationshipMemory, ...]:
    return self.society.relationship_memories(self.persona_id, group_id, subject_id)

async def persona_snapshot(self, group_id: str, config_version: int) -> PersonaSnapshot:
    profile = self._load_persona_profile(group_id)
    if profile.version != config_version:
        raise RuntimeError("persona profile changed after frozen evaluation")
    return await self.supervisor.snapshot(config_version)

def group_member_refs(self, group_id: str) -> Mapping[str, tuple[str, ...]]:
    return self.member_directory.names_and_aliases(group_id)
```

成员目录只提供本群成员的 ID、显示名和已确认别名，不把画像推断当作别名。模型看到的是当前上下文中可能被指向的受限成员集合；完整群目录过大时优先保留复读参与者、被回复成员和最近被提及成员。

- [ ] **Step 4: 运行测试并确认绿灯**

Run: `pytest -q tests/social_runtime/test_social_context.py tests/social_runtime/profile/test_profile_retrieval.py tests/social_runtime/test_relationships.py`

Expected: PASS; source/reply chain survive the budget and unrelated long background is dropped first.

- [ ] **Step 5: 提交上下文构建**

```bash
git add groupmate/social_runtime/social_context.py groupmate/social_runtime/manager.py groupmate/social_runtime/profile/retrieval.py tests/social_runtime/test_social_context.py tests/social_runtime/profile/test_profile_retrieval.py
git commit -m "feat: build relevant social scene context"
```

### Task 3: 用确定性证据识别复读链

**Files:**
- Create: `groupmate/social_runtime/chorus.py`
- Modify: `groupmate/social_runtime/social_context.py`
- Create: `tests/social_runtime/test_chorus.py`
- Modify: `tests/social_runtime/test_social_context.py`

**Interfaces:**
- Consumes: `SceneContext.events`、当前事件 ID、爱弥斯 actor ID、近期已发送的 chorus chain ID。
- Produces: `ChorusDetector.detect -> ChorusEvidence | None`；`ChorusParticipationRepository.recent_joined_chain_ids/mark_joined`；`SceneContext.with_chorus(evidence)`。

- [ ] **Step 1: 写入失败的复读链测试**

```python
def test_chorus_requires_two_distinct_humans_with_the_same_short_text():
    evidence = ChorusDetector(window_seconds=45, max_chars=80).detect(
        events=(
            _fact("m1", "u1", "小林今天请客", occurred_at=100),
            _fact("m2", "u2", "小林今天请客", occurred_at=112),
        ),
        source_event_id="m2",
        group_id="g1",
        persona_actor_id="aemeath",
        joined_chain_ids=(),
    )
    assert evidence is not None
    assert evidence.payload == "小林今天请客"
    assert evidence.participant_ids == ("u1", "u2")
    assert evidence.event_ids == ("m1", "m2")


def test_single_actor_spam_and_persona_output_do_not_form_a_chorus():
    detector = ChorusDetector(window_seconds=45, max_chars=80)
    assert detector.detect(
        events=(
            _fact("m1", "u1", "复读", occurred_at=100),
            _fact("m2", "u1", "复读", occurred_at=105),
            _fact("m3", "aemeath", "复读", occurred_at=108),
        ),
        source_event_id="m2",
        group_id="g1",
        persona_actor_id="aemeath",
        joined_chain_ids=(),
    ) is None


@pytest.mark.parametrize("origin_kind", ("COMMAND_RESULT", "PLUGIN_RESULT", "MEDIA", "PLATFORM_MENTION"))
def test_non_user_text_cannot_become_joinable_chorus(origin_kind):
    assert ChorusDetector().detect(
        events=(
            _fact("m1", "u1", "bq", occurred_at=100, origin_kind=origin_kind),
            _fact("m2", "u2", "bq", occurred_at=101, origin_kind=origin_kind),
        ),
        source_event_id="m2",
        group_id="g1",
        persona_actor_id="aemeath",
        joined_chain_ids=(),
    ) is None


def test_joined_chain_state_is_group_scoped_and_persistent(tmp_path):
    repo = ChorusParticipationRepository(tmp_path / "runtime.db")
    repo.mark_joined("g1", "chorus:abc", joined_at=120)
    assert repo.recent_joined_chain_ids("g1", since=60) == ("chorus:abc",)
    assert repo.recent_joined_chain_ids("g2", since=60) == ()
```

- [ ] **Step 2: 运行测试并确认红灯**

Run: `pytest -q tests/social_runtime/test_chorus.py tests/social_runtime/test_social_context.py`

Expected: FAIL because `ChorusDetector`, `ChorusEvidence` and chorus context attachment do not exist.

- [ ] **Step 3: 实现规范化、窗口和稳定链 ID**

```python
@dataclass(frozen=True)
class ChorusEvidence:
    chain_id: str
    payload: str
    normalized_key: str
    event_ids: tuple[str, ...]
    participant_ids: tuple[str, ...]
    already_joined: bool


class ChorusDetector:
    ALLOWED_ORIGIN = "USER_TEXT"

    def __init__(self, *, window_seconds: int = 45, max_chars: int = 80):
        self._window_seconds = window_seconds
        self._max_chars = max_chars

    def detect(self, *, events, source_event_id, group_id, persona_actor_id,
               joined_chain_ids) -> ChorusEvidence | None:
        source = self._source(events, source_event_id)
        if not self._eligible(source):
            return None
        key = self._normalize_key(source.text)
        matching = tuple(
            item for item in events
            if item.actor_id != persona_actor_id
            and item.origin_kind == self.ALLOWED_ORIGIN
            and 0 <= source.occurred_at - item.occurred_at <= self._window_seconds
            and self._normalize_key(item.text) == key
        )
        participants = tuple(dict.fromkeys(item.actor_id for item in matching))
        if len(participants) < 2:
            return None
        chain_id = self._chain_id(group_id, key, matching[0].event_id)
        return ChorusEvidence(
            chain_id=chain_id,
            payload=source.text.strip(),
            normalized_key=key,
            event_ids=tuple(item.event_id for item in matching),
            participant_ids=participants,
            already_joined=chain_id in set(joined_chain_ids),
        )
```

`_eligible` 只接受 1..80 字符的纯 `USER_TEXT`，拒绝 URL、空白文本、命令/插件结果、媒体和平台 mention 段。规范化仅做 Unicode NFKC、首尾空白去除和连续空白折叠；发送原文始终使用当前事件的 `payload`，不能使用规范化 key。`chain_id` 使用 group scope、normalized key 和链首事件 ID 的 canonical SHA-256 前 24 位，避免相同梗在不同时间误共享冷却。`SceneContext.with_chorus()` 返回新的冻结实例，不改写原 events。

`ChorusParticipationRepository` 在现有 runtime SQLite 中创建 `social_chorus_participation(group_id TEXT, chain_id TEXT, joined_at INTEGER, PRIMARY KEY(group_id, chain_id))`。只有 Outbox 报告实际发送成功后才调用 `mark_joined`；计划创建、预览、SHADOW 和发送失败均不得写入。查询默认保留 10 分钟窗口，旧记录可在普通维护周期清理。

- [ ] **Step 4: 运行测试并确认绿灯**

Run: `pytest -q tests/social_runtime/test_chorus.py tests/social_runtime/test_social_context.py`

Expected: PASS;不同真人形成链，单人刷屏和非用户文本被拒绝，原文与 normalized key 明确分离。

- [ ] **Step 5: 提交复读链检测**

```bash
git add groupmate/social_runtime/chorus.py groupmate/social_runtime/social_context.py tests/social_runtime/test_chorus.py tests/social_runtime/test_social_context.py
git commit -m "feat: detect evidence-backed group chorus chains"
```

### Task 4: 增加严格的结构化场景解析

**Files:**
- Modify: `groupmate/social_runtime/social_scenes.py`
- Create: `groupmate/adapters/social_scene_model.py`
- Test: `tests/social_runtime/test_social_scenes.py`
- Create: `tests/contracts/test_social_scene_model.py`

**Interfaces:**
- Consumes: `SceneContext.to_model_facts()`、可选 `ChorusEvidence` 和现有 `TextModelPort.complete_text`。
- Produces: `await SocialSceneInterpreter.interpret(context) -> SceneInterpretationResult`，失败时包含固定诊断码和保守场景。

- [ ] **Step 1: 写入模型输出校验测试**

```python
def test_interpreter_rejects_invented_evidence_ids():
    model = FixedSceneModel({
        "scene_kind": "repeated_boundary_test",
        "target_scope": "INDIVIDUAL",
        "target_id": "u1",
        "literal_subject": "拥抱请求",
        "user_move": "repeats_intimacy_request",
        "continuity_event_ids": ["invented"],
        "repetition_count": 3,
        "constraints": [], "information_gaps": [],
        "capability_request": "NONE", "confidence": 0.96,
    })
    result = asyncio.run(SocialSceneInterpreter(model).interpret(_context("m1")))
    assert result.diagnostic_code == "scene_evidence_invalid"
    assert result.scene.scene_kind == "conservative_direct"


def test_scene_model_prompt_contains_no_surface_meta_language():
    prompt = SceneJsonModel.system_prompt()
    assert "情绪承接" not in prompt
    assert "人格化补充" not in prompt
    assert "续聊接口" not in prompt


def test_interpreter_freezes_self_chorus_evidence_instead_of_model_payload():
    context = _chorus_context(
        payload="爱弥斯又嘴硬", event_ids=("m1", "m2"),
        participant_ids=("u1", "u2"),
    )
    model = FixedSceneModel({
        "scene_kind": "group_chorus",
        "target_scope": "GROUP",
        "target_id": None,
        "literal_subject": "爱弥斯",
        "user_move": "chorus_about_self",
        "continuity_event_ids": ["m1", "m2"],
        "chorus_target": "SELF",
        "chorus_target_id": None,
        "chorus_tone": "SAFE_BANTER",
        "chorus_payload": "模型试图改写的文本",
        "confidence": 0.97,
    })
    result = asyncio.run(SocialSceneInterpreter(model).interpret(context))
    assert result.scene.chorus_payload == "爱弥斯又嘴硬"
    assert result.scene.chorus_event_ids == ("m1", "m2")


def test_member_chorus_target_must_resolve_to_current_group_member():
    result = asyncio.run(SocialSceneInterpreter(_member_chorus_model("not-in-group")).interpret(
        _chorus_context(member_refs={"u9": ("小林",)})
    ))
    assert result.diagnostic_code == "chorus_member_invalid"
    assert result.scene.chorus_target is ChorusTarget.UNKNOWN
```

- [ ] **Step 2: 运行测试并确认红灯**

Run: `pytest -q tests/social_runtime/test_social_scenes.py tests/contracts/test_social_scene_model.py`

Expected: FAIL because interpreter and model adapter do not exist.

- [ ] **Step 3: 实现 JSON 端口、解析和降级**

```python
class SocialSceneModelPort(Protocol):
    async def classify_scene(self, facts: Mapping[str, object]) -> Mapping[str, object]: ...


@dataclass(frozen=True)
class SceneInterpretationResult:
    scene: SocialScene
    diagnostic_code: str | None = None


class SocialSceneInterpreter:
    async def interpret(self, context: SceneContext) -> SceneInterpretationResult:
        try:
            raw = await self._model.classify_scene(context.to_model_facts())
            raw = self._freeze_chorus_evidence(raw, context.chorus)
            scene = SocialScene.create(**raw)
            allowed = {item.event_id for item in context.events}
            if not set(scene.continuity_event_ids) <= allowed:
                return self._fallback(context, "scene_evidence_invalid")
            if scene.target_id not in {None, context.target_id}:
                return self._fallback(context, "scene_target_invalid")
            if scene.chorus_target is ChorusTarget.MEMBER:
                if scene.chorus_target_id not in context.member_refs:
                    return self._chorus_unknown(context, "chorus_member_invalid")
            return SceneInterpretationResult(scene)
        except Exception:
            return self._fallback(context, "scene_model_failed")
```

`SceneJsonModel` 调用现有文本端口，解析单个 JSON 对象，限制响应大小，并只允许规范中列出的场景族、目标范围、capability、`chorus_target` 和 `chorus_tone` 值。模型只负责语义靶心和语气风险，不能提供 chain ID、复读原文、参与者、already-joined 状态或事件 ID；`_freeze_chorus_evidence` 总是用 `ChorusEvidence` 覆盖这些字段。没有确定性证据却声称复读、目标成员不在受限目录、或 `MEMBER` 缺少 ID 时降级为 `UNKNOWN`，不得猜测。直接互动失败返回 `conservative_direct`；AMBIENT 失败返回 `observe_only`。

- [ ] **Step 4: 运行测试并确认绿灯**

Run: `pytest -q tests/social_runtime/test_social_scenes.py tests/contracts/test_social_scene_model.py`

Expected: PASS;未知 ID、错误目标、虚构复读证据、非法成员、非法 JSON 和模型异常均确定性降级。

- [ ] **Step 5: 提交场景解析**

```bash
git add groupmate/social_runtime/social_scenes.py groupmate/adapters/social_scene_model.py tests/social_runtime/test_social_scenes.py tests/contracts/test_social_scene_model.py
git commit -m "feat: interpret concrete social scenes"
```

### Task 5: 用关系、前情和安全要求形成爱弥斯立场

**Files:**
- Modify: `groupmate/social_runtime/stances.py`
- Modify: `groupmate/social_runtime/manager.py`
- Test: `tests/social_runtime/test_stances.py`
- Test: `tests/social_runtime/test_relationships.py`

**Interfaces:**
- Consumes: `SocialScene`、当前发送者关系、可选复读靶心成员关系、`PermissionSnapshot`、群文化、Persona mode modifiers 和相关记忆 ID。
- Produces: `StancePolicy.decide -> StanceDecision`。

- [ ] **Step 1: 写入关系差异与权限隔离测试**

```python
@pytest.mark.parametrize(
    ("relationship", "expected_willingness", "expected_boundary"),
    (
        (RelationshipProjection("aemeath", "g", "u", warmth=55, play_acceptance=60), "LIMITED", "SOFT"),
        (RelationshipProjection("aemeath", "g", "u"), "UNWILLING", "SOFT"),
        (RelationshipProjection("aemeath", "g", "u", boundary_pressure=70), "UNWILLING", "FIRM"),
    ),
)
def test_same_intimacy_request_uses_relationship_for_willingness(relationship, expected_willingness, expected_boundary):
    decision = StancePolicy().decide(
        _scene("intimacy_request"), actor_relationship=relationship,
        subject_relationship=None, culture_patterns=(),
        permission=PermissionSnapshot(True, "social_reply"),
        mode_modifiers=(), memory_event_ids=relationship.evidence_event_ids,
    )
    assert decision.willingness.value == expected_willingness
    assert decision.boundary.value == expected_boundary


def test_safety_minimum_ignores_low_affection_but_not_permission():
    decision = StancePolicy().decide(
        _scene("safety_signal"),
        actor_relationship=RelationshipProjection("aemeath", "g", "u", boundary_pressure=100),
        subject_relationship=None, culture_patterns=(),
        permission=PermissionSnapshot(True, "safety_required"),
        mode_modifiers=(), memory_event_ids=("boundary-1",),
    )
    assert decision.willingness is Willingness.REQUIRED_MINIMUM


@pytest.mark.parametrize(
    ("tone", "subject_pressure", "expected"),
    (
        ("SAFE_BANTER", 0, Willingness.WILLING),
        ("SAFE_BANTER", 70, Willingness.UNWILLING),
        ("ATTACK", 0, Willingness.UNWILLING),
        ("UNKNOWN", 0, Willingness.UNWILLING),
    ),
)
def test_member_chorus_uses_target_relationship_and_tone(tone, subject_pressure, expected):
    decision = StancePolicy().decide(
        _chorus_scene(target="MEMBER", tone=tone),
        actor_relationship=_relationship("u2", play_acceptance=80),
        subject_relationship=_relationship("u9", play_acceptance=60, boundary_pressure=subject_pressure),
        culture_patterns=("light_member_banter",),
        permission=PermissionSnapshot(True, "social_reply"),
        mode_modifiers=(), memory_event_ids=("m1", "m2"),
    )
    assert decision.willingness is expected
```

- [ ] **Step 2: 运行测试并确认红灯**

Run: `pytest -q tests/social_runtime/test_stances.py tests/social_runtime/test_relationships.py`

Expected: FAIL because `StancePolicy` is not implemented.

- [ ] **Step 3: 实现显式决策表**

```python
class StancePolicy:
    def decide(self, scene, *, actor_relationship, subject_relationship,
               culture_patterns, permission, mode_modifiers,
               memory_event_ids) -> StanceDecision:
        if not permission.allowed:
            return self._unwilling(scene, permission, Boundary.FINAL)
        if scene.scene_kind == "safety_signal":
            return StanceDecision(
                Attitude.FOCUSED, Willingness.REQUIRED_MINIMUM,
                Boundary.NONE, Concession.NONE, Effort.MINIMAL,
                Initiative.ALLOW, scene.continuity_event_ids, permission,
            )
        if scene.chorus_target is ChorusTarget.MEMBER:
            return self._member_chorus_stance(
                scene, subject_relationship, culture_patterns, permission,
            )
        if actor_relationship.boundary_pressure >= 40 or scene.repetition_count >= 3:
            return self._unwilling(scene, permission, Boundary.FIRM)
        if scene.scene_kind in {"intimacy_request", "playful_negotiation"}:
            return self._play_stance(scene, actor_relationship, permission)
        if scene.scene_kind in {"technical_help", "fact_question"}:
            return self._focused_help(scene, actor_relationship, permission)
        return self._neutral(scene, actor_relationship, permission, mode_modifiers)
```

`_member_chorus_stance` 仅在 `SAFE_BANTER`、靶心关系可用、靶心边界压力低于命名阈值、群文化未禁止成员玩笑且当前模式允许参与时返回 `WILLING`；`SENSITIVE`、`ATTACK`、`DANGEROUS`、`UNKNOWN` 或缺少靶心关系均返回 `UNWILLING`。指向爱弥斯的复读不走该分支，而是根据群体动作形成 `AMUSED`、`GUARDED` 或 `IRRITATED` 的群体回应立场。所有阈值集中为命名常量并由测试锁定；`reason_event_ids` 只来自场景、关系和允许记忆。删除或保留 `RelationshipProjector.authorizes_capability()` 均不得让其参与此策略。

- [ ] **Step 4: 运行测试并确认绿灯**

Run: `pytest -q tests/social_runtime/test_stances.py tests/social_runtime/test_relationships.py`

Expected: PASS;相同请求产生关系一致的不同意愿，复读读取靶心成员关系而非当前发送者关系，安全最低帮助仍然成立。

- [ ] **Step 5: 提交立场策略**

```bash
git add groupmate/social_runtime/stances.py groupmate/social_runtime/manager.py tests/social_runtime/test_stances.py tests/social_runtime/test_relationships.py
git commit -m "feat: derive evidence-backed aemeath stances"
```

### Task 6: 将立场转换为具体社交动作和事实

**Files:**
- Modify: `groupmate/social_runtime/social_moves.py`
- Modify: `groupmate/social_runtime/profile/retrieval.py`
- Test: `tests/social_runtime/test_social_moves.py`
- Test: `tests/social_runtime/profile/test_profile_retrieval.py`

**Interfaces:**
- Consumes: `SocialScene`, `StanceDecision`, `ProfileRetrieval` 和允许的关系记忆。
- Produces: `SocialMovePlanner.plan -> SocialMovePlan`，其中所有 `must_say` 都是带来源的 `DecisionFact`。

- [ ] **Step 1: 写入完整动作链测试**

```python
def test_repeated_boundary_scene_becomes_one_firm_refusal_fact():
    plan = SocialMovePlanner().plan(
        _scene("repeated_boundary_test", repetition_count=3),
        _stance("UNWILLING", boundary="FIRM"),
        profile=_empty_profile(), memories=(),
    )
    assert plan.primary_move is SocialMove.FIRM_BOUNDARY
    assert plan.ending is Ending.STOP
    assert [item.category for item in plan.must_say] == ["boundary"]
    assert "第三次" in plan.must_say[0].text


def test_technical_help_with_real_gap_asks_only_for_needed_evidence():
    plan = SocialMovePlanner().plan(
        _scene("technical_help", information_gaps=("报错首段", "版本号")),
        _stance("WILLING", attitude="FOCUSED"),
        profile=_empty_profile(), memories=(),
    )
    assert plan.primary_move is SocialMove.REQUEST_NEEDED_EVIDENCE
    assert plan.ask_for == ("报错首段", "版本号")
    assert plan.ending is Ending.QUESTION


def test_self_targeted_chorus_becomes_generated_group_response():
    plan = SocialMovePlanner().plan(
        _chorus_scene(target="SELF", payload="爱弥斯又嘴硬"),
        _stance("WILLING", attitude="AMUSED"),
        profile=_empty_profile(), memories=(),
    )
    assert plan.primary_move is SocialMove.GROUP_RESPONSE
    assert plan.realization_mode is RealizationMode.GENERATED
    assert plan.verbatim_payload is None


def test_safe_member_chorus_joins_with_exact_frozen_payload_once():
    plan = SocialMovePlanner().plan(
        _chorus_scene(
            target="MEMBER", target_id="u9", payload="小林今天请客",
            tone="SAFE_BANTER", already_joined=False,
        ),
        _stance("WILLING", attitude="AMUSED"),
        profile=_empty_profile(), memories=(),
    )
    assert plan.primary_move is SocialMove.JOIN_CHORUS
    assert plan.realization_mode is RealizationMode.EXACT_CHORUS
    assert plan.verbatim_payload == "小林今天请客"


@pytest.mark.parametrize("tone", ("ATTACK", "DANGEROUS", "UNKNOWN"))
def test_unsafe_or_already_joined_member_chorus_is_silent(tone):
    scene = _chorus_scene(
        target="MEMBER", target_id="u9", payload="不应发送",
        tone=tone, already_joined=(tone == "UNKNOWN"),
    )
    plan = SocialMovePlanner().plan(
        scene, _stance("UNWILLING"),
        profile=_empty_profile(), memories=(),
    )
    assert plan.primary_move is SocialMove.SILENCE
```

- [ ] **Step 2: 运行测试并确认红灯**

Run: `pytest -q tests/social_runtime/test_social_moves.py tests/social_runtime/profile/test_profile_retrieval.py`

Expected: FAIL because no planner maps scenes and stances to facts.

- [ ] **Step 3: 实现优先级明确的动作规划器**

```python
class SocialMovePlanner:
    def plan(self, scene, stance, *, profile, memories) -> SocialMovePlan:
        if stance.willingness is Willingness.REQUIRED_MINIMUM:
            return self._safety_minimum(scene)
        if scene.chorus_chain_id is not None:
            if scene.chorus_already_joined:
                return self._silence(scene)
            if scene.chorus_target is ChorusTarget.SELF:
                return self._group_response(scene, stance)
            if scene.chorus_target in {ChorusTarget.MEMBER, ChorusTarget.OTHER}:
                if (scene.chorus_tone is ChorusTone.SAFE_BANTER
                        and stance.willingness in {Willingness.WILLING, Willingness.EAGER}):
                    return self._join_chorus_exact(scene)
                return self._silence(scene)
            return self._silence(scene)
        if stance.boundary in {Boundary.FIRM, Boundary.FINAL}:
            return self._firm_boundary(scene)
        if scene.target_scope is TargetScope.GROUP:
            return self._group_response(scene, stance)
        if scene.scene_kind == "self_correction":
            return self._correct_self(scene)
        if scene.information_gaps:
            return self._request_evidence(scene)
        if stance.willingness is Willingness.LIMITED:
            return self._limited_accept(scene)
        if stance.willingness is Willingness.UNWILLING:
            return self._refuse(scene)
        return self._direct_or_play(scene, stance, profile, memories)
```

复读分流必须位于通用 `GROUP` 分支之前，否则所有复读都会被错误折叠为 `GROUP_RESPONSE`。`_join_chorus_exact` 只复制 `scene.chorus_payload`，写入 `EXACT_CHORUS` 和 `chorus_chain_id`，不创建 `must_say`、问题或解释性前缀。画像事实只在其 `fact_id`、证据级别和当前主题相关时进入 `may_say`；确认事实可直接陈述，稳定模式添加观察限定，玩笑印象只允许在画像或明确玩笑场景出现。

- [ ] **Step 4: 运行测试并确认绿灯**

Run: `pytest -q tests/social_runtime/test_social_moves.py tests/social_runtime/profile/test_profile_retrieval.py`

Expected: PASS;默认结束方式为 STOP，QUESTION 只对应实际信息缺口或动作需要；指向爱弥斯的复读成为群体回应，安全群友复读才逐字加入，其他情况沉默。

- [ ] **Step 5: 提交动作规划**

```bash
git add groupmate/social_runtime/social_moves.py groupmate/social_runtime/profile/retrieval.py tests/social_runtime/test_social_moves.py tests/social_runtime/profile/test_profile_retrieval.py
git commit -m "feat: plan concrete social response moves"
```

### Task 7: 接入 StyleDirector 并持久化新回复计划

**Files:**
- Modify: `groupmate/social_runtime/actions/style.py`
- Modify: `groupmate/social_runtime/replying.py`
- Modify: `groupmate/social_runtime/expression.py`
- Test: `tests/social_runtime/actions/test_style.py`
- Test: `tests/social_runtime/actions/test_replying.py`

**Interfaces:**
- Consumes: `SocialScene`, `StanceDecision`, `SocialMovePlan`, `RelationshipProjection`, Persona profile 和近期输出。
- Produces: 新版 `StyleDirective` 和可向后解码的 `ReplyPlan`。

- [ ] **Step 1: 写入失败的样式与持久化测试**

```python
def test_reply_planner_uses_style_director_instead_of_uniform_friendly_defaults():
    director = RecordingStyleDirector(_directive(posture="firm", directness=95))
    plan = ReplyPlanner(style_director=director).plan(
        _evaluation(), now=100, persona_profile=_persona(),
        relationship_projection=_relationship(boundary_pressure=70),
        scene=_scene("repeated_boundary_test"),
        stance=_stance("UNWILLING", boundary="FIRM"),
        move=_move("FIRM_BOUNDARY"), recent_outputs=(),
    )
    assert director.calls == 1
    assert plan.style.posture == "firm"
    assert plan.style.directness == 95


def test_repository_decodes_legacy_expression_only_plan(tmp_path):
    repo = ReplyPlanRepository(tmp_path / "runtime.db")
    restored = repo._decode(_legacy_plan_json())
    assert restored.scene.scene_kind == "legacy_conservative"
    assert restored.move.primary_move is SocialMove.DIRECT_ANSWER


def test_repository_round_trips_exact_chorus_plan(tmp_path):
    repo = ReplyPlanRepository(tmp_path / "runtime.db")
    original = _reply_plan(
        scene=_chorus_scene(chain_id="chorus:abc", payload="小林今天请客"),
        move=_move(
            "JOIN_CHORUS", realization_mode="EXACT_CHORUS",
            verbatim_payload="小林今天请客",
        ),
    )
    restored = repo._decode(repo._encode(original))
    assert restored.scene.chorus_chain_id == "chorus:abc"
    assert restored.move.verbatim_payload == "小林今天请客"
```

- [ ] **Step 2: 运行测试并确认红灯**

Run: `pytest -q tests/social_runtime/actions/test_style.py tests/social_runtime/actions/test_replying.py`

Expected: FAIL because planner still hardcodes `StyleDirective` and plans lack new decisions.

- [ ] **Step 3: 修改样式上下文、规划器和兼容解码**

```python
@dataclass(frozen=True)
class StyleContext:
    persona: PersonaStyleSnapshot
    mode: PersonaModeState
    relationship: RelationshipProjection | None
    scene: SocialScene
    stance: StanceDecision
    move: SocialMovePlan
    culture_patterns: tuple[str, ...]
    recent_outputs: tuple[str, ...]
    token_budget: int


class ReplyPlanner:
    def __init__(self, *, style_director=None, expression_planner=None):
        self._style_director = style_director or StyleDirector()
        self._expression_planner = expression_planner or ExpressionPlanner()
```

`ReplyPlan` 新增 `scene`, `stance`, `move`, `relationship_projection_version`。`_encode` 使用 `asdict`；`_decode` 显式恢复所有 Enum/tuple 和嵌套 dataclass，包括 chorus 字段与 `RealizationMode`。旧 JSON 缺字段时创建 `legacy_conservative` 默认值。`ExpressionPlan` 继续保存 explicit material、avoidances 和旧追踪字段，但新生成提示不再使用 reaction/followup/boundary 元话语。

`StyleDirector.direct()` 从 `context.move.primary_move.value.lower()` 派生 `StyleDirective.act`，不再读取旧宽泛 act；StyleContext 不保留第二份重复动作字段。`EXACT_CHORUS` 仍创建可追踪的最小 style 摘要，但这些表达参数不参与文本改写。

- [ ] **Step 4: 运行测试并确认绿灯**

Run: `pytest -q tests/social_runtime/actions/test_style.py tests/social_runtime/actions/test_replying.py tests/social_runtime/test_expression.py`

Expected: PASS;硬编码 friendly directive 的断言被真实 director 调用替代，旧计划仍能加载。

- [ ] **Step 5: 提交样式接入**

```bash
git add groupmate/social_runtime/actions/style.py groupmate/social_runtime/replying.py groupmate/social_runtime/expression.py tests/social_runtime/actions/test_style.py tests/social_runtime/actions/test_replying.py tests/social_runtime/test_expression.py
git commit -m "feat: wire social decisions into reply style"
```

### Task 8: 生成结构化成品并执行社交一致性检查

**Files:**
- Create: `groupmate/social_runtime/social_review.py`
- Modify: `groupmate/social_runtime/replying.py`
- Modify: `groupmate/social_runtime/actions/generation.py`
- Create: `tests/social_runtime/actions/test_social_review.py`
- Modify: `tests/social_runtime/actions/test_replying.py`
- Modify: `tests/social_runtime/actions/test_output_firewall.py`

**Interfaces:**
- Consumes: `ReplyPlan`、模型 JSON、允许的 fact/memory/capability/event ID 和可选 `EXACT_CHORUS` 原文。
- Produces: `RealizedReply`, `SocialReview`, 修复一次后的正文，再交给 `OutputFirewall`。

- [ ] **Step 1: 写入失败的结构与反人机测试**

```python
def test_reviewer_rejects_unknown_ids_and_service_tail():
    reply = RealizedReply(
        text="我理解你的担忧。如果你愿意，我可以继续帮助你。",
        covered_fact_ids=("invented",), used_memory_ids=(),
        used_capability_ids=(),
    )
    review = SocialOutputReviewer().review(reply, _plan(ending="STOP"))
    assert review.accepted is False
    assert "unknown_fact_id" in review.violations
    assert "generic_service_tail" in review.violations


def test_executor_repairs_once_with_specific_violations(tmp_path):
    model = SequenceModel((
        '{"text":"我理解你，需要我继续吗？","covered_fact_ids":[],"used_memory_ids":[],"used_capability_ids":[]}',
        '{"text":"把报错首段和版本号贴出来。","covered_fact_ids":["fact:inputs"],"used_memory_ids":[],"used_capability_ids":[]}',
    ))
    result = asyncio.run(_executor(tmp_path, model).execute_with_result(_technical_plan(), context_events=(), persona_profile=_persona(), recent_outputs=()))
    assert result.status == "READY"
    assert len(model.calls) == 2
    assert "generic_service_tail" in model.calls[1]["prompt"]


def test_exact_chorus_bypasses_model_but_still_uses_both_reviewers(tmp_path):
    model = FailIfCalledModel()
    plan = _exact_chorus_plan(
        payload="小林今天请客",
        event_ids=("m1", "m2"),
        chain_id="chorus:abc",
    )
    result = asyncio.run(_executor(tmp_path, model).execute_with_result(
        plan, context_events=(), persona_profile=_persona(), recent_outputs=(),
    ))
    assert result.status == "READY"
    assert result.text == "小林今天请客"
    assert model.calls == []
    assert result.realized.source_event_ids == ("m1", "m2")


def test_exact_chorus_reviewer_rejects_changed_or_previously_joined_payload():
    plan = _exact_chorus_plan(payload="小林今天请客", already_joined=True)
    review = SocialOutputReviewer().review(
        RealizedReply(
            text="我也来：小林今天请客",
            covered_fact_ids=(), used_memory_ids=(), used_capability_ids=(),
            source_event_ids=("m1", "m2"),
        ),
        plan,
    )
    assert set(review.violations) >= {"chorus_payload_changed", "chorus_already_joined"}
```

- [ ] **Step 2: 运行测试并确认红灯**

Run: `pytest -q tests/social_runtime/actions/test_social_review.py tests/social_runtime/actions/test_replying.py`

Expected: FAIL because structured replies and social review do not exist.

- [ ] **Step 3: 实现解析、检查、具体修复和降级**

```python
@dataclass(frozen=True)
class RealizedReply:
    text: str
    covered_fact_ids: tuple[str, ...]
    used_memory_ids: tuple[str, ...]
    used_capability_ids: tuple[str, ...]
    source_event_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class SocialReview:
    accepted: bool
    violations: tuple[str, ...]


class SocialOutputReviewer:
    def review(self, reply: RealizedReply, plan: ReplyPlan) -> SocialReview:
        violations = []
        violations += self._id_violations(reply, plan)
        violations += self._coverage_violations(reply, plan.move.must_say)
        violations += self._ending_violations(reply.text, plan.move.ending)
        violations += self._generic_structure_violations(reply.text)
        violations += self._identity_violations(reply.text, plan)
        violations += self._chorus_violations(reply, plan)
        return SocialReview(not violations, tuple(dict.fromkeys(violations)))
```

`ReplyExecutor._system_prompt` 删除四段式和“先接住”要求，只序列化具体消息、意愿、动作、DecisionFact、相关 Persona 事实和 style 约束。模型必须返回单个 JSON 对象。`EXACT_CHORUS` 在进入模型分支前直接构造 `RealizedReply`；`_chorus_violations` 验证动作、模式、逐字原文、chain ID、事件 ID、already-joined 状态和安全 tone。

现有 `OutputFirewall` 增加类型化 `ExactChorusAllowance(chain_id, payload, source_event_ids)`。只有 `SocialOutputReviewer` 已通过且 allowance 与 `ReplyPlan` 完全一致时，重复文本检查才允许这一次逐字输出；长度、安全、链接、mention、能力声明和平台限制不旁路。普通回复仍使用现有重复规避。首次普通社交或 firewall 失败时，修复提示包含 violation code、必须保留 fact 和 max_chars；`EXACT_CHORUS` 不允许模型修复，任一检查失败直接沉默。第二次普通生成失败时 AMBIENT 沉默，安全最低场景使用确定性文本，其他 required 直接互动使用场景族最小回退。

- [ ] **Step 4: 运行测试并确认绿灯**

Run: `pytest -q tests/social_runtime/actions/test_social_review.py tests/social_runtime/actions/test_replying.py tests/social_runtime/actions/test_output_firewall.py`

Expected: PASS;生成提示不含抽象元话语，普通生成修复最多一次，未知 ID 不能发送，合法复读原样通过而任何改写或重复参与均被拒绝。

- [ ] **Step 5: 提交生成与审查**

```bash
git add groupmate/social_runtime/social_review.py groupmate/social_runtime/replying.py groupmate/social_runtime/actions/generation.py tests/social_runtime/actions/test_social_review.py tests/social_runtime/actions/test_replying.py tests/social_runtime/actions/test_output_firewall.py
git commit -m "feat: review realized social replies"
```

### Task 9: 在桥接层编排新主链并更新安全追踪

**Files:**
- Modify: `groupmate/social_runtime/manager.py`
- Modify: `groupmate/social_runtime/participation.py`
- Modify: `groupmate/adapters/astrbot_bridge.py`
- Modify: `groupmate/social_runtime/control/message_traces.py`
- Modify: `tests/social_runtime/test_participation.py`
- Modify: `tests/scenarios/test_chat_mainline.py`
- Modify: `tests/contracts/test_message_traces.py`

**Interfaces:**
- Consumes: 命令归属完成后的群事件、近期事件、`ChorusEvidence` 和已获 Governor 批准的 evaluation。
- Produces: `ChorusEvidence -> SceneContext -> SocialScene -> StanceDecision -> SocialMovePlan -> ReplyPlan`，发送成功后记录 chain ID，并记录不含原始 Persona/画像文本的摘要。

- [ ] **Step 1: 写入失败的桥接与追踪测试**

```python
def test_direct_reply_runs_scene_before_reply_generation(tmp_path):
    context, trace = asyncio.run(_run_style_case(tmp_path, "又要抱抱", scene="intimacy_request"))
    assert [call["kind"] for call in context.calls] == ["scene", "reply"]
    assert trace["social_scene"]["scene_kind"] == "intimacy_request"
    assert trace["stance"]["willingness"] in {"LIMITED", "UNWILLING"}
    assert trace["social_move"]["ending"] == "STOP"


def test_trace_does_not_publish_member_facts_or_internal_scores(tmp_path):
    summary = _record_new_plan(tmp_path)
    serialized = json.dumps(summary, ensure_ascii=False)
    assert "profile_fact_ids" not in serialized
    assert "boundary_pressure" not in serialized
    assert "数据库" not in serialized


def test_confirmed_chorus_gets_scene_evaluation_instead_of_generic_repeat_suppression(tmp_path):
    result = asyncio.run(_run_group_events(
        tmp_path,
        (("u1", "小林今天请客"), ("u2", "小林今天请客")),
    ))
    assert result.participation_candidate.kind == "CHORUS_CHECK"
    assert result.participation_candidate.repetition_cost == 0.0
    assert result.calls[0]["kind"] == "scene"


def test_exact_command_never_enters_chorus_or_scene_pipeline(tmp_path):
    result = asyncio.run(_run_group_events(tmp_path, (("u1", "bq"), ("u2", "bq"))))
    assert result.command_owner is not None
    assert result.chorus_evidence is None
    assert result.calls == []
```

- [ ] **Step 2: 运行测试并确认红灯**

Run: `pytest -q tests/scenarios/test_chat_mainline.py -k 'scene_before_reply' tests/contracts/test_message_traces.py -k 'social_scene or member_facts'`

Expected: FAIL because bridge directly calls the old planner, Participation has no `CHORUS_CHECK` lane input, and trace still projects ExpressionPlan only.

- [ ] **Step 3: 实现桥接编排和追踪摘要**

命令和能力 Ownership 先运行；只有未被认领的纯闲聊事件才进入复读检测。在 `SocialRuntimeManager._evaluate_cycle()` 创建认知上下文前，从近期事件构造轻量 `SceneEventFact`，调用 `ChorusDetector`，并把证据附到 `ShadowEvaluation`。`ParticipationPolicy.propose(..., chorus_evidence=...)` 为确认链创建 `CHORUS_CHECK` 候选，`repetition_cost=0` 只表示“允许继续判断靶心”，不表示必须发送；Governor 的暂停、隐私、边界、平台、限流和最低效用约束仍然生效。普通重复仍使用原 repetition cost。

在 `start()` 中以同一个 `reply_model` 构造 `SceneJsonModel` 和 `SocialSceneInterpreter`。在 `_handle_evaluations()` 中，完成当前发送者关系、画像和记忆检索后按以下顺序执行：

```python
frame = evaluation.frame
plan_topic_id = next(iter(getattr(frame, "focus_topic_ids", ())), None)
context = self._scene_context_builder.build(
    source_event=source_event,
    context_events=tuple(getattr(evaluation, "context_events", ())),
    focus_event_ids=tuple(getattr(frame, "focus_event_ids", ())),
    target_id=subject_id,
    topic_id=plan_topic_id,
    persona_actor_id=self._manager.persona_id,
    persona_aliases=tuple(persona_profile.get("identity_aliases", ("爱弥斯",))),
    member_refs=self._manager.group_member_refs(group_id),
    profile=profile_retrieval,
    relationship_memories=relationship_memories,
).with_chorus(getattr(evaluation, "chorus_evidence", None))
interpretation = await self._scene_interpreter.interpret(context)
subject_relationship = (
    self._manager.relationship_projection(
        group_id, interpretation.scene.chorus_target_id,
    )
    if interpretation.scene.chorus_target is ChorusTarget.MEMBER
    else None
)
permission = PermissionSnapshot(True, "social_reply_governed")
persona_snapshot = await self._manager.persona_snapshot(
    group_id, int(getattr(evaluation, "config_version", 0))
)
stance = self._stance_policy.decide(
    interpretation.scene,
    actor_relationship=relationship_projection,
    subject_relationship=subject_relationship,
    culture_patterns=tuple(getattr(evaluation, "culture_patterns", ())),
    permission=permission,
    mode_modifiers=persona_snapshot.modifiers,
    memory_event_ids=memory_event_ids,
)
move = self._move_planner.plan(
    interpretation.scene, stance,
    profile=profile_retrieval, memories=relationship_memories,
)
plan = self._reply_planner.plan(
    evaluation, now=now, persona_profile=persona_profile,
    relationship_projection=relationship_projection,
    scene=interpretation.scene, stance=stance, move=move,
    recent_outputs=recent_outputs,
)
```

AMBIENT 的 `observe_only` 或 `SocialMove.SILENCE` 不创建 ReplyPlan。`JOIN_CHORUS` 只有 Outbox 返回成功后才调用 `ChorusParticipationRepository.mark_joined(group_id, chain_id, sent_at)`；SHADOW、预览、失败和重试前均不标记。追踪记录 scene kind、目标范围、chorus target、chain ID、参与人数、立场枚举、动作枚举、fact category、ending、realization mode、样式摘要、诊断码和证据预览，不记录完整成员 ID 列表、内部数值、画像正文或 Persona 私有材料。

- [ ] **Step 4: 运行测试并确认绿灯**

Run: `pytest -q tests/social_runtime/test_participation.py tests/scenarios/test_chat_mainline.py tests/contracts/test_message_traces.py tests/social_runtime/actions/test_replying.py`

Expected: PASS;直接、续聊、环境、复读和 SHADOW 路径均使用新链，普通重复仍受治理，外部命令场景无复读检测和模型回复。

- [ ] **Step 5: 提交主链编排**

```bash
git add groupmate/social_runtime/manager.py groupmate/social_runtime/participation.py groupmate/adapters/astrbot_bridge.py groupmate/social_runtime/control/message_traces.py tests/social_runtime/test_participation.py tests/scenarios/test_chat_mainline.py tests/contracts/test_message_traces.py
git commit -m "feat: orchestrate aemeath social style pipeline"
```

### Task 10: 增加完整关系场景、主动互动和回归验证

**Files:**
- Create: `tests/scenarios/test_aemeath_smalltalk_style.py`
- Modify: `tests/scenarios/test_autonomous_opportunities.py`
- Modify: `tests/scenarios/test_social_runtime_shadow.py`
- Modify: `tests/contracts/test_direct_ambient_worker.py`
- Modify: `groupmate/social_runtime/cognition/ambient_worker.py`

**Interfaces:**
- Consumes: 完成后的主链。
- Produces: 数据集场景回归矩阵和 SHADOW 新旧决策证据。

- [ ] **Step 1: 写入失败的场景矩阵**

```python
@pytest.mark.parametrize(
    ("case", "expected_move", "ending"),
    (
        ("familiar_limited_hug", "LIMITED_ACCEPT", "STOP"),
        ("reciprocal_play", "LIMITED_ACCEPT", "STOP"),
        ("guarded_repeat", "FIRM_BOUNDARY", "STOP"),
        ("technical_constraint", "DIRECT_ANSWER", "STOP"),
        ("identity_continuity", "COUNTER", "STOP"),
        ("group_pile_on", "GROUP_RESPONSE", "STOP"),
        ("chorus_about_aemeath", "GROUP_RESPONSE", "STOP"),
        ("safe_chorus_about_member", "JOIN_CHORUS", "STOP"),
        ("single_actor_repeat", "SILENCE", "STOP"),
        ("attack_chorus_about_member", "SILENCE", "STOP"),
        ("already_joined_chorus", "SILENCE", "STOP"),
        ("self_correction", "CORRECT_SELF", "STOP"),
        ("safety_low_affection", "SAFETY_MINIMUM", "OPEN_ACTION"),
        ("proactive_specific_topic", "PROACTIVE_JOIN", "STOP"),
        ("proactive_no_subject", "SILENCE", "STOP"),
    ),
)
def test_reference_derived_scene_matrix(case, expected_move, ending, tmp_path):
    result = asyncio.run(run_case(tmp_path, case))
    assert result.plan.move.primary_move.value == expected_move
    assert result.plan.move.ending.value == ending
    assert result.reply_has_unknown_evidence is False
    assert result.prompt_contains_social_meta_language is False
    assert result.persona_name == "爱弥斯"


def test_chorus_surface_behavior_is_target_specific(tmp_path):
    self_case = asyncio.run(run_case(tmp_path, "chorus_about_aemeath"))
    member_case = asyncio.run(run_case(tmp_path, "safe_chorus_about_member"))
    assert self_case.reply_text != self_case.scene.chorus_payload
    assert member_case.reply_text == member_case.scene.chorus_payload
    assert member_case.model_reply_calls == 0
    assert member_case.reply_text.startswith("我也来") is False
```

- [ ] **Step 2: 运行场景测试并确认红灯**

Run: `pytest -q tests/scenarios/test_aemeath_smalltalk_style.py tests/scenarios/test_autonomous_opportunities.py tests/scenarios/test_social_runtime_shadow.py`

Expected: FAIL until the ambient context no longer truncates every event to 32 characters and proactive cases use the same pipeline.

- [ ] **Step 3: 补齐 ambient 事实和 SHADOW 对比**

为 `SceneContextBuilder` 增加 `pack_event_mappings(events, focus_event_ids, max_chars)`，并让 `DirectAmbientWorker._facts()` 使用它替换固定 32 字符截断；当前事件、回复父链和 focus evidence 优先，普通背景按剩余预算裁剪。参与模型仍只决定 speak/silence，不生成回复。主动机会必须携带 `entry_reason_event_ids` 和 `literal_subject`；无具体 subject 的机会在 move 阶段成为 SILENCE。SHADOW 记录旧 act、新 scene/stance/move、生成检查结果和最终 would_reply，不改变发送副作用规则。

- [ ] **Step 4: 运行所有相关测试和静态检查**

Run: `pytest -q tests/social_runtime/test_social_context.py tests/social_runtime/test_chorus.py tests/social_runtime/test_social_scenes.py tests/social_runtime/test_stances.py tests/social_runtime/test_social_moves.py tests/social_runtime/test_participation.py tests/social_runtime/actions/test_style.py tests/social_runtime/actions/test_social_review.py tests/social_runtime/actions/test_replying.py tests/social_runtime/actions/test_output_firewall.py tests/contracts/test_social_scene_model.py tests/contracts/test_direct_ambient_worker.py tests/contracts/test_message_traces.py tests/scenarios/test_aemeath_smalltalk_style.py tests/scenarios/test_chat_mainline.py tests/scenarios/test_autonomous_opportunities.py tests/scenarios/test_social_runtime_shadow.py`

Expected: PASS. Also run `git diff --check`; expected no output.

- [ ] **Step 5: 提交场景与 SHADOW 验收**

```bash
git add groupmate/social_runtime/cognition/ambient_worker.py tests/contracts/test_direct_ambient_worker.py tests/scenarios/test_aemeath_smalltalk_style.py tests/scenarios/test_autonomous_opportunities.py tests/scenarios/test_social_runtime_shadow.py
git commit -m "test: cover aemeath social style scenarios"
```
