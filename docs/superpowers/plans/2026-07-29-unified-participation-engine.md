# Unified Participation Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Replace the old score-based participation path with a deterministic `ParticipationDecisionEngine`（统一参与决策引擎） while preserving copied-text @ as an Aemeath-styled bypass.

**Architecture:** `COPIED_AT`（复制文本 @） exits before participation decisions through `CopiedAtGuard`（复制文本 @ 旁路）. Real direct calls flow through `DirectAddressPressureTracker`（直接呼叫压力跟踪器） and `ParticipationDecisionEngine`（统一参与决策引擎）, which outputs action, act, posture, quote policy, media policy, and reason codes. Old `OpportunityArbiter`（机会仲裁器） and `ReplyIntentPlanner`（回复意图规划器） are removed from the online workflow.

**Tech Stack:** Python 3.7-compatible dataclasses/enums, pytest, existing Groupmate domain models, existing fake ports in `tests/fakes.py`.

**Execution Status:** Completed on 2026-07-31.

**Delivered By:** `47389eb`（复制文本 @ 旁路）, `c6a5b5b`（直接呼叫压力）, `81b4625` / `063034d`（参与决策契约与开放参与门）, `2536b3a`（工作流接入）, `03425b8`（旧参与机制移除）, and `181bf14` / `fdb8746`（persona-scoped runtime integration and final legacy runtime cleanup）.

**Completion Evidence:** `pytest` 602 passed; focused copied @ / direct pressure / participation / shadow projector tests 37 passed; `git diff --check` passed; old online participation residual scan found only regression assertions and new `groupmate.host.config` imports. Local Phase 3 shadow export was not regenerated because `SHADOW_EXPORT_DIR` and `SHADOW_TARGET_UIN` are not set in this session.

---

## File Structure

- Create `groupmate/engine/copied_at.py`: copied-text @ guard and fixed Aemeath-style tip.
- Create `groupmate/engine/direct_pressure.py`: pressure levels and in-memory per-group/per-user direct-call tracking.
- Create `groupmate/engine/participation_types.py`: input/output contracts for the unified decision engine.
- Create `groupmate/engine/participation.py`: deterministic decision gates for bypass, ownership, direct obligation, open participation, posture, quote, and media.
- Modify `groupmate/engine/workflow.py`: call copied @ guard first, then new participation engine; remove online `OpportunityArbiter.evaluate` and `ReplyIntentPlanner.plan` calls.
- Modify `groupmate/core/scenes.py`: ensure copied-text @ is not modeled as a normal direct scene in new flow; keep scene helper usable for non-copied triggers.
- Modify `groupmate/models.py`, `groupmate/config.py`, `groupmate/host/bridge.py`: remove `v3_opportunity_enabled` and add direct-pressure config fields.
- Modify `eval/shadow_projector.py`: use copied @ guard and the participation decision engine.
- Create/modify tests: `tests/test_copied_at_guard.py`, `tests/test_direct_pressure.py`, `tests/test_participation_decision.py`, `tests/test_workflow_participation.py`, existing config/workflow/shadow tests.

---

### Task 1: Copied Text @ Bypass（复制文本 @ 旁路）

**Files:**
- Create: `groupmate/engine/copied_at.py`
- Create: `tests/test_copied_at_guard.py`
- Modify: `groupmate/engine/workflow.py`
- Modify: `tests/test_workflow.py`

- [x] **Step 1: Write failing tests for Aemeath-styled copied @ tip**

```python
# tests/test_copied_at_guard.py
from groupmate.engine.copied_at import copied_at_tip, is_copied_at
from groupmate.models import TriggerKind


def test_copied_at_tip_uses_aemeath_style_and_alias():
    assert copied_at_tip("爱弥斯") == "复制出来的 @ 不算数哦，要叫爱弥斯的话，用真正的 @。"


def test_copied_at_tip_defaults_to_aemeath_name():
    assert copied_at_tip("") == "复制出来的 @ 不算数哦，要叫爱弥斯的话，用真正的 @。"


def test_copied_at_guard_only_matches_copied_at_trigger():
    assert is_copied_at(TriggerKind.COPIED_AT) is True
    assert is_copied_at(TriggerKind.NATIVE_DIRECT) is False
    assert is_copied_at(TriggerKind.ALIAS_DIRECT) is False
```

Update existing workflow assertion:

```python
# tests/test_workflow.py
def test_copied_at_sends_tip_without_llm(topic_snapshot, balanced_policy):
    platform = FakePlatform()
    memory = FakeMemoryRepository()
    generator = StaticGenerationModel("不该生成这句")
    workflow = build_workflow(
        generator=generator, platform=platform, memory=memory
    )

    outcome = asyncio.run(
        workflow.evaluate(
            topic_snapshot,
            TriggerKind.COPIED_AT,
            balanced_policy,
            trigger_alias="爱弥斯",
        )
    )

    assert outcome.sent is True
    assert outcome.reason == "copied_at_tip"
    assert platform.sent[0]["text"] == "复制出来的 @ 不算数哦，要叫爱弥斯的话，用真正的 @。"
    assert generator.calls == 0
    assert memory.outbox[outcome.decision_id]["status"] == "sent"
    assert len(memory.messages) == 1
    assert memory.messages[0].metadata["origin"] == "bot_delivery"
```

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_copied_at_guard.py tests/test_workflow.py::test_copied_at_sends_tip_without_llm -q`

Expected: FAIL because `groupmate.engine.copied_at` does not exist and workflow still sends the old generic string.

- [x] **Step 3: Implement copied @ helper**

```python
# groupmate/engine/copied_at.py
"""Copied-text @ handling that never enters participation decisions."""

from __future__ import annotations

from ..models import TriggerKind

_DEFAULT_ALIAS = "爱弥斯"
_TIP_TEMPLATE = "复制出来的 @ 不算数哦，要叫{name}的话，用真正的 @。"


def is_copied_at(trigger: TriggerKind) -> bool:
    """is_copied_at（是否复制文本 @）：只匹配 COPIED_AT 触发。"""

    return trigger is TriggerKind.COPIED_AT


def copied_at_tip(alias: str) -> str:
    """copied_at_tip（复制 @ 提示）：固定爱弥斯风格短提示。"""

    name = str(alias or "").strip() or _DEFAULT_ALIAS
    return _TIP_TEMPLATE.format(name=name)
```

- [x] **Step 4: Update workflow copied @ tip**

```python
# groupmate/engine/workflow.py imports
from .copied_at import copied_at_tip, is_copied_at

# in CognitiveWorkflow.evaluate
if is_copied_at(trigger):
    return await self._send_copied_at_tip(
        decision_id, topic, trigger_alias, now, still_valid
    )

# in _send_copied_at_tip
text = copied_at_tip(trigger_alias)
```

- [x] **Step 5: Run tests to verify pass**

Run: `pytest tests/test_copied_at_guard.py tests/test_workflow.py::test_copied_at_sends_tip_without_llm -q`

Expected: PASS.

- [x] **Step 6: Commit**

```bash
git add groupmate/engine/copied_at.py tests/test_copied_at_guard.py groupmate/engine/workflow.py tests/test_workflow.py
git commit -m "feat: style copied at bypass"
```

---

### Task 2: Direct Address Pressure（直接呼叫压力）

**Files:**
- Create: `groupmate/engine/direct_pressure.py`
- Create: `tests/test_direct_pressure.py`
- Modify: `groupmate/models.py`
- Modify: `groupmate/config.py`
- Modify: `groupmate/host/bridge.py`
- Modify: `tests/test_config.py`

- [x] **Step 1: Write failing direct-pressure tests**

```python
# tests/test_direct_pressure.py
from groupmate.engine.direct_pressure import (
    DirectAddressPressureLevel,
    DirectAddressPressureTracker,
)
from groupmate.models import ChatMessage, TriggerKind


def msg(text="爱弥斯", ts=100, sender="u1"):
    return ChatMessage(
        message_id=str(ts),
        group_id="g1",
        sender_id=sender,
        sender_name="Alice",
        text=text,
        timestamp=ts,
    )


def test_repeated_bare_direct_escalates_to_pester():
    tracker = DirectAddressPressureTracker(window_seconds=600, nudge_count=2, pester_count=3)

    first = tracker.observe(msg(ts=100), TriggerKind.ALIAS_DIRECT, now=100, aliases=("爱弥斯",))
    second = tracker.observe(msg(ts=120), TriggerKind.ALIAS_DIRECT, now=120, aliases=("爱弥斯",))
    third = tracker.observe(msg(ts=140), TriggerKind.ALIAS_DIRECT, now=140, aliases=("爱弥斯",))

    assert first.level is DirectAddressPressureLevel.NORMAL
    assert second.level is DirectAddressPressureLevel.NUDGE
    assert third.level is DirectAddressPressureLevel.PESTER


def test_contentful_direct_resets_pressure():
    tracker = DirectAddressPressureTracker(window_seconds=600, nudge_count=2, pester_count=3)
    tracker.observe(msg(ts=100), TriggerKind.ALIAS_DIRECT, now=100, aliases=("爱弥斯",))
    tracker.observe(msg(ts=120), TriggerKind.ALIAS_DIRECT, now=120, aliases=("爱弥斯",))

    state = tracker.observe(msg(text="爱弥斯 这个怎么弄？", ts=130), TriggerKind.ALIAS_DIRECT, now=130, aliases=("爱弥斯",))

    assert state.level is DirectAddressPressureLevel.NORMAL
    assert state.count == 0


def test_copied_at_is_excluded_from_pressure():
    tracker = DirectAddressPressureTracker(window_seconds=600, nudge_count=2, pester_count=3)

    state = tracker.observe(msg(ts=100), TriggerKind.COPIED_AT, now=100, aliases=("爱弥斯",))

    assert state.level is DirectAddressPressureLevel.NORMAL
    assert state.count == 0
```

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_direct_pressure.py -q`

Expected: FAIL because `direct_pressure.py` does not exist.

- [x] **Step 3: Add policy config fields**

```python
# groupmate/models.py GroupPolicy
direct_pressure_window_seconds: int = 600
direct_pressure_nudge_count: int = 2
direct_pressure_pester_count: int = 3
```

```python
# groupmate/config.py PluginSettings
direct_pressure_window_seconds: int = 600
direct_pressure_nudge_count: int = 2
direct_pressure_pester_count: int = 3

# in from_mapping
direct_pressure_window_seconds=_bounded_int(
    data.get("direct_pressure_window_seconds", 600), 600, 60, 3600
),
direct_pressure_nudge_count=_bounded_int(
    data.get("direct_pressure_nudge_count", 2), 2, 2, 10
),
direct_pressure_pester_count=_bounded_int(
    data.get("direct_pressure_pester_count", 3), 3, 3, 20
),
```

```python
# groupmate/host/bridge.py when building GroupPolicy
direct_pressure_window_seconds=int(self._setting("direct_pressure_window_seconds", 600)),
direct_pressure_nudge_count=int(self._setting("direct_pressure_nudge_count", 2)),
direct_pressure_pester_count=int(self._setting("direct_pressure_pester_count", 3)),
```

- [x] **Step 4: Implement pressure tracker**

```python
# groupmate/engine/direct_pressure.py
"""Direct call pressure derived from repeated real direct addresses."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Sequence, Tuple

from ..models import ChatMessage, StringEnum, TriggerKind

_CONTENTFUL = re.compile(r"[？?]|吗$|呢$|怎么|什么|谁|哪|帮|看|查|找|生成|解释|为什么")
_ALIAS_PADDING = re.compile(r"[\s@＠,，。.!！?？~～:：、]+")
_COUNTED_TRIGGERS = frozenset({
    TriggerKind.NATIVE_DIRECT,
    TriggerKind.ALIAS_DIRECT,
    TriggerKind.CONTINUATION,
})


class DirectAddressPressureLevel(StringEnum):
    """DirectAddressPressureLevel（直接呼叫压力档位）。"""

    NORMAL = "normal"
    NUDGE = "nudge"
    PESTER = "pester"
    AFTER_BOUNDARY = "after_boundary"


@dataclass(frozen=True)
class DirectAddressPressureState:
    """DirectAddressPressureState（直接呼叫压力状态）。"""

    level: DirectAddressPressureLevel
    count: int = 0
    reason_codes: Tuple[str, ...] = ()


class DirectAddressPressureTracker:
    """DirectAddressPressureTracker（直接呼叫压力跟踪器）。"""

    def __init__(self, *, window_seconds: int = 600, nudge_count: int = 2, pester_count: int = 3) -> None:
        self.window_seconds = max(1, int(window_seconds))
        self.nudge_count = max(2, int(nudge_count))
        self.pester_count = max(self.nudge_count + 1, int(pester_count))
        self._events: Dict[Tuple[str, str], Tuple[int, ...]] = {}

    def observe(self, message: ChatMessage, trigger: TriggerKind, *, now: int, aliases: Sequence[str]) -> DirectAddressPressureState:
        key = (message.group_id, message.sender_id)
        if trigger is TriggerKind.COPIED_AT or trigger not in _COUNTED_TRIGGERS:
            return DirectAddressPressureState(DirectAddressPressureLevel.NORMAL, 0, ("pressure_excluded", trigger.value))
        if self._has_content(message.text, aliases):
            self._events.pop(key, None)
            return DirectAddressPressureState(DirectAddressPressureLevel.NORMAL, 0, ("pressure_reset_contentful",))
        cutoff = int(now) - self.window_seconds
        kept = tuple(ts for ts in self._events.get(key, ()) if ts >= cutoff) + (int(now),)
        self._events[key] = kept
        count = len(kept)
        if count >= self.pester_count:
            return DirectAddressPressureState(DirectAddressPressureLevel.PESTER, count, ("pressure_pester",))
        if count >= self.nudge_count:
            return DirectAddressPressureState(DirectAddressPressureLevel.NUDGE, count, ("pressure_nudge",))
        return DirectAddressPressureState(DirectAddressPressureLevel.NORMAL, count, ("pressure_normal",))

    @staticmethod
    def _has_content(text: str, aliases: Sequence[str]) -> bool:
        cleaned = (text or "").strip()
        if not cleaned:
            return False
        compact = _ALIAS_PADDING.sub("", cleaned).casefold()
        for alias in aliases or ():
            if compact == _ALIAS_PADDING.sub("", str(alias or "")).casefold():
                return False
        return bool(_CONTENTFUL.search(cleaned) or len(compact) > 8)
```

- [x] **Step 5: Run tests**

Run: `pytest tests/test_direct_pressure.py tests/test_config.py -q`

Expected: PASS after config tests are updated for the new fields.

- [x] **Step 6: Commit**

```bash
git add groupmate/engine/direct_pressure.py tests/test_direct_pressure.py groupmate/models.py groupmate/config.py groupmate/host/bridge.py tests/test_config.py
git commit -m "feat: track direct address pressure"
```

---

### Task 3: Participation Contracts（参与决策契约）

**Files:**
- Create: `groupmate/engine/participation_types.py`
- Create: `tests/test_participation_decision.py`

- [x] **Step 1: Write failing contract tests**

```python
# tests/test_participation_decision.py
from groupmate.core.response_act import ResponseAct
from groupmate.engine.direct_pressure import DirectAddressPressureLevel, DirectAddressPressureState
from groupmate.engine.participation_types import (
    MediaPolicy,
    ParticipationAction,
    ParticipationDecision,
    ParticipationObligation,
)
from groupmate.models import InteractionScene, QuoteMode
from groupmate.social.affinity import ResponsePosture


def test_participation_decision_normalizes_reason_codes():
    decision = ParticipationDecision.speak(
        scene=InteractionScene.DIRECT_ADDRESS,
        act=ResponseAct.ACKNOWLEDGE,
        posture=ResponsePosture.POLITE,
        obligation=ParticipationObligation.DIRECT_REQUIRED,
        reason_codes=("direct", "bare"),
        contribution="短应声",
    )

    assert decision.action is ParticipationAction.SPEAK
    assert decision.reason_codes == ("direct", "bare")
    assert decision.quote_mode is QuoteMode.NEVER
    assert decision.media_policy.decorative_allowed is False


def test_pressure_state_can_be_stored_on_decision():
    pressure = DirectAddressPressureState(DirectAddressPressureLevel.NUDGE, 2)
    decision = ParticipationDecision.silence(
        scene=InteractionScene.AMBIENT_CONTRIBUTION,
        reason_codes=("empty_echo",),
        pressure=pressure,
    )

    assert decision.action is ParticipationAction.SILENCE
    assert decision.pressure.level is DirectAddressPressureLevel.NUDGE
```

- [x] **Step 2: Run tests to verify fail**

Run: `pytest tests/test_participation_decision.py::test_participation_decision_normalizes_reason_codes -q`

Expected: FAIL because `participation_types.py` does not exist.

- [x] **Step 3: Implement participation types**

```python
# groupmate/engine/participation_types.py
"""Participation decision contracts for the unified engine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from ..core.response_act import ResponseAct
from ..models import InteractionScene, QuoteMode, StringEnum
from ..social.affinity import ResponsePosture
from .direct_pressure import DirectAddressPressureState


class ParticipationAction(StringEnum):
    """ParticipationAction（参与动作）。"""

    SPEAK = "speak"
    SILENCE = "silence"


class ParticipationObligation(StringEnum):
    """ParticipationObligation（回应义务）。"""

    DIRECT_REQUIRED = "direct_required"
    OPEN_OPTIONAL = "open_optional"
    NONE = "none"


@dataclass(frozen=True)
class MediaPolicy:
    """MediaPolicy（媒体策略）。"""

    decorative_allowed: bool = False
    visual_reaction_allowed: bool = False
    capability_media_allowed: bool = False


@dataclass(frozen=True)
class ParticipationDecision:
    """ParticipationDecision（参与决策）。"""

    action: ParticipationAction
    scene: InteractionScene
    act: Optional[ResponseAct]
    posture: ResponsePosture
    obligation: ParticipationObligation
    reason_codes: Tuple[str, ...]
    contribution: str = ""
    quote_mode: QuoteMode = QuoteMode.NEVER
    media_policy: MediaPolicy = MediaPolicy()
    pressure: Optional[DirectAddressPressureState] = None

    @classmethod
    def speak(cls, *, scene, act, posture, obligation, reason_codes, contribution, quote_mode=QuoteMode.NEVER, media_policy=MediaPolicy(), pressure=None):
        return cls(
            ParticipationAction.SPEAK,
            scene,
            act,
            posture,
            obligation,
            tuple(reason_codes or ()),
            str(contribution or "").strip(),
            quote_mode,
            media_policy,
            pressure,
        )

    @classmethod
    def silence(cls, *, scene, reason_codes, posture=ResponsePosture.POLITE, pressure=None):
        return cls(
            ParticipationAction.SILENCE,
            scene,
            None,
            posture,
            ParticipationObligation.NONE,
            tuple(reason_codes or ()),
            "",
            QuoteMode.NEVER,
            MediaPolicy(),
            pressure,
        )
```

- [x] **Step 4: Run tests**

Run: `pytest tests/test_participation_decision.py -q`

Expected: PASS for contract tests.

- [x] **Step 5: Commit**

```bash
git add groupmate/engine/participation_types.py tests/test_participation_decision.py
git commit -m "feat: add participation decision contracts"
```

---

### Task 4: Direct Participation Engine（明确呼叫决策引擎）

**Files:**
- Create: `groupmate/engine/participation.py`
- Modify: `tests/test_participation_decision.py`

- [x] **Step 1: Add failing direct decision tests**

```python
# append to tests/test_participation_decision.py
from groupmate.core.addressee import AddresseeResolver
from groupmate.engine.direct_pressure import DirectAddressPressureTracker
from groupmate.engine.participation import ParticipationDecisionEngine
from groupmate.models import ChatMessage, GroupPolicy, OpportunityAction, TargetingDecision, TopicSnapshot, TriggerKind
from groupmate.persona.aemeath import AEMEATH_PARTICIPATION_PROFILE
from groupmate.social.affinity import AffinityBand, AffinitySnapshot, ResponsePosture


def message(text="爱弥斯", **overrides):
    values = dict(
        message_id="m1", group_id="g1", sender_id="u1", sender_name="Alice", text=text, timestamp=100
    )
    values.update(overrides)
    return ChatMessage(**values)


def topic_with(text):
    msg = message(text)
    return TopicSnapshot("t1", "g1", (msg,), msg.timestamp, msg.timestamp)


def decide(text, trigger=TriggerKind.ALIAS_DIRECT, band=AffinityBand.NEUTRAL):
    topic = topic_with(text)
    targeting = AddresseeResolver().resolve(topic, trigger, aliases=("爱弥斯",))
    engine = ParticipationDecisionEngine(pressure=DirectAddressPressureTracker())
    return engine.decide(
        topic=topic,
        trigger=trigger,
        policy=GroupPolicy(),
        targeting=targeting,
        now=100,
        aliases=("爱弥斯",),
        affinity=AffinitySnapshot(band, ResponsePosture.POLITE),
        persona=AEMEATH_PARTICIPATION_PROFILE,
        recent_outputs=(),
    )


def test_alias_direct_bare_address_speaks_acknowledge():
    decision = decide("爱弥斯")

    assert decision.action is ParticipationAction.SPEAK
    assert decision.obligation is ParticipationObligation.DIRECT_REQUIRED
    assert decision.act is ResponseAct.ACKNOWLEDGE
    assert decision.contribution == "短应声，不主动扩展话题"


def test_copied_at_never_reaches_participation_engine():
    decision = decide("@爱弥斯", TriggerKind.COPIED_AT)

    assert decision.action is ParticipationAction.SILENCE
    assert "copied_at_bypassed" in decision.reason_codes
```

- [x] **Step 2: Run tests to verify fail**

Run: `pytest tests/test_participation_decision.py -q`

Expected: FAIL because `participation.py` does not exist.

- [x] **Step 3: Implement minimal direct engine**

```python
# groupmate/engine/participation.py
"""Unified deterministic participation decision engine."""

from __future__ import annotations

from typing import Sequence

from ..core.response_act import ResponseAct
from ..core.scenes import classify_scene
from ..models import GroupPolicy, InteractionScene, QuoteMode, TargetingDecision, TopicSnapshot, TriggerKind
from ..persona.aemeath.behavior_profile import PersonaParticipationProfile
from ..social.affinity import AffinityBand, AffinitySnapshot, ResponsePosture
from .direct_pressure import DirectAddressPressureLevel, DirectAddressPressureTracker
from .participation_types import MediaPolicy, ParticipationDecision, ParticipationObligation

_DIRECT = frozenset({TriggerKind.NATIVE_DIRECT, TriggerKind.ALIAS_DIRECT, TriggerKind.CONTINUATION})


class ParticipationDecisionEngine:
    """ParticipationDecisionEngine（统一参与决策引擎）。"""

    def __init__(self, *, pressure: DirectAddressPressureTracker = None) -> None:
        self.pressure = pressure or DirectAddressPressureTracker()

    def decide(self, *, topic: TopicSnapshot, trigger: TriggerKind, policy: GroupPolicy, targeting: TargetingDecision, now: int, aliases: Sequence[str], affinity: AffinitySnapshot, persona: PersonaParticipationProfile, recent_outputs: Sequence[str]) -> ParticipationDecision:
        latest = topic.latest
        if latest is None:
            return ParticipationDecision.silence(
                scene=InteractionScene.AMBIENT_CONTRIBUTION,
                reason_codes=("empty_topic",),
            )
        scene = classify_scene(trigger, latest)
        if trigger is TriggerKind.COPIED_AT:
            return ParticipationDecision.silence(scene=scene, reason_codes=("copied_at_bypassed",))
        if trigger in _DIRECT:
            pressure = self.pressure.observe(latest, trigger, now=now, aliases=aliases)
            act = self._direct_act(latest.text, pressure.level, affinity.band)
            posture = self._posture(affinity.response_posture, pressure.level, affinity.band)
            return ParticipationDecision.speak(
                scene=scene,
                act=act,
                posture=posture,
                obligation=ParticipationObligation.DIRECT_REQUIRED,
                reason_codes=("direct_required",) + pressure.reason_codes,
                contribution=self._direct_contribution(act),
                quote_mode=QuoteMode.NEVER,
                media_policy=MediaPolicy(),
                pressure=pressure,
            )
        return ParticipationDecision.silence(scene=scene, reason_codes=("open_participation_not_implemented",))

    @staticmethod
    def _direct_act(text: str, level: DirectAddressPressureLevel, band: AffinityBand) -> ResponseAct:
        if level is DirectAddressPressureLevel.PESTER and band in (AffinityBand.HOSTILE, AffinityBand.WARY):
            return ResponseAct.BOUNDARY
        if level is DirectAddressPressureLevel.PESTER and band in (AffinityBand.FRIENDLY, AffinityBand.CLOSE):
            return ResponseAct.PLAYFUL_REPLY
        return ResponseAct.ACKNOWLEDGE

    @staticmethod
    def _posture(default: ResponsePosture, level: DirectAddressPressureLevel, band: AffinityBand) -> ResponsePosture:
        if level is DirectAddressPressureLevel.PESTER and band is AffinityBand.HOSTILE:
            return ResponsePosture.FIRM
        if level is DirectAddressPressureLevel.PESTER and band is AffinityBand.WARY:
            return ResponsePosture.RESERVED
        return default

    @staticmethod
    def _direct_contribution(act: ResponseAct) -> str:
        if act is ResponseAct.BOUNDARY:
            return "短句守住边界，不延长空 @"
        if act is ResponseAct.PLAYFUL_REPLY:
            return "用爱弥斯风格轻轻戏谑一下，让对方说正事"
        return "短应声，不主动扩展话题"
```

- [x] **Step 4: Run tests**

Run: `pytest tests/test_participation_decision.py -q`

Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add groupmate/engine/participation.py tests/test_participation_decision.py
git commit -m "feat: add direct participation engine"
```

---

### Task 5: Open Participation Gate（开放参与门）

**Files:**
- Modify: `groupmate/engine/participation.py`
- Modify: `tests/test_participation_decision.py`

- [x] **Step 1: Add failing open-participation tests**

```python
# append to tests/test_participation_decision.py
def test_open_group_question_with_concrete_help_speaks():
    decision = decide("这个插件怎么重载？", TriggerKind.CANDIDATE)

    assert decision.action is ParticipationAction.SPEAK
    assert decision.obligation is ParticipationObligation.OPEN_OPTIONAL
    assert decision.act is ResponseAct.ANSWER
    assert "motive:help_when_concrete" in decision.reason_codes


def test_empty_echo_candidate_silences():
    decision = decide("哈哈哈", TriggerKind.CANDIDATE)

    assert decision.action is ParticipationAction.SILENCE
    assert "inhibit:empty_echo" in decision.reason_codes
```

- [x] **Step 2: Run tests to verify fail**

Run: `pytest tests/test_participation_decision.py::test_open_group_question_with_concrete_help_speaks tests/test_participation_decision.py::test_empty_echo_candidate_silences -q`

Expected: FAIL because open participation still returns `open_participation_not_implemented`.

- [x] **Step 3: Implement minimal open gate**

```python
# groupmate/engine/participation.py additions
import re

_QUESTION = re.compile(r"[？?]|怎么|什么|谁|哪|为什么|如何")
_EMPTY_ECHO = re.compile(r"^(哈+|哈哈+|确实|好耶|草|笑死)$")

# replace final return in decide()
if _EMPTY_ECHO.search((latest.text or "").strip()):
    return ParticipationDecision.silence(scene=scene, reason_codes=("inhibit:empty_echo",))
if trigger is TriggerKind.CANDIDATE and _QUESTION.search(latest.text or ""):
    return ParticipationDecision.speak(
        scene=scene,
        act=ResponseAct.ANSWER,
        posture=affinity.response_posture,
        obligation=ParticipationObligation.OPEN_OPTIONAL,
        reason_codes=("motive:help_when_concrete",),
        contribution="若能给具体短答就回答，否则沉默",
        quote_mode=QuoteMode.NEVER,
        media_policy=MediaPolicy(),
    )
return ParticipationDecision.silence(scene=scene, reason_codes=("no_open_motive",))
```

- [x] **Step 4: Run tests**

Run: `pytest tests/test_participation_decision.py -q`

Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add groupmate/engine/participation.py tests/test_participation_decision.py
git commit -m "feat: add open participation gate"
```

---

### Task 6: Workflow Integration（工作流接入）

**Files:**
- Modify: `groupmate/engine/workflow.py`
- Create: `tests/test_workflow_participation.py`
- Modify: `tests/test_opportunity.py` or delete tests that assert old online behavior

- [x] **Step 1: Write failing workflow tests**

```python
# tests/test_workflow_participation.py
import asyncio

from groupmate.engine.rate_limit import SlidingWindowRateLimiter
from groupmate.engine.workflow import CognitiveWorkflow
from groupmate.models import ChatMessage, GroupPolicy, TopicSnapshot, TriggerKind
from groupmate.persona.aemeath import AemeathOutputFirewall, AemeathPersonaProvider
from tests.fakes import FakeClock, FakeMemoryRepository, FakePlatform, NullVision, StaticGenerationModel


def message(text, ts=100):
    return ChatMessage(
        message_id=str(ts), group_id="g1", sender_id="u1", sender_name="Alice", text=text, timestamp=ts
    )


def workflow(generator=None, platform=None):
    return CognitiveWorkflow(
        generation_model=generator or StaticGenerationModel("在呢。"),
        vision=NullVision(),
        platform=platform or FakePlatform(),
        memory=FakeMemoryRepository(),
        persona=AemeathPersonaProvider(),
        output_guard=AemeathOutputFirewall(max_chars=60),
        rate_limiter=SlidingWindowRateLimiter(hourly_limit=6, cooldown_seconds=0),
        clock=FakeClock(200),
    )


def test_workflow_direct_uses_participation_engine_without_opportunity_arbiter():
    wf = workflow()
    topic = TopicSnapshot("t1", "g1", (message("爱弥斯"),), 100, 100)

    outcome = asyncio.run(wf.evaluate(topic, TriggerKind.ALIAS_DIRECT, GroupPolicy(humanize_delay_enabled=False)))

    assert outcome.sent is True
    assert outcome.reason == "sent"


def test_workflow_copied_at_does_not_call_model_or_open_continuation():
    model = StaticGenerationModel("不该生成")
    platform = FakePlatform()
    wf = workflow(generator=model, platform=platform)
    topic = TopicSnapshot("t1", "g1", (message("@爱弥斯"),), 100, 100)

    outcome = asyncio.run(wf.evaluate(topic, TriggerKind.COPIED_AT, GroupPolicy(humanize_delay_enabled=False), trigger_alias="爱弥斯"))

    assert outcome.sent is True
    assert outcome.reason == "copied_at_tip"
    assert platform.sent[0]["text"] == "复制出来的 @ 不算数哦，要叫爱弥斯的话，用真正的 @。"
    assert model.calls == 0
```

- [x] **Step 2: Run tests to verify fail**

Run: `pytest tests/test_workflow_participation.py -q`

Expected: FAIL until workflow constructs and uses the new engine.

- [x] **Step 3: Inject participation engine into workflow**

```python
# groupmate/engine/workflow.py imports
from ..social.affinity import snapshot_for_relationship
from .direct_pressure import DirectAddressPressureTracker
from .participation import ParticipationDecisionEngine
from .participation_types import ParticipationAction, ParticipationObligation

# __init__ signature additions
participation_engine: Optional[ParticipationDecisionEngine] = None,
direct_pressure: Optional[DirectAddressPressureTracker] = None,

# __init__ body
self.direct_pressure = direct_pressure or DirectAddressPressureTracker()
self.participation_engine = participation_engine or ParticipationDecisionEngine(
    pressure=self.direct_pressure
)
```

- [x] **Step 4: Replace online OpportunityArbiter path**

Implementation outline in `CognitiveWorkflow.evaluate` after targeting resolution:

```python
relationship_state = self._relationship_state_for_target(topic, targeting)
affinity = snapshot_for_relationship(relationship_state)
participation = self.participation_engine.decide(
    topic=topic,
    trigger=trigger,
    policy=policy,
    targeting=targeting,
    now=now,
    aliases=policy.aliases,
    affinity=affinity,
    persona=getattr(self.persona, "participation_profile"),
    recent_outputs=tuple(self._recent_outputs[topic.group_id]),
)
self._record(decision_id, topic.group_id, "PARTICIPATION", ",".join(participation.reason_codes), now)
if participation.action is ParticipationAction.SILENCE:
    return self._silent(decision_id, topic.group_id, participation.reason_codes[-1], now)

reply_mode = ReplyMode.SHORT_SOCIAL
response_act = ResponseActPlan(
    participation.act,
    participation.scene,
    participation.reason_codes,
)
contribution = participation.contribution
```

Remove online calls to `self.opportunity_arbiter.evaluate(...)` and `self.intent_planner.plan(...)` from the main path. Leave helper attributes only if tests still need them, then delete after tests are migrated.

- [x] **Step 5: Run focused workflow tests**

Run: `pytest tests/test_workflow_participation.py tests/test_workflow.py::test_copied_at_sends_tip_without_llm -q`

Expected: PASS.

- [x] **Step 6: Commit**

```bash
git add groupmate/engine/workflow.py tests/test_workflow_participation.py tests/test_workflow.py
git commit -m "feat: route workflow through participation engine"
```

---

### Task 7: Remove Old Opportunity Configuration（移除旧机会配置）

**Files:**
- Modify: `groupmate/models.py`
- Modify: `groupmate/config.py`
- Modify: `groupmate/host/bridge.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_opportunity.py`

- [x] **Step 1: Write failing config assertions**

```python
# tests/test_config.py
def test_settings_no_longer_expose_v3_opportunity_enabled():
    settings = PluginSettings.from_mapping({"v3_opportunity_enabled": False})

    assert not hasattr(settings, "v3_opportunity_enabled")


def test_group_policy_has_direct_pressure_defaults():
    policy = GroupPolicy()

    assert policy.direct_pressure_window_seconds == 600
    assert policy.direct_pressure_nudge_count == 2
    assert policy.direct_pressure_pester_count == 3
```

- [x] **Step 2: Run tests to verify fail**

Run: `pytest tests/test_config.py -q`

Expected: FAIL while old setting remains.

- [x] **Step 3: Remove old setting and migrate tests**

Remove these fields and parser branches:

```python
# groupmate/models.py
v3_opportunity_enabled: bool = True

# groupmate/config.py PluginSettings
v3_opportunity_enabled: bool = True

# groupmate/config.py from_mapping
v3_opportunity_enabled=_boolean(
    data.get("v3_opportunity_enabled", True), True
),

# groupmate/host/bridge.py
v3_opportunity_enabled=bool(self._setting("v3_opportunity_enabled", True)),
```

Move remaining `tests/test_opportunity.py` cases that still validate useful low-level old behavior into either deleted tests or `tests/test_participation_decision.py`. Do not keep tests that require `OpportunityArbiter` as an online workflow dependency.

- [x] **Step 4: Run tests**

Run: `pytest tests/test_config.py tests/test_participation_decision.py tests/test_workflow_participation.py -q`

Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add groupmate/models.py groupmate/config.py groupmate/host/bridge.py tests/test_config.py tests/test_opportunity.py tests/test_participation_decision.py
git commit -m "refactor: remove legacy opportunity switch"
```

---

### Task 8: Shadow Projector And Verification（影子投影与验收）

**Files:**
- Modify: `eval/shadow_projector.py`
- Modify: `tests/test_phase2_projections.py` or add focused shadow tests if present
- Regenerate local ignored reports under `eval/results/` if export variables are available

- [x] **Step 1: Add failing projector test for copied @ bypass**

```python
# tests/test_shadow_projector.py if not present, otherwise append to existing shadow tests
def test_shadow_projector_treats_copied_at_as_bypass():
    # Build a BehaviorExample whose latest normalized text starts with copied @ alias.
    # Expected projection owner is observe_only or copied_at_tip, not groupmate direct participation.
    assert projection.trigger == "copied_at"
    assert projection.would_reply is False
    assert "copied_at_bypassed" in projection.reason_codes
```

- [x] **Step 2: Run projector test to verify fail**

Run: `pytest tests/test_shadow_projector.py -q`

Expected: FAIL until projector uses copied @ bypass.

- [x] **Step 3: Update projector**

```python
# eval/shadow_projector.py imports
from groupmate.engine.copied_at import is_copied_at
from groupmate.engine.direct_pressure import DirectAddressPressureTracker
from groupmate.engine.participation import ParticipationDecisionEngine
from groupmate.engine.participation_types import ParticipationAction
from groupmate.persona.aemeath import AEMEATH_PARTICIPATION_PROFILE
from groupmate.social.affinity import snapshot_for_relationship

# __init__
self.participation = ParticipationDecisionEngine(
    pressure=DirectAddressPressureTracker()
)

# project() after trigger classification
if is_copied_at(trigger.kind):
    return ShadowProjection(
        sample_id=example.sample_id,
        owner="copied_at_guard",
        would_reply=False,
        trigger=trigger.kind.value,
        scene=InteractionScene.AMBIENT_CONTRIBUTION,
        act=None,
        quote_allowed=False,
        decorative_media_allowed=False,
        capability_media_allowed=False,
        ambiguous_target=False,
        owner_count=1,
        completion_claim_allowed=False,
        reason_codes=("copied_at_bypassed",),
    )
```

- [x] **Step 4: Run full focused suite**

Run: `pytest tests/test_copied_at_guard.py tests/test_direct_pressure.py tests/test_participation_decision.py tests/test_workflow_participation.py tests/test_config.py -q`

Expected: PASS.

- [x] **Step 5: Run full test suite**

Run: `pytest -q`

Expected: PASS. If failures mention removed old opportunity behavior, update tests to assert the new participation engine contract instead of the old utility score.

- [x] **Step 6: Regenerate shadow report when local export env is available**

Run:

```bash
python3.7 -m eval.shadow_export \
  --export-dir "$SHADOW_EXPORT_DIR" \
  --target-uin "$SHADOW_TARGET_UIN" \
  --target-alias 小维 \
  --current-alias 爱弥斯 \
  --id-salt-file eval/results/.shadow-id-salt \
  --output eval/results/phase3-shadow.json \
  --review-output eval/results/phase3-review.jsonl
```

Expected: `target_silence_projected_reply` decreases materially, copied-text @ does not appear as direct participation, and violation counts for boundary media and false completion stay 0.

- [x] **Step 7: Commit**

```bash
git add eval/shadow_projector.py tests/test_shadow_projector.py
git commit -m "feat: project unified participation decisions"
```

---

## Self-Review

- Spec coverage: copied-text @ bypass, Aemeath-styled fixed tip, old opportunity removal, direct pressure, affinity posture, open participation, workflow integration, and shadow evaluation each have tasks.
- Placeholder scan: no unresolved placeholder markers or cross-task shorthand steps are present.
- Type consistency: `ParticipationDecisionEngine`（统一参与决策引擎）, `CopiedAtGuard`（复制文本 @ 旁路）, `DirectAddressPressureTracker`（直接呼叫压力跟踪器）, and `ParticipationDecision`（参与决策） names are consistent with the approved spec.
- Subagent note: current session instructions require explicit user choice before spawning subagents; do not spawn any agent unless the user chooses a subagent execution mode.
