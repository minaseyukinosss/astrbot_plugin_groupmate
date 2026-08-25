# Affection Event Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立可审计的关系事件闭环，让直接互动产生缓慢的确定性积累，让现有 AMBIENT 模型附带提取语义关系事件，并由本地策略完成验证、计分、去重、日限额和 SHADOW 隔离。

**Architecture:** 模型只输出事件类型、对象、严重程度、置信度、摘要和来源消息，不输出分数。`RelationshipEventPolicy` 将已验证事件映射为现有八维关系投影；`RelationshipEventService` 负责按 `persona_id × group_id × subject_id` 读取当前投影、应用每日正向预算并原子记录结果。直接呼唤与续聊使用本地确定性事件；普通群聊复用 `ambient_social_assessor` 的同一次请求，不新增模型调用。

**Tech Stack:** Python 3.10+、dataclasses、SQLite、现有 DeepSeek JSON cognition boundary、pytest。

---

### Task 1: Define relationship proposals and local policy

**Files:**
- Create: `groupmate/social_runtime/society/relationship_events.py`
- Modify: `groupmate/social_runtime/society/__init__.py`
- Test: `tests/social_runtime/test_relationship_events.py`

- [ ] **Step 1: Write failing policy tests**

```python
def test_model_proposal_never_carries_a_numeric_delta():
    fields = {item.name for item in dataclasses.fields(RelationshipEventProposal)}
    assert "amount" not in fields


def test_warm_exchange_maps_to_a_small_local_delta():
    decision = RelationshipEventPolicy().decide(
        _proposal(kind="warm_exchange", confidence=0.9, severity="ordinary"),
        current=RelationshipProjection("p", "g", "u"),
        positive_delta_today=0.0,
    )
    assert decision.outcome == "ACCEPT"
    assert decision.evidence.kind == "warm_exchange"
    assert decision.evidence.amount == 2


def test_low_confidence_boundary_event_is_rejected():
    decision = RelationshipEventPolicy().decide(
        _proposal(kind="boundary_pressure", confidence=0.82, severity="severe"),
        current=RelationshipProjection("p", "g", "u"),
        positive_delta_today=0.0,
    )
    assert decision.outcome == "REJECT"
    assert decision.reason_codes == ("confidence_below_threshold",)
```

- [ ] **Step 2: Run tests and confirm red**

Run: `.venv/bin/pytest -q tests/social_runtime/test_relationship_events.py`

Expected: FAIL because `relationship_events` does not exist.

- [ ] **Step 3: Implement the proposal and policy**

Create frozen contracts:

```python
@dataclass(frozen=True)
class RelationshipEventProposal:
    event_id: str
    persona_id: str
    group_id: str
    subject_id: str
    kind: str
    confidence: float
    severity: str
    summary: str
    source_event_ids: tuple[str, ...]
    occurred_at: int
    repair_of: str | None = None
    sensitivity: str = "normal"


@dataclass(frozen=True)
class RelationshipEventDecision:
    outcome: str
    reason_codes: tuple[str, ...]
    proposal: RelationshipEventProposal
    evidence: RelationshipEvidence | None
    public_delta: float
```

Allow only the ten event kinds in the design and severity values `minor`, `ordinary`, `significant`, `severe`. Validate complete scope, one to eight source events, confidence in `[0,1]`, bounded summary and sensitivity. Confidence thresholds are `0.82` for positive semantic events, `0.90` for boundary pressure, `0.88` for repair, and `1.0` deterministic for interaction/reciprocity.

Map locally: `interaction -> familiarity +1`; `warm_exchange -> warmth +1/2/4/6`; `trust_confirmed -> trust +1/2/4/6`; `reciprocal_action -> reciprocity +1`; `play_accepted -> play_acceptance +1/2`; `reliable_help -> reliability +2/4/6`; `care_permission -> care_permission +1/2`; `boundary_pressure -> boundary_pressure +1/3/8/15`; `repair_attempt -> no projection`; `repair_confirmed -> boundary_pressure -1/-2/-4/-6`.

Compute `public_delta` by applying the proposed evidence to the current projection and comparing `PublicAffection` values. Reject positive events when accepting them would push the member's accepted positive delta for the UTC+8 calendar day above `0.8`. Negative events do not consume the positive budget.

- [ ] **Step 4: Run focused tests**

Run: `.venv/bin/pytest -q tests/social_runtime/test_relationship_events.py tests/social_runtime/test_relationships.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add groupmate/social_runtime/society/relationship_events.py groupmate/social_runtime/society/__init__.py tests/social_runtime/test_relationship_events.py
git commit -m "feat: validate relationship event policy"
```

### Task 2: Persist decisions and projections atomically

**Files:**
- Modify: `groupmate/social_runtime/persistence/repositories.py`
- Test: `tests/social_runtime/test_relationship_events.py`

- [ ] **Step 1: Write failing repository tests**

```python
def test_same_relationship_event_is_applied_once(tmp_path):
    service = _service(tmp_path)
    first = service.process(_proposal(event_id="r1"), mode="SOCIAL_RUNTIME")
    second = service.process(_proposal(event_id="r1"), mode="SOCIAL_RUNTIME")
    assert first.outcome == "ACCEPT"
    assert second.outcome == "DUPLICATE"
    assert service.snapshot("p", "g", "u").version == 1


def test_shadow_records_suggestion_without_changing_projection(tmp_path):
    service = _service(tmp_path)
    decision = service.process(_proposal(event_id="r2"), mode="SHADOW")
    assert decision.outcome == "SUGGEST"
    assert service.snapshot("p", "g", "u").version == 0
    assert service.decisions("p", "g", "u")[0].outcome == "SUGGEST"
```

- [ ] **Step 2: Run tests and confirm red**

Run: `.venv/bin/pytest -q tests/social_runtime/test_relationship_events.py -k 'once or shadow'`

Expected: FAIL because no transactional service exists.

- [ ] **Step 3: Implement `RelationshipEventService` and repository methods**

Store each proposal and decision in the existing `relationship_events.event_json`. In one `BEGIN IMMEDIATE` transaction: check `event_id`; load projection; calculate accepted positive public delta for the Asia/Shanghai calendar day; run policy; insert the decision; and, only for `SOCIAL_RUNTIME + ACCEPT`, update `relationship_projection`. A replay with identical content returns `DUPLICATE`; the same ID with different content raises `RelationshipEventIdentityConflict`.

Expose only scoped methods:

```python
process(proposal, *, mode: str) -> RelationshipEventDecision
snapshot(persona_id, group_id, subject_id) -> RelationshipProjection
decisions(persona_id, group_id, subject_id, *, since: int | None = None) -> tuple[RelationshipEventDecision, ...]
```

- [ ] **Step 4: Run focused tests**

Run: `.venv/bin/pytest -q tests/social_runtime/test_relationship_events.py tests/social_runtime/test_relationships.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add groupmate/social_runtime/persistence/repositories.py groupmate/social_runtime/society/relationship_events.py tests/social_runtime/test_relationship_events.py
git commit -m "feat: persist relationship decisions atomically"
```

### Task 3: Extend the existing AMBIENT verdict with optional relationship events

**Files:**
- Modify: `groupmate/adapters/deepseek_cognition.py`
- Modify: `groupmate/social_runtime/cognition/ambient_worker.py`
- Modify: `groupmate/social_runtime/cognition/service.py`
- Test: `tests/contracts/test_direct_ambient_worker.py`
- Test: `tests/contracts/test_direct_deepseek_cognition.py`

- [ ] **Step 1: Write failing parser tests**

```python
def test_ambient_verdict_emits_validated_relationship_observation():
    verdict = _verdict(relationship_events=[{
        "kind": "warm_exchange", "subject_id": "u1", "severity": "ordinary",
        "confidence": 0.91, "summary": "成员认真感谢了爱弥斯",
        "evidence_event_ids": ["e1"], "repair_of": None,
        "sensitivity": "normal",
    }])
    result = asyncio.run(_worker(verdict).observe_with_result(_frame(), _context()))
    relationship = [item for item in result.observations if item.kind == "relationship_event"]
    assert len(relationship) == 1
    assert relationship[0].proposition["kind"] == "warm_exchange"


def test_invalid_relationship_entry_does_not_invalidate_participation():
    verdict = _verdict(relationship_events=[{"kind": "invented"}])
    result = asyncio.run(_worker(verdict).observe_with_result(_frame(), _context()))
    assert any(item.kind == "participation_assessment" for item in result.observations)
    assert not any(item.kind == "relationship_event" for item in result.observations)
```

- [ ] **Step 2: Run parser tests and confirm red**

Run: `.venv/bin/pytest -q tests/contracts/test_direct_ambient_worker.py tests/contracts/test_direct_deepseek_cognition.py -k relationship`

Expected: FAIL because the response contract ignores relationship events.

- [ ] **Step 3: Extend one JSON request without creating another model call**

Add `relationship_events` to the DeepSeek system schema as an optional array with at most four entries. Require the model to omit uncertain events and never output a numeric score or delta. Existing verdicts without the field remain valid.

`DirectAmbientWorker` validates every entry independently against the current frame's candidate audiences and evidence IDs. Invalid entries are dropped with a bounded diagnostic code; they must not reject the valid participation assessment. Valid entries become `CognitiveObservation(kind="relationship_event")`. Add the new relationship parser diagnostic codes to the known invalid-output set only when the whole verdict is unusable; per-entry drops remain successful worker results.

- [ ] **Step 4: Run focused tests**

Run: `.venv/bin/pytest -q tests/contracts/test_direct_ambient_worker.py tests/contracts/test_direct_deepseek_cognition.py`

Expected: PASS and existing one-call assertions remain unchanged.

- [ ] **Step 5: Commit**

```bash
git add groupmate/adapters/deepseek_cognition.py groupmate/social_runtime/cognition/ambient_worker.py groupmate/social_runtime/cognition/service.py tests/contracts/test_direct_ambient_worker.py tests/contracts/test_direct_deepseek_cognition.py
git commit -m "feat: extract relationship events with ambient cognition"
```

### Task 4: Project deterministic and model relationship events in the manager

**Files:**
- Modify: `groupmate/social_runtime/manager.py`
- Modify: `groupmate/social_runtime/control/message_traces.py`
- Test: `tests/scenarios/test_chat_mainline.py`
- Test: `tests/scenarios/test_social_runtime_shadow.py`

- [ ] **Step 1: Write failing integration tests**

```python
def test_direct_call_adds_only_a_small_interaction_event(tmp_path):
    result = asyncio.run(_direct_case(tmp_path, mode="SOCIAL_RUNTIME"))
    assert result.affection.value <= 0.2
    assert result.decisions[-1].proposal.kind == "interaction"


def test_continuation_adds_reciprocity_but_replay_is_idempotent(tmp_path):
    result = asyncio.run(_continuation_case(tmp_path))
    kinds = [item.proposal.kind for item in result.decisions]
    assert kinds.count("reciprocal_action") == 1


def test_shadow_model_event_is_visible_but_does_not_change_affection(tmp_path):
    result = asyncio.run(_ambient_relationship_case(tmp_path, mode="SHADOW"))
    assert result.affection.value == 0.0
    assert result.trace["relationship"]["outcome"] == "SUGGEST"
```

- [ ] **Step 2: Run integration tests and confirm red**

Run: `.venv/bin/pytest -q tests/scenarios/test_chat_mainline.py tests/scenarios/test_social_runtime_shadow.py -k relationship`

Expected: FAIL because evaluations do not project relationship observations.

- [ ] **Step 3: Wire proposals without changing participation ownership**

After cognition returns, manager extracts non-conflicting `relationship_event` observations and converts them into scoped proposals. For `DIRECT_FAST`, if no semantic proposal exists, create deterministic `interaction`; for `CONTINUATION`, create deterministic `reciprocal_action`. Event IDs are SHA-256 identities over persona, group, subject, kind and ordered source event IDs.

Process proposals after the participation result has been accepted but independently of whether Governor chooses ACT. Relationship decisions must never modify `GovernorResult`, selected intention, reply ownership or capability permissions. Attach only `outcome`, event kind, human reason and resulting public stage to the message trace; do not expose raw model summary or internal dimensions.

- [ ] **Step 4: Run integration tests**

Run: `.venv/bin/pytest -q tests/scenarios/test_chat_mainline.py tests/scenarios/test_social_runtime_shadow.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add groupmate/social_runtime/manager.py groupmate/social_runtime/control/message_traces.py tests/scenarios/test_chat_mainline.py tests/scenarios/test_social_runtime_shadow.py
git commit -m "feat: project relationship events from chat"
```

### Task 5: Verify relationship core and document SHADOW calibration

**Files:**
- Modify: `docs/operations/social-runtime-shadow.md`

- [ ] **Step 1: Document observable outcomes**

Add a section explaining `SUGGEST`, `ACCEPT`, `REJECT`, and `DUPLICATE`; state that SHADOW never changes the projection and that one direct call should move public affection by no more than `0.2`.

- [ ] **Step 2: Run the focused suite**

Run:

```bash
.venv/bin/pytest -q \
  tests/social_runtime/test_relationship_events.py \
  tests/social_runtime/test_relationships.py \
  tests/contracts/test_direct_ambient_worker.py \
  tests/contracts/test_direct_deepseek_cognition.py \
  tests/scenarios/test_chat_mainline.py \
  tests/scenarios/test_social_runtime_shadow.py
```

Expected: PASS.

- [ ] **Step 3: Verify repository scope**

Run: `git diff --check && git status --short`

Expected: no whitespace errors and only intended files plus the existing untracked `analysis/` directory.

- [ ] **Step 4: Commit documentation**

```bash
git add docs/operations/social-runtime-shadow.md
git commit -m "docs: explain relationship shadow calibration"
```
