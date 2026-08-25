# Persona Current-State Expression Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Groupmate 使用版本化的当前 Persona 事实和关系阶段塑造回复，同时默认不显式提及世界观元素，并确保爱弥斯不再被写成仍处于“电子幽灵”状态。

**Architecture:** 新增独立的 Persona canon 契约与素材选择器；现有 `GroupmatePersonaProfile` 继续负责稳定行为规则，canon 只负责当前状态、历史、能力、日常素材和禁区。表达规划先生成自然核心反应，再由确定性的相关性与冷却门决定是否携带最多一个显式素材；回复模型只接收筛选后的安全上下文，不再看到整个素材池。关系公开分数和阶段作为只读表达输入接入，但不授权回复、工具或能力。

**Tech Stack:** Python 3.10+、dataclasses、SQLite、AstrBot 插件配置、pytest。

---

## File map

- Create `groupmate/social_runtime/persona/canon.py`: Persona 事实、时间有效性、当前快照和素材选择。
- Create `groupmate/social_runtime/persona/presets.py`: 爱弥斯当前剧情快照；与通用选择逻辑隔离。
- Modify `groupmate/social_runtime/persona/profile.py`: 读取可选的顶层 `canon`，旧配置保持兼容。
- Modify `groupmate/social_runtime/persona/__init__.py`: 导出 canon 契约。
- Modify `groupmate/social_runtime/society/relationships.py`: 公开分数与统一关系阶段解析器。
- Modify `groupmate/social_runtime/persistence/repositories.py`: 提供只读关系快照入口。
- Modify `groupmate/social_runtime/expression.py`: 关系姿态、显式素材门和禁区。
- Modify `groupmate/social_runtime/replying.py`: 只向模型传递筛选后的 Persona 上下文。
- Modify `groupmate/social_runtime/manager.py`: 按冻结配置返回 Persona 表达快照，并读取目标成员关系。
- Modify `groupmate/adapters/astrbot_bridge.py`: 把目标关系和近期输出传入规划器，维护有界的群级近期输出缓存。
- Modify `_conf_schema.json`: 暴露可配置 Persona 预设；默认使用当前爱弥斯快照。
- Modify `groupmate/settings.py`: 校验 `persona_preset`。
- Test `tests/social_runtime/test_persona_canon.py`.
- Test `tests/social_runtime/test_relationships.py`.
- Test `tests/social_runtime/test_expression.py`.
- Test `tests/social_runtime/actions/test_replying.py`.
- Test `tests/scenarios/test_chat_mainline.py`.

### Task 1: Add versioned Persona canon and the current Aemeath preset

**Files:**
- Create: `groupmate/social_runtime/persona/canon.py`
- Create: `groupmate/social_runtime/persona/presets.py`
- Modify: `groupmate/social_runtime/persona/__init__.py`
- Test: `tests/social_runtime/test_persona_canon.py`

- [ ] **Step 1: Write the failing canon tests**

```python
from groupmate.social_runtime.persona.canon import PersonaCanon, PersonaFact
from groupmate.social_runtime.persona.presets import AEMEATH_CURRENT_CANON


def test_current_snapshot_does_not_promote_expired_state():
    canon = PersonaCanon(
        current_phase=4,
        checked_at=100,
        facts=(
            PersonaFact("ghost", "history", "曾以电子幽灵存在", "3.1", 1, 3, ("电子",)),
            PersonaFact("body", "current_state", "已重归现世并拥有躯壳", "3.3", 4, None, ("身体", "现世")),
        ),
    )
    snapshot = canon.current_snapshot()
    assert [item.fact_id for item in snapshot.current_state] == ["body"]
    assert [item.fact_id for item in snapshot.history] == ["ghost"]


def test_aemeath_current_state_is_embodied_and_ghost_is_history():
    snapshot = AEMEATH_CURRENT_CANON.current_snapshot()
    assert any("重归现世" in item.text for item in snapshot.current_state)
    assert not any("电子幽灵" in item.text for item in snapshot.current_state)
    assert any("电子幽灵" in item.text for item in snapshot.history)
```

- [ ] **Step 2: Run the canon tests and confirm red**

Run: `pytest -q tests/social_runtime/test_persona_canon.py`

Expected: FAIL because `persona.canon` and `persona.presets` do not exist.

- [ ] **Step 3: Implement immutable facts and current snapshot selection**

```python
@dataclass(frozen=True)
class PersonaFact:
    fact_id: str
    category: str
    text: str
    canon_phase: str
    valid_from: int
    valid_to: int | None
    tags: tuple[str, ...] = ()

    def active_at(self, phase: int) -> bool:
        return self.valid_from <= phase and (
            self.valid_to is None or phase <= self.valid_to
        )


@dataclass(frozen=True)
class PersonaCanonSnapshot:
    current_state: tuple[PersonaFact, ...]
    history: tuple[PersonaFact, ...]
    abilities: tuple[PersonaFact, ...]
    daily_life: tuple[PersonaFact, ...]
    values: tuple[PersonaFact, ...]
    avoidances: tuple[str, ...]


@dataclass(frozen=True)
class PersonaCanon:
    current_phase: int
    checked_at: int
    facts: tuple[PersonaFact, ...]

    def current_snapshot(self) -> PersonaCanonSnapshot:
        active = tuple(item for item in self.facts if item.active_at(self.current_phase))
        history = tuple(item for item in self.facts if item.category == "history")
        return PersonaCanonSnapshot(
            current_state=tuple(item for item in active if item.category == "current_state"),
            history=history,
            abilities=tuple(item for item in active if item.category == "abilities"),
            daily_life=tuple(item for item in active if item.category == "daily_life"),
            values=tuple(item for item in active if item.category == "values"),
            avoidances=(
                "不要把当前存在描述成接入频道或系统上线",
                "不要把思考和情绪默认描述成算力、线程、信号或数据",
            ),
        )
```

`presets.py` 中只保存爱弥斯资料，不写选择逻辑。当前状态包含“已重归现世并拥有能够现实行动的躯壳”；“曾以电子幽灵存在”只放入 `history`。课程、写歌、唱歌、游戏放入 `daily_life`；像素形态、机兵和隧者兵装放入 `abilities`。

- [ ] **Step 4: Run focused tests**

Run: `pytest -q tests/social_runtime/test_persona_canon.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add groupmate/social_runtime/persona/canon.py groupmate/social_runtime/persona/presets.py groupmate/social_runtime/persona/__init__.py tests/social_runtime/test_persona_canon.py
git commit -m "feat: model versioned persona canon"
```

### Task 2: Configure the preset without coupling generic core logic to a name

**Files:**
- Modify: `groupmate/settings.py`
- Modify: `_conf_schema.json`
- Modify: `groupmate/adapters/astrbot_bridge.py`
- Modify: `groupmate/social_runtime/persona/profile.py`
- Test: `tests/shared/test_plugin_skeleton.py`
- Test: `tests/social_runtime/test_persona_profile.py`

- [ ] **Step 1: Write failing settings and compatibility tests**

```python
def test_persona_preset_defaults_to_current_aemeath_snapshot():
    settings = SocialRuntimeSettings.from_mapping({})
    assert settings.persona_preset == "aemeath_current"


def test_unknown_persona_preset_is_rejected():
    with pytest.raises(ValueError, match="persona_preset"):
        SocialRuntimeSettings.from_mapping({"persona_preset": "unknown"})


def test_old_profile_without_persona_canon_remains_valid():
    payload = GroupmatePersonaProfile.default().to_mapping()
    restored = GroupmatePersonaProfile.from_mapping(payload)
    assert restored.canon.current_snapshot().current_state == ()
```

- [ ] **Step 2: Run focused tests and confirm red**

Run: `pytest -q tests/shared/test_plugin_skeleton.py tests/social_runtime/test_persona_profile.py -k 'preset or canon'`

Expected: FAIL because settings and profiles do not expose a preset/canon.

- [ ] **Step 3: Add the preset setting and profile-owned canon**

Add to `SocialRuntimeSettings`:

```python
persona_preset: str = "aemeath_current"
```

Normalize with an explicit allowlist:

```python
persona_preset = str(source.get("persona_preset", "aemeath_current") or "").strip()
if persona_preset not in {"aemeath_current", "custom"}:
    raise ValueError("persona_preset must be aemeath_current or custom")
```

Add `_conf_schema.json` field:

```json
"persona_preset": {
  "description": "人格资料预设",
  "type": "string",
  "options": ["aemeath_current", "custom"],
  "labels": ["爱弥斯（当前剧情）", "自定义 Persona"],
  "default": "aemeath_current",
  "hint": "预设只提供人格资料，不硬编码别称；名称和别称仍使用上方配置。"
}
```

`GroupmatePersonaProfile` 新增 `canon: PersonaCanon`，`from_mapping` 对旧配置缺失 canon 时使用空 canon；`to_mapping` 输出经过验证的 canon。桥接层的默认配置按 `persona_preset` 选择 canon，但继续用 `persona_name` 和 `persona_aliases` 覆盖身份名称。核心解析器不得出现“爱弥斯”或“小爱”的条件分支。

- [ ] **Step 4: Run focused tests**

Run: `pytest -q tests/shared/test_plugin_skeleton.py tests/social_runtime/test_persona_profile.py`

Expected: PASS; old profile remains readable.

- [ ] **Step 5: Commit**

```bash
git add groupmate/settings.py _conf_schema.json groupmate/adapters/astrbot_bridge.py groupmate/social_runtime/persona/profile.py tests/shared/test_plugin_skeleton.py tests/social_runtime/test_persona_profile.py
git commit -m "feat: configure current persona preset"
```

### Task 3: Add public relationship score and a shared stage resolver

**Files:**
- Modify: `groupmate/social_runtime/society/relationships.py`
- Modify: `groupmate/social_runtime/persistence/repositories.py`
- Test: `tests/social_runtime/test_relationships.py`

- [ ] **Step 1: Write failing score and boundary tests**

```python
def test_public_affection_score_is_derived_from_dimensions():
    state = RelationshipProjection(
        "aemeath", "g1", "u1",
        familiarity=20, warmth=30, trust=10, reciprocity=10,
        play_acceptance=5, reliability=20, care_permission=10,
        boundary_pressure=0,
    )
    score = PublicAffection.from_projection(state)
    assert score.value == 15.9
    assert score.stage == RelationshipStage.KNOWS


@pytest.mark.parametrize(
    ("value", "stage"),
    [(-40, "警戒"), (-10, "疏远"), (0, "陌生"), (10, "认识"),
     (30, "熟悉"), (55, "亲近"), (80, "默契")],
)
def test_stage_boundaries_have_one_owner(value, stage):
    assert relationship_stage(value).label == stage
```

- [ ] **Step 2: Run tests and confirm red**

Run: `pytest -q tests/social_runtime/test_relationships.py`

Expected: FAIL because the public score and stage resolver do not exist.

- [ ] **Step 3: Implement the derived score and resolver**

```python
class RelationshipStage(str, Enum):
    GUARDED = "警戒"
    DISTANT = "疏远"
    STRANGER = "陌生"
    KNOWS = "认识"
    FAMILIAR = "熟悉"
    CLOSE = "亲近"
    IN_SYNC = "默契"


@dataclass(frozen=True)
class PublicAffection:
    value: float
    stage: RelationshipStage

    @classmethod
    def from_projection(cls, state: RelationshipProjection) -> "PublicAffection":
        raw = (
            state.familiarity * 0.15 + state.warmth * 0.18 + state.trust * 0.20
            + state.reciprocity * 0.12 + state.play_acceptance * 0.08
            + state.reliability * 0.12 + state.care_permission * 0.15
            - state.boundary_pressure * 0.40
        )
        value = round(max(-100.0, min(100.0, raw)), 1)
        return cls(value, relationship_stage(value))
```

Use one ordered boundary table for `relationship_stage`; do not duplicate thresholds in UI or expression code. Add a repository helper that returns `(RelationshipProjection, PublicAffection)` for a required `persona_id × group_id × subject_id` scope.

- [ ] **Step 4: Run tests**

Run: `pytest -q tests/social_runtime/test_relationships.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add groupmate/social_runtime/society/relationships.py groupmate/social_runtime/persistence/repositories.py tests/social_runtime/test_relationships.py
git commit -m "feat: derive public affection stages"
```

### Task 4: Gate explicit Persona material by relevance and recent repetition

**Files:**
- Modify: `groupmate/social_runtime/persona/canon.py`
- Modify: `groupmate/social_runtime/expression.py`
- Test: `tests/social_runtime/test_persona_canon.py`
- Test: `tests/social_runtime/test_expression.py`

- [ ] **Step 1: Write failing material-gate tests**

```python
def test_unrelated_chat_uses_no_explicit_persona_material():
    plan = ExpressionPlanner().plan(
        lane="DIRECT_FAST",
        act="respond_to_direct_interaction",
        source_text="小爱，陪我聊会儿",
        persona_profile=_aemeath_profile(),
        relationship=PublicAffection(0.0, RelationshipStage.STRANGER),
        recent_outputs=(),
    )
    assert plan.explicit_material is None
    assert plan.material_reason == "no_relevant_material"


def test_relevant_game_topic_may_select_one_daily_life_fact():
    plan = ExpressionPlanner().plan(
        lane="CONTINUATION",
        act="continue_dialogue",
        source_text="你最近在玩什么游戏",
        persona_profile=_aemeath_profile(),
        relationship=PublicAffection(35.0, RelationshipStage.FAMILIAR),
        recent_outputs=(),
    )
    assert plan.explicit_material is not None
    assert "游戏" in plan.explicit_material


def test_recent_material_is_cooled_down():
    plan = ExpressionPlanner().plan(
        lane="CONTINUATION",
        act="continue_dialogue",
        source_text="说说你的机兵",
        persona_profile=_aemeath_profile(),
        relationship=PublicAffection(35.0, RelationshipStage.FAMILIAR),
        recent_outputs=("刚刚才说过隧者兵装。",),
    )
    assert plan.explicit_material is None
    assert plan.material_reason == "recently_repeated"
```

- [ ] **Step 2: Run tests and confirm red**

Run: `pytest -q tests/social_runtime/test_persona_canon.py tests/social_runtime/test_expression.py`

Expected: FAIL because expression plans do not carry a selected material or relationship stage.

- [ ] **Step 3: Implement deterministic zero-or-one selection**

Extend `ExpressionPlan` with:

```python
relationship_stage: str
explicit_material: str | None
material_reason: str
persona_avoidances: tuple[str, ...]
```

`PersonaMaterialSelector.select` must:

1. Build normalized terms from the source message.
2. Consider only facts whose tags intersect the source text.
3. Prefer `daily_life`, then `values`, then `abilities`, then `history`.
4. Reject a candidate when its meaningful tag or text fragment appears in the last eight Bot outputs.
5. Return no material when no fact is relevant; never select randomly and never use a global output percentage.
6. Allow at most one fact and never return an expired fact.

`ExpressionPlanner` uses `relationship.stage` to set distance and boundary posture, but the stage must not change `act`, `lane`, ownership, or capability permissions.

- [ ] **Step 4: Run focused tests**

Run: `pytest -q tests/social_runtime/test_persona_canon.py tests/social_runtime/test_expression.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add groupmate/social_runtime/persona/canon.py groupmate/social_runtime/expression.py tests/social_runtime/test_persona_canon.py tests/social_runtime/test_expression.py
git commit -m "feat: gate explicit persona material"
```

### Task 5: Send only the selected Persona context to the reply model

**Files:**
- Modify: `groupmate/social_runtime/replying.py`
- Modify: `groupmate/social_runtime/manager.py`
- Modify: `groupmate/adapters/astrbot_bridge.py`
- Test: `tests/social_runtime/actions/test_replying.py`
- Test: `tests/scenarios/test_chat_mainline.py`

- [ ] **Step 1: Write failing prompt and integration tests**

```python
def test_prompt_does_not_dump_the_full_persona_material_pool():
    plan = ReplyPlanner().plan(
        _evaluation(text="小爱，陪我聊会儿"),
        now=100,
        persona_profile=_aemeath_profile(),
        relationship=_stranger_affection(),
        recent_outputs=(),
    )
    prompt = ReplyExecutor._system_prompt(plan, _aemeath_profile())
    assert '"explicit_material": null' in prompt
    assert "隧者兵装" not in prompt
    assert "写歌" not in prompt
    assert "当前存在不是电子幽灵" in prompt


def test_chat_mainline_uses_natural_core_reply_without_forced_lore(tmp_path):
    result = asyncio.run(_run_reply(tmp_path, text="小爱，陪我聊会儿"))
    system_prompt = result.model_calls[-1]["system_prompt"]
    assert "默认不要显式提及任何设定素材" in system_prompt
    assert "接入频道" not in result.generated_text
```

- [ ] **Step 2: Run tests and confirm red**

Run: `pytest -q tests/social_runtime/actions/test_replying.py tests/scenarios/test_chat_mainline.py -k 'persona or lore or material'`

Expected: FAIL because `_system_prompt` still serializes the full Persona profile and callers do not supply relationship/recent outputs.

- [ ] **Step 3: Narrow the model prompt and wire read-only relationship input**

Change `ReplyPlanner.plan` to accept:

```python
relationship: PublicAffection
recent_outputs: tuple[str, ...]
```

The bridge resolves the target member from the selected plan/evaluation, asks the manager for a scoped relationship snapshot, and passes the most recent eight successful outputs for the same group. Keep a `deque(maxlen=8)` per group; append the preview text in SHADOW and the delivered text after a usable formal execution. The cache is only a style cooldown and is allowed to reset on restart.

Replace the full profile payload in `_system_prompt` with:

```python
{
    "identity": {
        "name": persona_profile["identity"]["name"],
        "role": persona_profile["identity"]["role"],
    },
    "behavior": {
        "tone": persona_profile["expression"]["tone"],
        "language_habits": persona_profile["expression"]["language_habits"],
        "relationship_stage": plan.expression.relationship_stage,
        "reaction_stance": plan.expression.reaction_stance,
        "boundary_style": plan.expression.boundary_style,
    },
    "current_reality": [
        fact.text
        for fact in PersonaCanon.from_mapping(
            persona_profile.get("canon", {})
        ).current_snapshot().current_state
    ],
    "explicit_material": plan.expression.explicit_material,
    "avoidances": plan.expression.persona_avoidances,
}
```

Add explicit instructions: current reality is a factual constraint and does not need to be mentioned; `explicit_material is None` means do not mention lore, abilities, school, reports, songs, games, electronic signals, machinery, or other Persona props unless the user already introduced them in the supplied conversation.

Manager creates one `SQLiteSocietyRepository` and exposes `relationship_affection(group_id, subject_id)` with strict scope checks. It is read-only in this task.

- [ ] **Step 4: Run focused tests**

Run: `pytest -q tests/social_runtime/actions/test_replying.py tests/social_runtime/test_expression.py tests/scenarios/test_chat_mainline.py`

Expected: PASS.

- [ ] **Step 5: Run the small compatibility set**

Run: `pytest -q tests/social_runtime/test_persona_profile.py tests/social_runtime/test_relationships.py tests/contracts/test_projection_consumer.py tests/shared/test_plugin_skeleton.py`

Expected: PASS; old Persona config and existing relationship projection remain readable.

- [ ] **Step 6: Commit**

```bash
git add groupmate/social_runtime/replying.py groupmate/social_runtime/manager.py groupmate/adapters/astrbot_bridge.py tests/social_runtime/actions/test_replying.py tests/scenarios/test_chat_mainline.py
git commit -m "feat: generate from bounded persona context"
```

### Task 6: Verify the first slice without broad test execution

**Files:**
- Modify: `docs/operations/social-runtime-shadow.md`

- [ ] **Step 1: Document the observable behavior**

Add a “Persona 当前状态与素材门” section explaining:

- ordinary replies normally show no explicit Persona material;
- current-state facts constrain correctness but are not mandatory content;
- `电子幽灵` may only appear as history when the current topic makes it relevant;
- the runtime trace shows relationship stage and whether explicit material was selected, but not the complete private Persona pool;
- SHADOW previews are sufficient to compare naturalness before formal sending.

- [ ] **Step 2: Run only the targeted suite**

Run:

```bash
pytest -q \
  tests/social_runtime/test_persona_canon.py \
  tests/social_runtime/test_persona_profile.py \
  tests/social_runtime/test_relationships.py \
  tests/social_runtime/test_expression.py \
  tests/social_runtime/actions/test_replying.py \
  tests/scenarios/test_chat_mainline.py \
  tests/shared/test_plugin_skeleton.py
```

Expected: PASS.

- [ ] **Step 3: Check formatting and repository scope**

Run: `git diff --check && git status --short`

Expected: no whitespace errors; only intended files plus the pre-existing untracked `analysis/` directory are shown.

- [ ] **Step 4: Commit documentation**

```bash
git add docs/operations/social-runtime-shadow.md
git commit -m "docs: explain persona material gating"
```

## Deferred follow-up plans

This first plan intentionally stops at a working expression-ready relationship read path. The following independently testable plans come next:

1. Relationship event extraction, local scoring policy, daily budget, deduplication, repair, decay, and SHADOW suggestions.
2. Relationship memories and evidence-bound positive recall/resentment.
3. `查看好感度` command ownership, active-member directory, pink leaderboard rendering, text fallback, and control-plane visibility.

They must reuse the `PublicAffection` and `RelationshipStage` owner added here rather than redefining score or stage logic.
