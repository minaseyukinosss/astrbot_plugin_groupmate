# Minimal Configuration And Policy Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the legacy AstrBot configuration and mixed `GroupPolicy` with six deployment settings, persona-scoped configuration, focused internal policies, and one production runtime path.

**Architecture:** Parse only host-owned values into immutable `DeploymentSettings`（部署设置）, resolve the fixed `aemeath` persona through `PersonaRegistry`（人格注册表）, and inject a code-owned `BehaviorPolicy`（内部行为策略集合） into runtime components. Remove every legacy switch, hard-coded relationship, group brief, and local reaction-catalog path instead of retaining compatibility branches.

**Tech Stack:** Python 3 dataclasses, AstrBot `_conf_schema.json`, asyncio runtime, pytest, SQLite-backed integration fakes, vanilla JavaScript plugin page.

**Design Spec:** `docs/superpowers/specs/2026-07-29-configuration-persona-scope-design.md`

**Execution Status:** Completed on 2026-07-30.

**Delivered By:** `238bdde`（严格六项配置）, `16c7968`（显式 Aemeath PersonaRegistry）, and `181bf14`（策略、运行时、评测、文档与残留清理）.

**Completion Evidence:** `pytest` 601 passed; deterministic baseline 120/120 passed; Phase 2 behavior evaluation 10/10 passed; production residual scans and `git diff --check` passed.

**Execution Order:** Complete this plan before `docs/superpowers/plans/2026-07-29-persona-scoped-state-v11.md`.

---

## File Map（文件职责）

- Create `groupmate/host/config.py`: strict AstrBot configuration parser and immutable deployment settings.
- Create `groupmate/persona/registry.py`: persona definitions, registry, and resolved persona context.
- Create `groupmate/policies.py`: internal participation, conversation, reply, and resource policies.
- Modify `main.py`: parse AstrBot configuration once and pass typed settings to the bridge.
- Modify `_conf_schema.json`: expose exactly six settings.
- Modify `groupmate/host/bridge.py`: resolve persona, providers, capabilities, policies, and status without dynamic `_setting` reads.
- Modify `groupmate/engine/{triggers,participation,runtime,workflow}.py`: consume focused policies and remove rollback branches.
- Modify `groupmate/core/{intent,projections,context_assembly}.py`: remove global output cap, mixed policy, group brief, and reaction history.
- Modify `groupmate/persona/aemeath/{provider,__init__}.py`: remove hard-coded relationships and group brief.
- Modify `groupmate/engine/composer.py`: retain capability media but remove decorative local reaction input.
- Delete `groupmate/config.py`, `groupmate/persona/aemeath/relationships.py`, `groupmate/media/reactions.py`, `groupmate/media/__init__.py`, and `tests/test_reaction_media.py` after callers move.
- Modify `groupmate/host/web_api.py`, `pages/settings/{index.html,app.js}`, and `README.md`: show only the new contract and runtime health.
- Modify `eval/{schema,runner,shadow_projector,shadow_export}.py`: construct persona context and behavior policies instead of `GroupPolicy`.
- Modify `eval/{build_corpus.py,scenarios/baseline.jsonl,scenarios/phase2_behavior.jsonl}`: remove deleted plugin-policy keys while preserving expected-output constraints.
- Rewrite or update configuration, runtime, workflow, projection, provider, Web UI, and evaluation tests.

### Task 1: Strict `DeploymentSettings`（部署设置）

**Files:**
- Create: `groupmate/host/config.py`
- Modify: `groupmate/host/__init__.py`
- Modify: `main.py`
- Rewrite: `tests/test_config.py`
- Modify: `tests/test_plugin_loading.py`

- [x] **Step 1: Replace legacy configuration tests with the new contract**

Write tests that assert the exact dataclass fields, per-persona lookup, explicit empty aliases, strict QQ validation, duplicate relationship rejection, and diagnostics:

```python
from dataclasses import fields

import pytest

from groupmate.host.config import (
    AstrBotConfigParser,
    ConfigurationError,
    DeploymentSettings,
)


def test_deployment_settings_contain_only_six_public_values():
    settings = AstrBotConfigParser().parse({})
    assert {item.name for item in fields(DeploymentSettings)} == {
        "enabled_groups",
        "persona_aliases",
        "relationships",
        "generation_provider",
        "vision_enabled",
        "vision_provider",
        "diagnostics",
    }
    assert settings.enabled_groups == ()
    assert settings.aliases_for("aemeath") == ("爱弥斯", "小爱", "飞行雪绒")
    assert settings.relationships_for("aemeath") == ()


def test_explicit_empty_aliases_are_not_replaced_by_defaults():
    settings = AstrBotConfigParser().parse(
        {"persona_group": {"persona_aliases": {"aemeath": []}, "relationships": {"aemeath": []}}}
    )
    assert settings.aliases_for("aemeath") == ()
    assert "empty_aliases:aemeath" in settings.diagnostics.warnings


def test_duplicate_relationship_ids_are_rejected():
    raw = {
        "persona_group": {
            "relationships": {
                "aemeath": [
                    {"id": "123", "relationship": "普通群友", "address": ""},
                    {"id": "123", "relationship": "闺蜜", "address": "小明"},
                ]
            }
        }
    }
    with pytest.raises(ConfigurationError, match="persona_group.relationships.aemeath.*123"):
        AstrBotConfigParser().parse(raw)


def test_legacy_keys_are_diagnosed_and_never_applied():
    settings = AstrBotConfigParser().parse(
        {"group_brief": "旧值", "max_reply_chars": 999, "enabled_groups": ["100"]}
    )
    assert settings.enabled_groups == ()
    assert settings.diagnostics.ignored_legacy_keys == (
        "group_brief",
        "max_reply_chars",
    )
```

- [x] **Step 2: Run the focused tests and verify RED**

Run: `./.venv/bin/python -m pytest tests/test_config.py -q`

Expected: collection fails because `groupmate.host.config` does not exist.

- [x] **Step 3: Implement the strict parser and immutable settings**

Use tuple-backed persona mappings so the frozen object cannot expose mutable dictionaries:

```python
@dataclass(frozen=True)
class ConfigDiagnostics:
    ignored_legacy_keys: Tuple[str, ...] = ()
    unknown_keys: Tuple[str, ...] = ()
    warnings: Tuple[str, ...] = ()


@dataclass(frozen=True)
class DeploymentSettings:
    enabled_groups: Tuple[str, ...]
    persona_aliases: Tuple[Tuple[str, Tuple[str, ...]], ...]
    relationships: Tuple[Tuple[str, Tuple[RelationshipEntry, ...]], ...]
    generation_provider: str
    vision_enabled: bool
    vision_provider: str
    diagnostics: ConfigDiagnostics

    def aliases_for(self, persona_id: str) -> Tuple[str, ...]:
        return dict(self.persona_aliases).get(str(persona_id), ())

    def relationships_for(self, persona_id: str) -> Tuple[RelationshipEntry, ...]:
        return dict(self.relationships).get(str(persona_id), ())


class ConfigurationError(ValueError):
    pass
```

`AstrBotConfigParser.parse`（解析 AstrBot 配置） must read only `scope_group.enabled_groups`, `persona_group.persona_aliases`, `persona_group.relationships`, and the three `provider_group` values. Validate group and QQ IDs with `str.isdigit()`, preserve explicit empty alias lists, accept relationship labels only from `普通群友 / 闺蜜 / 最亲近`, and sort diagnostic key names for stable output. Top-level legacy values, including still-valid names such as flat `enabled_groups`, are diagnostics only and never fallback inputs.

Change `main.py` to:

```python
from .groupmate.host.config import AstrBotConfigParser

self.config = AstrBotConfigParser().parse(config)
self.bridge = AstrBotBridge(context, self.config, data_dir)
```

- [x] **Step 4: Run the focused tests and verify GREEN**

Run: `./.venv/bin/python -m pytest tests/test_config.py tests/test_plugin_loading.py -q`

Expected: all selected tests pass.

- [x] **Step 5: Commit**

```bash
git add groupmate/host/config.py groupmate/host/__init__.py main.py tests/test_config.py tests/test_plugin_loading.py
git commit -m "refactor: add strict deployment settings"
```

### Task 2: Exact Six-Item AstrBot Schema（六项配置界面）

**Files:**
- Modify: `_conf_schema.json`
- Test: `tests/test_config.py`

- [x] **Step 1: Add a schema-shape regression test**

```python
def test_schema_exposes_exactly_six_settings():
    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    items = {
        name
        for group in schema.values()
        for name in group.get("items", {})
    }
    assert items == {
        "enabled_groups",
        "persona_aliases",
        "relationships",
        "generation_provider",
        "vision_enabled",
        "vision_provider",
    }
    assert schema["scope_group"]["items"]["enabled_groups"]["default"] == []
```

- [x] **Step 2: Verify RED**

Run: `./.venv/bin/python -m pytest tests/test_config.py::test_schema_exposes_exactly_six_settings -q`

Expected: FAIL because the old schema exposes additional items.

- [x] **Step 3: Replace the schema groups**

Keep only `scope_group`（启用范围）, `persona_group`（人格部署配置）, and `provider_group`（模型配置）. Represent `persona_aliases` and `relationships` as nested `aemeath` objects in the UI while preserving the logical parser shape:

```json
{
  "scope_group": {"type": "object", "items": {"enabled_groups": {"type": "list", "default": []}}},
  "persona_group": {
    "type": "object",
    "items": {
      "persona_aliases": {"type": "object", "items": {"aemeath": {"type": "list", "default": ["爱弥斯", "小爱", "飞行雪绒"]}}},
      "relationships": {"type": "object", "items": {"aemeath": {"type": "template_list", "default": [], "templates": {"member": {"name": "群成员", "display_item": "id", "items": {"id": {"type": "string", "default": ""}, "relationship": {"type": "string", "default": "普通群友", "options": ["普通群友", "闺蜜", "最亲近"]}, "address": {"type": "string", "default": ""}}}}}}}
    }
  },
  "provider_group": {"type": "object", "items": {"generation_provider": {"type": "string", "_special": "select_provider", "default": ""}, "vision_enabled": {"type": "bool", "default": true}, "vision_provider": {"type": "string", "_special": "select_provider", "default": ""}}}
}
```

Preserve clear Chinese `description` and `hint` strings when expanding this compact structure in the actual file.

- [x] **Step 4: Verify GREEN and JSON validity**

Run: `./.venv/bin/python -m pytest tests/test_config.py -q`

Expected: all configuration tests pass and JSON loading succeeds.

- [x] **Step 5: Commit**

```bash
git add _conf_schema.json tests/test_config.py
git commit -m "refactor: reduce AstrBot config to six settings"
```

### Task 3: `PersonaRegistry` And Clean Aemeath Provider（人格注册表与干净人格入口）

**Files:**
- Create: `groupmate/persona/registry.py`
- Modify: `groupmate/persona/__init__.py`
- Modify: `groupmate/persona/aemeath/provider.py`
- Modify: `groupmate/persona/aemeath/__init__.py`
- Modify: `groupmate/core/context_assembly.py`
- Delete: `groupmate/persona/aemeath/relationships.py`
- Test: `tests/test_persona.py`
- Test: `tests/test_core_assembly.py`

- [x] **Step 1: Write persona resolution and prompt-cleanliness tests**

```python
def test_registry_resolves_aemeath_with_configured_aliases_and_no_default_relationships():
    context = default_persona_registry().resolve(
        "aemeath",
        aliases=("爱弥斯", "小爱"),
        relationships=(),
    )
    assert context.persona_id == "aemeath"
    assert context.display_name == "爱弥斯"
    assert context.aliases == ("爱弥斯", "小爱")
    assert context.relationship_seeds == ()


def test_aemeath_system_prompt_has_no_group_brief_slot():
    parameters = set(signature(AemeathPersonaProvider).parameters)
    assert "group_brief" not in parameters
    system = AemeathPersonaProvider(relationships=()).system_text()
    assert "当前群氛围" not in system
```

- [x] **Step 2: Verify RED**

Run: `./.venv/bin/python -m pytest tests/test_persona.py tests/test_core_assembly.py -q`

Expected: FAIL because the registry is missing and group brief/default relationships remain.

- [x] **Step 3: Implement the registry and remove hidden defaults**

Create these immutable types:

```python
@dataclass(frozen=True)
class PersonaDefinition:
    persona_id: str
    display_name: str
    default_aliases: Tuple[str, ...]
    participation_profile: PersonaParticipationProfile
    provider_factory: Callable[[Sequence[RelationshipEntry]], object]


@dataclass(frozen=True)
class PersonaContext:
    definition: PersonaDefinition
    aliases: Tuple[str, ...]
    relationship_seeds: Tuple[RelationshipEntry, ...]
    prompt_provider: object

    @property
    def persona_id(self) -> str:
        return self.definition.persona_id

    @property
    def display_name(self) -> str:
        return self.definition.display_name
```

Register only `aemeath`. `resolve`（解析人格） must reject unknown IDs and must use the supplied aliases exactly, including an empty tuple. Make `AemeathPersonaProvider(relationships=())` default to an empty tuple, remove `group_brief`, remove `set_group_brief`, remove `DEFAULT_RELATIONSHIPS`, and remove the group-brief branch from `ContextAssembly.build_system`（构建稳定提示词）.

- [x] **Step 4: Verify GREEN**

Run: `./.venv/bin/python -m pytest tests/test_persona.py tests/test_core_assembly.py tests/test_behavior_profile.py -q`

Expected: all selected tests pass.

- [x] **Step 5: Commit**

```bash
git add groupmate/persona groupmate/core/context_assembly.py tests/test_persona.py tests/test_core_assembly.py
git commit -m "refactor: resolve Aemeath through persona registry"
```

### Task 4: Focused Internal Policies（拆分内部策略）

**Files:**
- Create: `groupmate/policies.py`
- Modify: `groupmate/models.py`
- Modify: `groupmate/engine/triggers.py`
- Modify: `groupmate/engine/participation.py`
- Modify: `groupmate/engine/runtime.py`
- Modify: `groupmate/engine/workflow.py`
- Modify: `groupmate/core/projections.py`
- Modify: `groupmate/core/intent.py`
- Modify: `groupmate/persona/aemeath/output_firewall.py`
- Modify: `tests/conftest.py`
- Modify: `tests/test_triggers.py`
- Modify: `tests/test_participation_decision.py`
- Modify: `tests/test_runtime.py`
- Modify: `tests/test_phase2_projections.py`
- Modify: `tests/test_workflow.py`
- Modify: `tests/test_reply_mode.py`
- Modify: `tests/test_guardrails.py`

- [x] **Step 1: Add policy-contract tests**

```python
def test_behavior_policy_contains_only_focused_internal_policies():
    behavior = BehaviorPolicy()
    assert behavior.participation.direct_pressure_window_seconds == 600
    assert behavior.conversation.continuation_seconds == 90
    assert behavior.reply.max_reply_segments == 2
    assert behavior.resources.open_send_hourly_limit > 0
    assert not hasattr(behavior, "aliases")
    assert not hasattr(behavior, "vision_enabled")
    assert not hasattr(behavior, "v3_scheduler_enabled")


def test_reply_length_comes_from_reply_mode_not_global_policy():
    assert "policy_max" not in signature(max_chars_for_mode).parameters
    assert max_chars_for_mode(ReplyMode.SHORT_SOCIAL) == 60
    assert max_chars_for_mode(ReplyMode.HELP_DETAIL) == 180
```

Change trigger tests to construct `TriggerRouter(aliases=("爱弥斯", "小爱"))`, and participation tests to pass `policy=BehaviorPolicy().participation` rather than `GroupPolicy`.

- [x] **Step 2: Verify RED**

Run: `./.venv/bin/python -m pytest tests/test_triggers.py tests/test_participation_decision.py tests/test_runtime.py tests/test_phase2_projections.py tests/test_reply_mode.py tests/test_guardrails.py -q`

Expected: FAIL because focused policies and new signatures do not exist.

- [x] **Step 3: Add the policy types**

```python
@dataclass(frozen=True)
class ParticipationPolicy:
    direct_pressure_window_seconds: int = 600
    direct_pressure_nudge_count: int = 2
    direct_pressure_pester_count: int = 3


@dataclass(frozen=True)
class ConversationPolicy:
    history_limit: int = 100
    debounce_min_seconds: float = 4.0
    debounce_max_seconds: float = 8.0
    topic_max_seconds: int = 12
    candidate_ttl_seconds: int = 20
    continuation_seconds: int = 90


@dataclass(frozen=True)
class ReplyPolicy:
    humanize_delay_enabled: bool = True
    max_reply_segments: int = 2


@dataclass(frozen=True)
class ResourcePolicy:
    open_send_hourly_limit: int = 6
    open_send_cooldown_seconds: int = 600
    generation_hourly_limit: int = 30
    vision_hourly_limit: int = 12


@dataclass(frozen=True)
class BehaviorPolicy:
    participation: ParticipationPolicy = field(default_factory=ParticipationPolicy)
    conversation: ConversationPolicy = field(default_factory=ConversationPolicy)
    reply: ReplyPolicy = field(default_factory=ReplyPolicy)
    resources: ResourcePolicy = field(default_factory=ResourcePolicy)
```

Remove `GroupPolicy` from `groupmate/models.py`. Pass `PersonaContext.aliases` independently to `TriggerRouter`, addressee resolution, response-act planning, and direct-pressure observation. Pass only the relevant focused policy into each component. `CognitiveWorkflow` should receive `behavior`, `persona_context`, and `vision_enabled` at construction instead of reading mixed fields during `evaluate`.

Change `max_chars_for_mode(mode)`（按回复模式取字数上限） to accept only the reply mode and return `ModeConstraints.max_chars`（模式长度约束）. Construct `AemeathOutputFirewall()`（爱弥斯输出防火墙） without a configured global maximum; its `validate` method continues to apply the selected reply-mode constraints. Delivery uses `behavior.reply.max_reply_segments`, `behavior.reply.humanize_delay_enabled`, and `behavior.conversation.candidate_ttl_seconds`.

- [x] **Step 4: Migrate runtime and projection signatures**

The stable signatures after this task are:

```python
GroupActor(group_id, workflow, persona_context, behavior)
GroupRuntimeManager(workflow_factory, persona_factory, behavior_factory)
TriggerRouter(aliases: Sequence[str])
ParticipationDecisionEngine.decide(topic=topic, trigger=trigger, policy=behavior.participation, targeting=targeting, now=now, aliases=persona_context.aliases, affinity=affinity, persona=persona_context.definition.participation_profile, recent_outputs=recent_outputs, task_resolution=task_resolution)
StateProjector.rebuild(group_id, now=now, policy=behavior.conversation)
CognitiveWorkflow.evaluate(topic, trigger, behavior, trigger_alias="", still_valid=None)
```

Update all selected fixtures to use one `BehaviorPolicy()` and one resolved Aemeath `PersonaContext`; do not introduce a compatibility `GroupPolicy` alias.

- [x] **Step 5: Verify GREEN**

Run: `./.venv/bin/python -m pytest tests/test_triggers.py tests/test_participation_decision.py tests/test_runtime.py tests/test_phase2_projections.py tests/test_workflow.py tests/test_reply_mode.py tests/test_guardrails.py -q`

Expected: all selected tests pass.

- [x] **Step 6: Commit**

```bash
git add groupmate/policies.py groupmate/models.py groupmate/engine/triggers.py groupmate/engine/participation.py groupmate/engine/runtime.py groupmate/engine/workflow.py groupmate/core/projections.py groupmate/core/intent.py groupmate/persona/aemeath/output_firewall.py tests/conftest.py tests/test_triggers.py tests/test_participation_decision.py tests/test_runtime.py tests/test_phase2_projections.py tests/test_workflow.py tests/test_reply_mode.py tests/test_guardrails.py
git commit -m "refactor: replace mixed group policy"
```

### Task 5: One Scheduler, Composition, And Memory Path（删除阶段回退路线）

**Files:**
- Modify: `groupmate/engine/runtime.py`
- Modify: `groupmate/engine/workflow.py`
- Modify: `groupmate/host/bridge.py`
- Modify: `tests/test_phase1_runtime.py`
- Modify: `tests/test_memory_writer.py`
- Modify: `tests/test_workflow.py`

- [x] **Step 1: Add absence and behavior tests**

```python
def test_runtime_has_no_legacy_scheduler_switch():
    assert "v3_scheduler_enabled" not in signature(GroupActor).parameters
    assert "v3_scheduler_enabled" not in signature(GroupRuntimeManager).parameters
    assert "legacy" not in inspect.getsource(GroupActor._evaluate_immediate)


async def test_memory_writer_is_always_scheduled_after_confirmed_send(
    workflow, direct_topic, behavior, memory_writer
):
    outcome = await workflow.evaluate(direct_topic, TriggerKind.ALIAS_DIRECT, behavior)
    assert outcome.sent is True
    assert memory_writer.calls == 1
    assert "enabled" not in signature(memory_writer.schedule_after_send).parameters
```

- [x] **Step 2: Verify RED**

Run: `./.venv/bin/python -m pytest tests/test_phase1_runtime.py tests/test_memory_writer.py tests/test_workflow.py -q`

Expected: FAIL because switchable branches remain.

- [x] **Step 3: Delete rollback parameters and branches**

Remove `_v3_scheduler_enabled`, serial evaluation branches, scheduler status labels, `composition_enabled`, `v3_memory_writer_enabled`, `v3_composition_enabled`, compatibility assembly calls, and the conditional task resolver. Always launch the current non-blocking scheduler, always build `ResponseActPlan`, always execute the current capability/composer pipeline, and call:

```python
self.memory_writer.schedule_after_send(
    topic,
    targeting,
    decision_id=decision_id,
    now=send_now,
    reply_text=outcome.text or "",
)
```

Remove the `enabled` parameter from `MemoryWriter.schedule_after_send`（发送后安排记忆写入） and its callers. Keep exception isolation so memory writing never turns a confirmed reply into failure.

- [x] **Step 4: Verify GREEN**

Run: `./.venv/bin/python -m pytest tests/test_phase1_runtime.py tests/test_memory_writer.py tests/test_workflow.py -q`

Expected: all selected tests pass.

- [x] **Step 5: Commit**

```bash
git add groupmate/engine/runtime.py groupmate/engine/workflow.py groupmate/host/bridge.py tests/test_phase1_runtime.py tests/test_memory_writer.py tests/test_workflow.py
git commit -m "refactor: remove phased runtime fallbacks"
```

### Task 6: Remove The Old Local Reaction System（移除旧本地反应素材系统）

**Files:**
- Modify: `groupmate/engine/composer.py`
- Modify: `groupmate/engine/workflow.py`
- Modify: `groupmate/core/projections.py`
- Modify: `groupmate/host/bridge.py`
- Delete: `groupmate/media/reactions.py`
- Delete: `groupmate/media/__init__.py`
- Delete: `tests/test_reaction_media.py`
- Modify: `tests/test_composer.py`
- Modify: `tests/test_workflow.py`
- Modify: `tests/test_phase2_projections.py`

- [x] **Step 1: Replace reaction tests with a capability-media boundary test**

```python
def test_composer_accepts_safe_capability_media_but_has_no_reaction_argument(tmp_path):
    parameters = set(signature(ResponseComposer.compose).parameters)
    assert "reaction" not in parameters
    result = CapabilityResult.success(
        facts=("图片结果",),
        media_candidates=(approved_capability_image(tmp_path),),
    )
    draft = ResponseComposer().compose(
        text="找到了。",
        act_plan=answer_plan(),
        quote_message_id=None,
        capability_result=result,
    )
    assert [segment.kind for segment in draft.segments] == [OutboundKind.TEXT, OutboundKind.IMAGE]
```

- [x] **Step 2: Verify RED**

Run: `./.venv/bin/python -m pytest tests/test_composer.py tests/test_workflow.py tests/test_phase2_projections.py -q`

Expected: FAIL because the composer and workflow still expose local reactions.

- [x] **Step 3: Remove only decorative reaction code**

Delete `reaction` from `ResponseComposer.compose`, `_safe_reaction`, `_DECORATIVE_ACTS`, workflow reaction catalog/policy fields, `_select_reaction`, recent reaction-media hydration, and bridge directory loading. Preserve `CapabilityResult.media_candidates`, `_safe_capability_media`, `OutboundSegment(IMAGE)`, delivery metadata, platform image sending, and stable media IDs.

`ResponseAct.VISUAL_REACTION` remains valid for a text reaction to visual content; it must not select a local decorative asset.

- [x] **Step 4: Verify GREEN**

Run: `./.venv/bin/python -m pytest tests/test_composer.py tests/test_workflow.py tests/test_platform_port.py tests/test_capability_contracts.py tests/test_phase2_projections.py -q`

Expected: all selected tests pass.

- [x] **Step 5: Commit**

```bash
git add groupmate/media/reactions.py groupmate/media/__init__.py groupmate/engine/composer.py groupmate/engine/workflow.py groupmate/core/projections.py groupmate/host/bridge.py tests/test_reaction_media.py tests/test_composer.py tests/test_workflow.py tests/test_phase2_projections.py
git commit -m "refactor: remove legacy local reactions"
```

### Task 7: Enforce Open-Send Safety Budget（接入开放场景发送安全门）

**Files:**
- Modify: `groupmate/engine/workflow.py`
- Modify: `groupmate/engine/rate_limit.py`
- Modify: `tests/test_phase4_budgets.py`
- Modify: `tests/test_direct_fallback.py`

- [x] **Step 1: Add open-versus-direct budget tests**

```python
async def test_open_participation_stops_before_generation_when_send_budget_is_exhausted(
    workflow, open_help_topic, behavior, budgets, generation
):
    budgets.record_send(100)
    outcome = await workflow.evaluate(open_help_topic, TriggerKind.CANDIDATE, behavior)
    assert outcome.sent is False
    assert outcome.reason == "open_send_budget_exhausted"
    assert generation.calls == 0


async def test_direct_required_bypasses_open_send_budget(
    workflow, direct_topic, behavior, budgets
):
    budgets.record_send(100)
    outcome = await workflow.evaluate(direct_topic, TriggerKind.ALIAS_DIRECT, behavior)
    assert outcome.sent is True
```

- [x] **Step 2: Verify RED**

Run: `./.venv/bin/python -m pytest tests/test_phase4_budgets.py tests/test_direct_fallback.py -q`

Expected: the open-participation test fails because `allow_send` is not called.

- [x] **Step 3: Add the deterministic safety gate**

Immediately after `ParticipationDecisionEngine.decide` returns `SPEAK`, before memory retrieval, capability execution, or generation:

```python
if (
    participation.obligation is ParticipationObligation.OPEN_OPTIONAL
    and not self.budgets.allow_send(now)
):
    return self._silent(
        decision_id,
        topic.group_id,
        "open_send_budget_exhausted",
        now,
    )
```

Continue recording the send only after confirmed delivery and only for `OPEN_OPTIONAL`; never turn the budget into a probability or an invitation to speak.

- [x] **Step 4: Verify GREEN**

Run: `./.venv/bin/python -m pytest tests/test_phase4_budgets.py tests/test_direct_fallback.py tests/test_workflow.py -q`

Expected: all selected tests pass.

- [x] **Step 5: Commit**

```bash
git add groupmate/engine/workflow.py groupmate/engine/rate_limit.py tests/test_phase4_budgets.py tests/test_direct_fallback.py
git commit -m "fix: enforce open participation send budget"
```

### Task 8: Provider Precedence, Bridge Wiring, And Status（模型优先级、宿主接线与状态）

**Files:**
- Modify: `groupmate/host/bridge.py`
- Modify: `groupmate/host/llm.py`
- Modify: `groupmate/host/web_api.py`
- Modify: `pages/settings/index.html`
- Modify: `pages/settings/app.js`
- Modify: `tests/test_plugin_loading.py`
- Modify: `tests/test_native_wake_suppress.py`
- Modify: `tests/test_plugin_page_assets.py`
- Create: `tests/test_provider_resolution.py`

- [x] **Step 1: Add provider precedence and status tests**

```python
async def test_explicit_generation_provider_wins_over_current_group_provider(tmp_path):
    bridge = make_bridge(tmp_path, generation_provider="fixed-provider")
    bridge.context.get_current_chat_provider_id.return_value = "group-provider"
    await bridge._prepare_actor(event_for("g1"))
    assert bridge._provider_by_group["g1"] == "fixed-provider"


async def test_empty_generation_provider_follows_group_provider(tmp_path):
    bridge = make_bridge(tmp_path, generation_provider="")
    bridge.context.get_current_chat_provider_id.return_value = "group-provider"
    await bridge._prepare_actor(event_for("g1"))
    assert bridge._provider_by_group["g1"] == "group-provider"


def test_status_reports_health_without_removed_values(bridge):
    payload = bridge.status()
    assert payload["active_persona"] == "aemeath"
    assert payload["enabled_scope"] == "all"
    assert "group_brief" not in repr(payload)
    assert "max_reply_chars" not in repr(payload)
```

- [x] **Step 2: Verify RED**

Run: `./.venv/bin/python -m pytest tests/test_provider_resolution.py tests/test_plugin_loading.py tests/test_plugin_page_assets.py -q`

Expected: FAIL because current group Provider overwrites explicit configuration and status exposes old fields.

- [x] **Step 3: Wire typed configuration and persona context**

Remove `_setting` from `AstrBotBridge`. Resolve the configured persona once with:

```python
self.persona_context = default_persona_registry().resolve(
    "aemeath",
    aliases=settings.aliases_for("aemeath"),
    relationships=settings.relationships_for("aemeath"),
)
self.behavior = BehaviorPolicy()
```

In `_prepare_actor`, use configured text Provider without asking AstrBot for a replacement; query the current group Provider only when the configured value is empty. The visual getter returns the explicit visual Provider or the already resolved group text Provider. Construct the vision capability only when `settings.vision_enabled` is true.

Always handle native wake events that belong to Groupmate; preserve existing external-knowledge handoff to the AstrBot Agent.

- [x] **Step 4: Replace status and page fields**

Return `active_persona`, `enabled_scope`, `alias_count`, `relationship_seed_count`, `generation_provider_mode`, `vision_status`, `database_schema`, `config_health`, and `ignored_legacy_keys`. Remove scheduler version, group brief, global max chars, wake switch, and spontaneous-limit display. Update HTML IDs and JavaScript rendering to match.

- [x] **Step 5: Verify GREEN**

Run: `./.venv/bin/python -m pytest tests/test_provider_resolution.py tests/test_plugin_loading.py tests/test_native_wake_suppress.py tests/test_plugin_page_assets.py -q`

Expected: all selected tests pass.

- [x] **Step 6: Commit**

```bash
git add groupmate/host pages/settings tests/test_provider_resolution.py tests/test_plugin_loading.py tests/test_native_wake_suppress.py tests/test_plugin_page_assets.py
git commit -m "refactor: enforce provider and status contract"
```

### Task 9: Evaluation Adapters, Documentation, And Residual Audit（评估适配、文档与残留审计）

**Files:**
- Modify: `eval/schema.py`
- Modify: `eval/runner.py`
- Modify: `eval/shadow_projector.py`
- Modify: `eval/shadow_export.py`
- Modify: `eval/build_corpus.py`
- Modify: `eval/scenarios/baseline.jsonl`
- Modify: `eval/scenarios/phase2_behavior.jsonl`
- Modify: `tests/test_eval_schema.py`
- Modify: `tests/test_eval_runner.py`
- Modify: `tests/test_shadow_projector.py`
- Modify: `README.md`
- Delete: `groupmate/config.py`

- [x] **Step 1: Add a repository residual test**

```python
REMOVED_PRODUCTION_NAMES = (
    "PluginSettings",
    "GroupPolicy",
    "handle_native_wake",
    "group_brief",
    "max_reply_chars",
    "spontaneous_hourly_limit",
    "spontaneous_cooldown_seconds",
    "v3_scheduler_enabled",
    "v3_memory_writer_enabled",
    "v3_composition_enabled",
    "reaction_media_enabled",
    "reaction_catalog_path",
    "LocalReactionCatalog",
    "ReactionPolicy",
    "DEFAULT_RELATIONSHIPS",
    "flatten_plugin_config",
)


def test_removed_configuration_and_fallbacks_are_absent_from_production():
    root = Path(__file__).resolve().parents[1]
    production = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (root / "groupmate").rglob("*.py")
    )
    for name in REMOVED_PRODUCTION_NAMES:
        assert name not in production
```

- [x] **Step 2: Verify RED**

Run: `./.venv/bin/python -m pytest tests/test_config.py::test_removed_configuration_and_fallbacks_are_absent_from_production -q`

Expected: FAIL until all production references are removed.

- [x] **Step 3: Migrate evaluation contracts and evaluation fixtures**

Replace `EvaluationScenario.group_policy` with `behavior_policy` returning `BehaviorPolicy`; keep only scenario-level values that still represent legitimate test inputs. Alias variation belongs in a `PersonaContext` fixture, not in behavior policy. Remove scenario schema keys for deleted configuration fields; preserve `constraints.max_chars` because it is an expected-output assertion, not a plugin setting.

Update shadow tools to construct:

```python
persona = default_persona_registry().resolve(
    "aemeath",
    aliases=(current_alias,),
    relationships=(),
)
behavior = BehaviorPolicy()
projector = ShadowProjector(behavior=behavior, persona_context=persona)
```

- [x] **Step 4: Update current documentation and delete the old parser**

README must list exactly the six configuration keys with Chinese explanations, document empty-group semantics, Provider precedence, ignored legacy keys, and `aemeath` state ownership. Delete `groupmate/config.py` only after `rg` shows no import remains.

- [x] **Step 5: Run the residual audit**

Run:

```bash
rg -n "PluginSettings|GroupPolicy|group_brief|v3_scheduler_enabled|v3_memory_writer_enabled|v3_composition_enabled|reaction_media_enabled|reaction_catalog_path|DEFAULT_RELATIONSHIPS|flatten_plugin_config" groupmate main.py _conf_schema.json README.md pages tests eval
```

Expected: no production/config/current-documentation matches; test matches are limited to the explicit absence-test string tuple and historical `docs/superpowers` is outside the command.

- [x] **Step 6: Run complete verification**

Run:

```bash
./.venv/bin/python -m pytest -q
./.venv/bin/python -m eval.runner --mode deterministic --enforce
./.venv/bin/python -m eval.runner --mode deterministic --enforce --scenarios eval/scenarios/phase2_behavior.jsonl --output /tmp/groupmate-phase2-behavior.json
git diff --check
```

Expected: full pytest passes, both deterministic evaluations report every run passed, and `git diff --check` emits no output.

- [x] **Step 7: Commit**

```bash
git add groupmate/config.py eval/schema.py eval/runner.py eval/shadow_projector.py eval/shadow_export.py eval/build_corpus.py eval/scenarios/baseline.jsonl eval/scenarios/phase2_behavior.jsonl tests/test_eval_schema.py tests/test_eval_runner.py tests/test_shadow_projector.py tests/test_config.py README.md
git commit -m "refactor: finish minimal configuration cleanup"
```
