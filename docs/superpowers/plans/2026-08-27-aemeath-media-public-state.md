# 爱弥斯媒体语义与公开状态实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让图片、表情和回复关系作为有证据的社交语义进入闲聊，同时把可公开的自我状态、关系事件和互动压力与运营私有状态严格分型。

**Architecture:** 原始媒体 URL、路径和字节继续由 `MessageMediaDirectory` 私有保存；新增的 `VerifiedMediaObservation` 只携带来源事件、媒体引用、种类和受限摘要。公开状态由确定性 `PublicStatePolicy` 决定类别、可见范围和事实，语言层只能做短句表面呈现，不能改值或把 `OPERATOR_ONLY` 放入生成上下文。

**Tech Stack:** Python 数据类、现有消息媒体目录、Persona media registry、Social Runtime 事件与 Outbox、pytest。

**Spec:** `docs/superpowers/specs/2026-08-27-aemeath-smalltalk-style-system-design.md`

**Scenario Catalog:** `docs/scenarios/aemeath-scenario-catalog.json`

## Global Constraints

- 本计划在闲聊主链计划完成后执行；命令能力计划可以并行评审，但媒体命令仍服从其归属规则。
- 回复图片、表情或视频消息不能自动重触发原媒体解析命令。
- 只允许已验证媒体摘要进入 `SceneContext`；不得根据文件名、URL 或扩展名猜画面。
- Persona 媒体发送必须通过现有注册、许可、哈希、关系和冷却检查。
- `OPERATOR_ONLY` 永不进入用户文本或表层生成提示。
- 公开状态值、原因和可见范围由确定性代码决定，模型不能改写事实。

---

## 文件职责

- 新建 `groupmate/social_runtime/media/observations.py`：受限媒体观察契约和重复触发保护。
- 修改 `groupmate/adapters/message_media.py`：从私有媒体记录产生安全引用，不公开源地址。
- 修改 `groupmate/social_runtime/social_context.py`：把已验证观察加入场景事实。
- 修改 `groupmate/social_runtime/media/contracts.py`、`registry.py`：消费 `media_intent` 和真实关系投影。
- 修改 `groupmate/social_runtime/replying.py`：允许安全媒体 part 与文本组成同一 DeliveryBundle。
- 新建 `groupmate/social_runtime/public_state.py`：公开状态类别、可见策略和确定性 notice。
- 修改 `groupmate/social_runtime/persona/self_state.py`、`society/relationship_events.py`：产生可分类状态事实。
- 修改 `groupmate/social_runtime/control/message_traces.py`：运营视图保留私有诊断，用户视图只给许可状态。
- 新建 `tests/social_runtime/media/test_observations.py`、`tests/social_runtime/test_public_state.py`。
- 修改 `tests/social_runtime/media/test_selection.py`、`tests/social_runtime/actions/test_replying.py`、`tests/scenarios/test_aemeath_smalltalk_style.py`。

### Task 1: 建立有来源的媒体观察契约

**Files:**
- Create: `groupmate/social_runtime/media/observations.py`
- Modify: `groupmate/adapters/message_media.py`
- Create: `tests/social_runtime/media/test_observations.py`
- Modify: `tests/contracts/test_message_trace_bridge.py`

**Interfaces:**
- Consumes: `SocialEventEnvelope` 的 opaque media refs 和可选视觉提供者结果。
- Produces: `VerifiedMediaObservation`；不产生能力执行请求。

- [ ] **Step 1: 写入失败的隐私与证据测试**

```python
def test_verified_media_observation_keeps_only_opaque_reference():
    item = VerifiedMediaObservation.create(
        observation_id="vision:m1:0", source_event_id="m1",
        media_ref="message-media:abc", kind="image",
        summary="一张庆祝用的表情图", confidence=0.91,
        provider_result_id="vision-result:1",
    )
    serialized = json.dumps(asdict(item), ensure_ascii=False)
    assert "http" not in serialized
    assert "/Users/" not in serialized


def test_reply_to_video_is_not_an_analysis_request():
    decision = MediaObservationPolicy().classify_reply(
        source_kind="video", replied_to_media=True, explicit_command=False,
    )
    assert decision.trigger_capability is False
```

- [ ] **Step 2: 运行测试并确认红灯**

Run: `pytest -q tests/social_runtime/media/test_observations.py`

Expected: FAIL during import.

- [ ] **Step 3: 实现冻结观察和严格来源校验**

```python
@dataclass(frozen=True)
class VerifiedMediaObservation:
    observation_id: str
    source_event_id: str
    media_ref: str
    kind: str
    summary: str
    confidence: float
    provider_result_id: str


@dataclass(frozen=True)
class MediaReplyDecision:
    trigger_capability: bool
    reason_code: str
```

只允许 `MessageMediaDirectory.contains` 返回真且 provider_result_id 非空的引用创建观察。摘要限定长度并过滤 URL、长数字和本地路径。`explicit_command=False` 时任何 reply-to-media 都返回 `trigger_capability=False`。

- [ ] **Step 4: 运行测试并确认绿灯**

Run: `pytest -q tests/social_runtime/media/test_observations.py tests/contracts/test_message_trace_bridge.py`

Expected: PASS.

- [ ] **Step 5: 提交媒体观察**

```bash
git add groupmate/social_runtime/media/observations.py groupmate/adapters/message_media.py tests/social_runtime/media/test_observations.py tests/contracts/test_message_trace_bridge.py
git commit -m "feat: record verified media observations"
```

### Task 2: 将媒体语义加入场景而不重触发命令

**Files:**
- Modify: `groupmate/social_runtime/social_context.py`
- Modify: `groupmate/adapters/astrbot_bridge.py`
- Modify: `tests/social_runtime/test_social_context.py`
- Modify: `tests/scenarios/test_aemeath_smalltalk_style.py`

**Interfaces:**
- Consumes: `VerifiedMediaObservation`。
- Produces: `SceneContext.media_observations` 和媒体互动场景。

- [ ] **Step 1: 写入失败的纯媒体互动测试**

```python
def test_image_only_direct_message_reaches_scene_with_verified_summary(tmp_path):
    result = asyncio.run(run_media_case(tmp_path, kind="image", text="", direct=True))
    assert result.scene.literal_subject == "一张庆祝用的表情图"
    assert result.scene.user_move == "shares_media_for_reaction"
    assert result.capability_calls == 0


def test_replying_to_bot_video_does_not_repeat_video_summary(tmp_path):
    result = asyncio.run(run_media_case(tmp_path, kind="video", reply_to_bot_media=True))
    assert result.capability_calls == 0
    assert result.plan.move.primary_move in {SocialMove.DIRECT_ANSWER, SocialMove.TEASE_FROM_CONTEXT}
```

- [ ] **Step 2: 运行测试并确认红灯**

Run: `pytest -q tests/social_runtime/test_social_context.py -k 'media' tests/scenarios/test_aemeath_smalltalk_style.py -k 'media'`

Expected: FAIL because SceneContext does not carry verified media observations.

- [ ] **Step 3: 接入上下文与场景 facts**

`SceneContextBuilder.build()` 新增 `media_observations` 参数，按 source_event_id 只选择当前消息、回复父链和 focus evidence 的观察。模型 facts 仅包含 observation ID、kind、summary、confidence；不包含 media_ref。没有文本但有验证摘要的直接互动可形成 `media_reaction` 场景；没有摘要时 literal subject 使用确定性“这张图片/这个表情/这个视频”，不得猜内容。

- [ ] **Step 4: 运行测试并确认绿灯**

Run: `pytest -q tests/social_runtime/test_social_context.py tests/scenarios/test_aemeath_smalltalk_style.py -k 'media or image or video'`

Expected: PASS;媒体回复不产生原能力请求。

- [ ] **Step 5: 提交媒体语义接入**

```bash
git add groupmate/social_runtime/social_context.py groupmate/adapters/astrbot_bridge.py tests/social_runtime/test_social_context.py tests/scenarios/test_aemeath_smalltalk_style.py
git commit -m "feat: use verified media in social scenes"
```

### Task 3: 让社交动作安全选择并发送 Persona 媒体

**Files:**
- Modify: `groupmate/social_runtime/media/contracts.py`
- Modify: `groupmate/social_runtime/media/registry.py`
- Modify: `groupmate/social_runtime/replying.py`
- Modify: `tests/social_runtime/media/test_selection.py`
- Modify: `tests/social_runtime/actions/test_replying.py`

**Interfaces:**
- Consumes: `SocialMovePlan.media_intent`, `StyleDirective.media_policy`, `RelationshipProjection`。
- Produces: 可选 media DeliveryPart；文本仍可独立发送。

- [ ] **Step 1: 写入失败的关系与冷却测试**

```python
def test_media_intent_cannot_bypass_relationship_boundary(tmp_path):
    selection = _selector(tmp_path).select(
        (_asset(min_familiarity=20, max_boundary_pressure=10),),
        _context(familiarity=60, boundary_pressure=70, text_sufficient=False),
    )
    assert selection.selected_asset_id is None
    assert "boundary_pressure" in selection.reason_codes


def test_reply_bundle_can_contain_text_then_registered_image(tmp_path):
    bundle = _execute_media_plan(tmp_path)
    assert [part.kind.value for part in bundle.parts] == ["text", "image"]
    assert bundle.parts[1].payload["asset_id"].startswith("media:")
```

- [ ] **Step 2: 运行测试并确认红灯**

Run: `pytest -q tests/social_runtime/media/test_selection.py tests/social_runtime/actions/test_replying.py -k 'media'`

Expected: FAIL because ReplyExecutor currently creates text-only bundles.

- [ ] **Step 3: 接入选择与发送**

只有 `media_intent=SEND_IF_AVAILABLE` 且 `media_policy` 允许时创建 `MediaSelectionContext`。使用真实 familiarity/boundary_pressure、mode、act、culture 和 recent uses 调用 `MediaSelector`。选中资产后由 registry 再验证文件，再创建 `DeliveryPartKind.IMAGE`；未选中时发送文本，不把媒体选择失败交给语言模型解释。

- [ ] **Step 4: 运行测试并确认绿灯**

Run: `pytest -q tests/social_runtime/media/test_selection.py tests/social_runtime/media/test_registry.py tests/social_runtime/actions/test_replying.py -k 'media or bundle'`

Expected: PASS.

- [ ] **Step 5: 提交媒体发送**

```bash
git add groupmate/social_runtime/media/contracts.py groupmate/social_runtime/media/registry.py groupmate/social_runtime/replying.py tests/social_runtime/media/test_selection.py tests/social_runtime/actions/test_replying.py
git commit -m "feat: send governed persona media"
```

### Task 4: 对公开状态和运营私有状态分型

**Files:**
- Create: `groupmate/social_runtime/public_state.py`
- Modify: `groupmate/social_runtime/persona/self_state.py`
- Modify: `groupmate/social_runtime/society/relationship_events.py`
- Create: `tests/social_runtime/test_public_state.py`

**Interfaces:**
- Consumes: `GlobalSelfState`, `RelationshipEventDecision`, SocialPressure evidence 和群级可见配置。
- Produces: `PublicStateNotice | None`。

- [ ] **Step 1: 写入失败的状态可见矩阵**

```python
@pytest.mark.parametrize(
    ("kind", "visible", "category"),
    (
        ("energy", True, "PUBLIC_STATUS"),
        ("relationship_delta", True, "RELATIONSHIP_EVENT"),
        ("interaction_pressure", True, "SOCIAL_PRESSURE"),
        ("database_id", False, "OPERATOR_ONLY"),
        ("traceback", False, "OPERATOR_ONLY"),
    ),
)
def test_public_state_policy_classifies_visibility(kind, visible, category):
    result = PublicStatePolicy().notice(_state_fact(kind), _visibility_config())
    assert (result is not None) is visible
    assert PublicStatePolicy.category(kind).value == category
```

- [ ] **Step 2: 运行测试并确认红灯**

Run: `pytest -q tests/social_runtime/test_public_state.py`

Expected: FAIL during import.

- [ ] **Step 3: 实现确定性 notice 和公开配置**

```python
class StateVisibility(str, Enum):
    PUBLIC_STATUS = "PUBLIC_STATUS"
    RELATIONSHIP_EVENT = "RELATIONSHIP_EVENT"
    SOCIAL_PRESSURE = "SOCIAL_PRESSURE"
    OPERATOR_ONLY = "OPERATOR_ONLY"


@dataclass(frozen=True)
class PublicStateNotice:
    notice_id: str
    category: StateVisibility
    fact_text: str
    source_event_ids: tuple[str, ...]
    audience: str
    occurred_at: int
```

Policy 使用确定性渲染函数，例如 energy 只从实际区间生成“今天有点没精神”，关系事件使用已批准 public_delta 的方向但不输出内部维度，互动压力使用有界证据计数。所有 notice 均携带来源；`OPERATOR_ONLY` 始终返回 None。

- [ ] **Step 4: 运行测试并确认绿灯**

Run: `pytest -q tests/social_runtime/test_public_state.py tests/social_runtime/test_self_state.py tests/social_runtime/test_relationship_events.py`

Expected: PASS.

- [ ] **Step 5: 提交状态分型**

```bash
git add groupmate/social_runtime/public_state.py groupmate/social_runtime/persona/self_state.py groupmate/social_runtime/society/relationship_events.py tests/social_runtime/test_public_state.py tests/social_runtime/test_self_state.py tests/social_runtime/test_relationship_events.py
git commit -m "feat: classify public social state notices"
```

### Task 5: 将许可状态发送并完成隐私回归

**Files:**
- Modify: `groupmate/adapters/astrbot_bridge.py`
- Modify: `groupmate/social_runtime/control/message_traces.py`
- Modify: `groupmate/social_runtime/replying.py`
- Modify: `tests/contracts/test_message_traces.py`
- Modify: `tests/scenarios/test_aemeath_smalltalk_style.py`

**Interfaces:**
- Consumes: `PublicStateNotice`。
- Produces: 用户许可短句或 operator-only trace；模型不能修改 notice 事实。

- [ ] **Step 1: 写入失败的用户与运营视图测试**

```python
def test_public_notice_can_be_sent_but_operator_fields_never_reach_prompt(tmp_path):
    result = asyncio.run(run_state_case(tmp_path, category="PUBLIC_STATUS"))
    assert "有点没精神" in result.sent_text
    assert "state_version" not in result.model_prompt
    assert "cognitive_load" not in result.model_prompt


def test_operator_only_state_stays_in_trace(tmp_path):
    result = asyncio.run(run_state_case(tmp_path, category="OPERATOR_ONLY"))
    assert result.sent_text is None
    assert result.trace["operator_diagnostics"]["error_code"] == "provider_timeout"
```

- [ ] **Step 2: 运行测试并确认红灯**

Run: `pytest -q tests/contracts/test_message_traces.py -k 'public_state or operator' tests/scenarios/test_aemeath_smalltalk_style.py -k 'state'`

Expected: FAIL because notices are not yet delivered or separated in trace.

- [ ] **Step 3: 实现发送与追踪分离**

公开 notice 作为带 `DecisionFact` 的确定性短句进入 ReplyPlan；生成器可调整语序但必须覆盖 notice fact。数值、原始状态键和调试字段只写入 trace 的 operator diagnostics。`RELATIONSHIP_EVENT` 和 `SOCIAL_PRESSURE` 只有群配置允许时发送；未允许时仍可在运营追踪中显示类别和来源。

- [ ] **Step 4: 运行完整定向套件**

Run: `pytest -q tests/social_runtime/media/test_observations.py tests/social_runtime/media/test_selection.py tests/social_runtime/media/test_registry.py tests/social_runtime/test_public_state.py tests/social_runtime/actions/test_replying.py tests/contracts/test_message_traces.py tests/scenarios/test_aemeath_smalltalk_style.py`

Expected: PASS. Also run `git diff --check`; expected no output.

- [ ] **Step 5: 提交媒体与状态验收**

```bash
git add groupmate/adapters/astrbot_bridge.py groupmate/social_runtime/control/message_traces.py groupmate/social_runtime/replying.py tests/contracts/test_message_traces.py tests/scenarios/test_aemeath_smalltalk_style.py
git commit -m "test: verify media and public state boundaries"
```
