# Groupmate Trigger and Persona Expression Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make confirmed Persona names and aliases enter a deterministic direct-interaction lane, preserve AstrBot plugin ownership, continue short conversations through a bounded lease, and generate short Persona-specific group-chat replies without copying the reference Bot's literal phrasing.

**Architecture:** Add a pure `PersonaAddressResolver` between platform translation and Social Runtime ingestion. It produces immutable addressing facts and an alias-stripped remainder; the existing external-trigger policy classifies that remainder before attention scheduling. Direct and continuation lanes stay deterministic, while an `ExpressionPlan` shapes the already-approved response using Persona cues; only unaddressed, unleased messages reach ambient cognition.

**Tech Stack:** Python 3 dataclasses, asyncio, SQLite-backed runtime repositories, pytest, vanilla ES modules for the settings page.

---

## File map

- Create `groupmate/social_runtime/addressing.py`: normalize Persona names and resolve direct-address facts without platform I/O.
- Create `groupmate/social_runtime/expression.py`: frozen expression-plan contract and deterministic planning inputs.
- Modify `groupmate/settings.py`: deployment fallback for Persona primary name and confirmed aliases.
- Modify `_conf_schema.json`: expose primary name and confirmed aliases in AstrBot plugin configuration.
- Modify `groupmate/social_runtime/persona/profile.py`: accept a bounded alias list and preserve old published profiles.
- Modify `groupmate/adapters/astrbot_bridge.py`: enrich translated events with the current Persona identity and re-run external ownership on the stripped remainder.
- Modify `groupmate/social_runtime/attention.py`: consume only resolved direct-address facts and keep ambient cognition out of direct interactions.
- Modify `groupmate/social_runtime/world.py`: carry the minimum extra lease evidence required for natural continuation.
- Modify `groupmate/social_runtime/manager.py`: open and advance the enriched lease after a usable reply.
- Modify `groupmate/social_runtime/replying.py`: persist `ExpressionPlan` and inject it into final-generation prompts.
- Modify `groupmate/social_runtime/control/message_traces.py`: record human-readable address, ownership, lease and expression evidence.
- Modify `pages/settings/components/presenters.js` and `pages/settings/components/inspector.js`: show the result and trigger basis before technical diagnostics.
- Modify focused tests under `tests/social_runtime`, `tests/contracts`, `tests/scenarios`, `tests/shared`, and `tests/page`.

### Task 1: Add backward-compatible Persona identity configuration

**Files:**
- Modify: `_conf_schema.json`
- Modify: `groupmate/settings.py`
- Modify: `groupmate/social_runtime/persona/profile.py`
- Test: `tests/shared/test_plugin_skeleton.py`
- Test: `tests/social_runtime/test_persona_profile.py`

- [ ] **Step 1: Write failing settings and profile tests**

```python
def test_persona_identity_settings_normalize_confirmed_aliases():
    settings = SocialRuntimeSettings.from_mapping({
        "persona_name": " 爱弥斯 ",
        "persona_aliases": [" 小爱 ", "爱弥斯", "小爱", ""],
    })
    assert settings.persona_name == "爱弥斯"
    assert settings.persona_aliases == ("小爱",)


def test_old_persona_profile_without_aliases_remains_valid():
    payload = GroupmatePersonaProfile.default().to_mapping()
    payload["identity"].pop("aliases")
    restored = GroupmatePersonaProfile.from_mapping(payload).to_mapping()
    assert restored["identity"]["aliases"] == []


def test_persona_profile_rejects_ambiguous_aliases():
    payload = GroupmatePersonaProfile.default().to_mapping()
    payload["identity"]["name"] = "爱弥斯"
    payload["identity"]["aliases"] = ["爱弥斯", "爱"]
    with pytest.raises(ValueError, match="persona alias"):
        GroupmatePersonaProfile.from_mapping(payload)
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
pytest -q tests/shared/test_plugin_skeleton.py::test_astrbot_config_only_exposes_groupmate_deployment_choices tests/social_runtime/test_persona_profile.py
```

Expected: failures because `persona_name`, `persona_aliases`, and `identity.aliases` do not exist.

- [ ] **Step 3: Implement bounded identity configuration**

Add deployment fields:

```python
@dataclass(frozen=True)
class SocialRuntimeSettings:
    # existing fields remain unchanged
    persona_name: str = "Groupmate"
    persona_aliases: tuple[str, ...] = ()

    @staticmethod
    def _persona_aliases(name: str, values: object) -> tuple[str, ...]:
        source = values if isinstance(values, (list, tuple)) else ()
        normalized = tuple(dict.fromkeys(
            str(value or "").strip() for value in source
            if str(value or "").strip()
        ))
        aliases = tuple(value for value in normalized if value != name)
        if len(aliases) > 12 or any(len(value) < 2 or len(value) > 24 for value in aliases):
            raise ValueError("persona aliases must contain 2-24 characters and at most 12 entries")
        return aliases
```

In `from_mapping`, normalize the name first and pass it into `_persona_aliases`. Add this schema:

```json
"persona_name": {
  "description": "Groupmate 人格名称",
  "type": "string",
  "default": "Groupmate",
  "hint": "用于自称和直接呼唤识别。"
},
"persona_aliases": {
  "description": "已确认的人格别称",
  "type": "list",
  "default": [],
  "hint": "每行一个别称。只有管理员确认的别称会触发直接互动。"
}
```

Change `GroupmatePersonaProfile.sections` to allow the bounded alias tuple and normalize it separately from string fields:

```python
@staticmethod
def _aliases(raw_section: Mapping[str, object], primary_name: str) -> tuple[str, ...]:
    raw = raw_section.get("aliases", ())
    if not isinstance(raw, (list, tuple)):
        raise ValueError("persona aliases must be a list")
    values = tuple(str(value or "").strip() for value in raw)
    if any(not value for value in values):
        raise ValueError("persona alias must not be empty")
    if len(set(values)) != len(values):
        raise ValueError("persona alias must be unique")
    if len(values) > 12 or any(len(value) < 2 or len(value) > 24 for value in values):
        raise ValueError("persona alias must contain 2-24 characters and at most 12 entries")
    if primary_name in values:
        raise ValueError("persona alias must differ from the primary name")
    return values
```

Keep `aliases` optional on input for backward compatibility, store it as a tuple in the frozen identity section, and serialize it as `list(identity["aliases"])` in `to_mapping()`.

- [ ] **Step 4: Run the tests and verify GREEN**

Run:

```bash
pytest -q tests/shared/test_plugin_skeleton.py tests/social_runtime/test_persona_profile.py
```

Expected: all tests pass; the schema exposes the two new Persona fields and old published profiles still load.

- [ ] **Step 5: Commit**

```bash
git add _conf_schema.json groupmate/settings.py groupmate/social_runtime/persona/profile.py tests/shared/test_plugin_skeleton.py tests/social_runtime/test_persona_profile.py
git commit -m "feat: configure persona names and aliases"
```

### Task 2: Resolve direct addressing as immutable facts

**Files:**
- Create: `groupmate/social_runtime/addressing.py`
- Test: `tests/social_runtime/test_addressing.py`

- [ ] **Step 1: Write failing resolver tests**

```python
@pytest.mark.parametrize(
    ("text", "addressed", "kind", "remainder"),
    (
        ("小爱", True, "PURE_ALIAS", ""),
        ("小爱呢", True, "ALIAS_PREFIX", "呢"),
        ("小爱 说话", True, "ALIAS_PREFIX", "说话"),
        ("爱弥斯 bq 开心", True, "ALIAS_PREFIX", "bq 开心"),
        ("你怎么看，小爱", True, "ALIAS_SUFFIX", "你怎么看"),
        ("我觉得小爱这个名字不错", False, "NONE", "我觉得小爱这个名字不错"),
        ("这是小爱情节", False, "NONE", "这是小爱情节"),
    ),
)
def test_resolver_distinguishes_calls_from_body_mentions(text, addressed, kind, remainder):
    result = PersonaAddressResolver("爱弥斯", ("小爱",)).resolve_text(text)
    assert result.addressed_to_bot is addressed
    assert result.address_kind == kind
    assert result.address_remainder == remainder


def test_platform_at_and_reply_are_high_confidence_without_alias_text():
    resolver = PersonaAddressResolver("爱弥斯", ("小爱",))
    at = resolver.resolve(text="早", mentions_bot=True, reply_to_bot=False)
    reply = resolver.resolve(text="然后呢", mentions_bot=False, reply_to_bot=True)
    assert (at.address_kind, reply.address_kind) == ("AT", "REPLY")
    assert at.address_confidence == reply.address_confidence == "HIGH"


def test_new_alias_is_only_recorded_as_a_candidate():
    result = PersonaAddressResolver("爱弥斯", ("小爱",)).resolve_text(
        "小爱以后叫你爱酱"
    )
    assert result.addressed_to_bot is True
    assert result.alias_candidate == "爱酱"
    assert "爱酱" not in PersonaAddressResolver("爱弥斯", ("小爱",)).names
```

- [ ] **Step 2: Run the resolver tests and verify RED**

Run:

```bash
pytest -q tests/social_runtime/test_addressing.py
```

Expected: import failure because `PersonaAddressResolver` is not implemented.

- [ ] **Step 3: Implement the pure resolver**

```python
@dataclass(frozen=True)
class AddressResolution:
    addressed_to_bot: bool
    address_kind: str
    matched_alias: str | None
    address_remainder: str
    address_confidence: str
    alias_candidate: str | None = None


class PersonaAddressResolver:
    def __init__(self, primary_name: str, aliases: tuple[str, ...] = ()) -> None:
        names = tuple(dict.fromkeys((primary_name.strip(), *(a.strip() for a in aliases))))
        if any(not value for value in names):
            raise ValueError("persona address names must not be empty")
        self.names = tuple(sorted(names, key=len, reverse=True))

    def resolve(self, *, text: str, mentions_bot: bool, reply_to_bot: bool) -> AddressResolution:
        if reply_to_bot:
            return AddressResolution(True, "REPLY", None, text.strip(), "HIGH")
        if mentions_bot:
            return AddressResolution(True, "AT", None, text.strip(), "HIGH")
        return self.resolve_text(text)

    def resolve_text(self, text: str) -> AddressResolution:
        value = " ".join(str(text or "").strip().split())
        for name in self.names:
            if value == name:
                return AddressResolution(True, "PURE_ALIAS", name, "", "HIGH")
            if value.startswith(name):
                remainder = value[len(name):].lstrip(" ，,。.!！?？~～…:：")
                if self._prefix_boundary(value[len(name):]):
                    return AddressResolution(
                        True,
                        "ALIAS_PREFIX",
                        name,
                        remainder,
                        "HIGH",
                        self._candidate(remainder),
                    )
            if value.endswith(name):
                leading = value[:-len(name)]
                if leading and leading[-1] in " ，,：:、":
                    remainder = leading.rstrip(" ，,：:、")
                    if remainder:
                        return AddressResolution(True, "ALIAS_SUFFIX", name, remainder, "HIGH")
        return AddressResolution(False, "NONE", None, value, "NONE", None)

    @staticmethod
    def _candidate(remainder: str) -> str | None:
        match = re.fullmatch(r"(?:以后)?叫你([\w\u3400-\u9fff·]{2,24})[。.!！?？~～]*", remainder)
        return match.group(1) if match else None
```

Implement `_prefix_boundary` with an explicit punctuation/whitespace boundary plus a small set of complete conversational continuations such as `呢、在、说、讲、帮、看、回、你、来、给、能、会、要、别`. Do not use unconstrained substring or fuzzy matching.

- [ ] **Step 4: Run the resolver tests and verify GREEN**

Run:

```bash
pytest -q tests/social_runtime/test_addressing.py
```

Expected: all resolver cases pass.

- [ ] **Step 5: Commit**

```bash
git add groupmate/social_runtime/addressing.py tests/social_runtime/test_addressing.py
git commit -m "feat: resolve persona direct addressing"
```

### Task 3: Reclassify external ownership after removing the alias

**Files:**
- Modify: `groupmate/adapters/astrbot_bridge.py`
- Modify: `groupmate/adapters/astrbot_events.py`
- Test: `tests/contracts/test_astrbot_events.py`
- Test: `tests/scenarios/test_chat_mainline.py`

- [ ] **Step 1: Write failing bridge scenarios**

```python
async def _run_alias_case(tmp_path, text):
    context = _Context()
    settings = SocialRuntimeSettings.from_mapping({
        "enabled_groups": ["885617919"],
        "runtime_mode": "SOCIAL_RUNTIME",
        "generation_provider": "provider:text",
        "persona_name": "爱弥斯",
        "persona_aliases": ["小爱"],
        "external_command_prefixes": ["bq=astrbot.meme"],
    })
    bridge = AstrBotSocialRuntimeBridge(context, settings, tmp_path, clock=lambda: 100)
    await bridge.start()
    await bridge.handle_event(_event("alias-case", text))
    trace = bridge.trace_repository.query(
        persona_id=settings.persona_id,
        group_id="885617919",
    )["items"][0]["summary"]
    await bridge.close()
    return context, trace


def test_alias_prefixed_external_command_stays_owned_by_astrbot(tmp_path):
    context, trace = asyncio.run(_run_alias_case(tmp_path, "小爱 bq 开心"))
    assert context.model_calls == []
    assert trace["route"]["owner"] == "EXTERNAL_PLUGIN"
    assert trace["route"]["reason"] == "匹配已配置的外部触发规则"


def test_alias_prefixed_social_call_enters_direct_lane(tmp_path):
    context, trace = asyncio.run(_run_alias_case(tmp_path, "小爱说话"))
    assert trace["decision"]["participation_lane"] == "DIRECT_FAST"
    cognition_calls = [
        call for call in context.model_calls
        if "结构化群聊观察器" in call["system_prompt"]
    ]
    assert cognition_calls == []
    assert len(context.client.calls) == 1
```

Use the existing `_Context`, `_event`, bridge startup, trace query and shutdown helpers already present in `tests/scenarios/test_chat_mainline.py`; the assertions must inspect real trace and outbox behavior rather than mocks of the resolver.

- [ ] **Step 2: Run the scenarios and verify RED**

Run:

```bash
pytest -q tests/scenarios/test_chat_mainline.py -k 'alias_prefixed'
```

Expected: `小爱 bq 开心` is not externally owned and `小爱说话` falls into AMBIENT.

- [ ] **Step 3: Enrich events in the bridge**

Store one `ExternalTriggerPolicy` and the `ConfigVersionRepository` on the bridge. Add `_profile_snapshot(group_id)` with this precedence: an explicitly published per-group `persona_profile` controls identity; otherwise the plugin's `persona_name` and `persona_aliases` are overlaid on the default profile. After translation, construct `PersonaAddressResolver` from that current profile, resolve the message and classify the stripped remainder:

```python
def _resolve_interaction(self, event: SocialEventEnvelope) -> SocialEventEnvelope:
    profile = self._profile_snapshot(str(event.group_id or "")).config["persona_profile"]
    identity = profile["identity"]
    resolver = PersonaAddressResolver(
        str(identity["name"]),
        tuple(identity.get("aliases", ())),
    )
    resolution = resolver.resolve(
        text=str(event.payload.get("text") or ""),
        mentions_bot=bool(event.payload.get("mentions_bot")),
        reply_to_bot=bool(event.payload.get("reply_to_bot")),
    )
    ownership = self._external_trigger_policy.classify(
        resolution.address_remainder
    )
    payload = dict(event.payload)
    payload.update(asdict(resolution))
    payload["direct_address"] = resolution.addressed_to_bot
    if ownership is not None:
        payload.update({
            "interaction_owner": ownership.owner.value,
            "social_eligible": ownership.social_eligible,
            "owner_ref": ownership.owner_ref,
            "ownership_source": "alias_stripped_" + ownership.source,
            "external_trigger_kind": ownership.trigger_kind,
            "external_trigger_value": ownership.trigger_value,
        })
    return SocialEventEnvelope.create(**{**event.to_dict(), "payload": payload})
```

Call `_resolve_interaction` in both `handle_event` and `observe_event` immediately after pure platform translation. Keep `AstrBotEventTranslator` responsible for platform facts and direct unprefixed external triggers; remove no existing fact fields.

- [ ] **Step 4: Run focused ownership and mainline tests**

Run:

```bash
pytest -q tests/contracts/test_astrbot_events.py tests/scenarios/test_chat_mainline.py -k 'external or alias_prefixed or live_chat'
```

Expected: alias-prefixed commands are handed off, alias-prefixed social calls use `DIRECT_FAST`, and existing unprefixed triggers remain unchanged.

- [ ] **Step 5: Commit**

```bash
git add groupmate/adapters/astrbot_bridge.py groupmate/adapters/astrbot_events.py tests/contracts/test_astrbot_events.py tests/scenarios/test_chat_mainline.py
git commit -m "feat: route alias calls through ownership and direct chat"
```

### Task 4: Make direct-address evidence explicit and model-independent

**Files:**
- Modify: `groupmate/social_runtime/attention.py`
- Modify: `groupmate/social_runtime/control/message_traces.py`
- Test: `tests/scenarios/test_attention_windows.py`
- Test: `tests/evaluation/test_shadow_review.py`

- [ ] **Step 1: Write failing policy and evidence tests**

```python
def test_confirmed_alias_is_fast_without_ambient_worker():
    event = _message(1, 100, "u1")
    event = SocialEventEnvelope.create(**{
        **event.to_dict(),
        "payload": {
            **dict(event.payload),
            "direct_address": True,
            "address_kind": "ALIAS_PREFIX",
            "matched_alias": "小爱",
            "address_remainder": "说话",
        },
    })
    world = GroupWorldProjector().apply(GroupWorldProjector().empty(event.group_id), event)
    frame = AttentionScheduler().on_event(event, world, _persona(), now=100)[0]
    assert frame.trigger_kind == "FAST"
    assert frame.requested_workers == ()
```

Add a trace assertion that the human-readable result reason is `命中人格别称：小爱`, while the stored evidence keeps `address_kind` and never stores unbounded internal model text.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
pytest -q tests/scenarios/test_attention_windows.py -k alias tests/evaluation/test_shadow_review.py -k alias
```

Expected: the lane may already be FAST, but trace evidence and the alias-specific reason are missing.

- [ ] **Step 3: Persist bounded address evidence**

Keep `_is_fast` deterministic and add an explicit diagnostic mapping:

```python
def _direct_reason(event: SocialEventEnvelope) -> str:
    kind = str(event.payload.get("address_kind") or "")
    if kind == "AT":
        return "明确 @ 机器人"
    if kind == "REPLY":
        return "回复了 Bot 的上一条消息"
    alias = " ".join(str(event.payload.get("matched_alias") or "").split())[:24]
    if kind in {"PURE_ALIAS", "ALIAS_PREFIX", "ALIAS_SUFFIX"} and alias:
        return f"命中人格别称：{alias}"
    return "明确对 Bot 发起互动"
```

Project `address_kind`, bounded `matched_alias`, `addressed_to_bot`, `alias_candidate`, and the reason into `summary.route`/`summary.judgement`; do not add them to cognitive model input for direct lanes. `alias_candidate` is display-only evidence: it never changes the resolver's active names until the administrator adds it to a published Persona profile or plugin configuration.

- [ ] **Step 4: Run focused policy and trace tests**

Run:

```bash
pytest -q tests/scenarios/test_attention_windows.py tests/evaluation/test_shadow_review.py -k 'alias or direct'
```

Expected: direct aliases are strategy decisions with readable evidence and no ambient worker diagnostic.

- [ ] **Step 5: Commit**

```bash
git add groupmate/social_runtime/attention.py groupmate/social_runtime/control/message_traces.py tests/scenarios/test_attention_windows.py tests/evaluation/test_shadow_review.py
git commit -m "feat: explain deterministic alias participation"
```

### Task 5: Enrich the bounded conversation lease

**Files:**
- Modify: `groupmate/social_runtime/world.py`
- Modify: `groupmate/social_runtime/manager.py`
- Modify: `groupmate/social_runtime/attention.py`
- Test: `tests/scenarios/test_chat_mainline.py`
- Test: `tests/scenarios/test_attention_windows.py`

- [ ] **Step 1: Write failing continuation tests**

```python
from dataclasses import replace

from groupmate.social_runtime.world import ConversationLease


def test_same_member_followup_can_continue_from_last_bot_reply_even_after_topic_projection_moves():
    projector = GroupWorldProjector()
    scheduler = AttentionScheduler()
    direct = _message(1, 100, "u1")
    world = projector.apply(projector.empty("885617919"), direct)
    world = replace(
        world,
        conversation_lease=ConversationLease(
            target_id="u1",
            topic_id="m1",
            source_plan_id="reply:1",
            opened_at=100,
            expires_at=300,
            remaining_turns=5,
        ),
    )
    followup = SocialEventEnvelope.create(**social_event_values(
        event_id="qq:m2",
        source_message_id="m2",
        actor_id="u1",
        occurred_at=150,
        received_at=150,
        correlation_id="corr:m2",
        payload={"text": "然后呢"},
    ))
    world = projector.apply(world, followup)
    frame = scheduler.on_event(followup, world, _persona(), now=150)[0]
    assert frame.trigger_kind == "CONTINUATION"
    assert frame.focus_topic_ids == ("m2",)


def test_new_direct_caller_preempts_existing_lease():
    projector = GroupWorldProjector()
    scheduler = AttentionScheduler()
    base = projector.apply(projector.empty("885617919"), _message(1, 100, "u1"))
    world = replace(base, conversation_lease=ConversationLease(
        "u1", "m1", "reply:1", 100, 300, 5
    ))
    direct = SocialEventEnvelope.create(**social_event_values(
        event_id="qq:m2",
        source_message_id="m2",
        actor_id="u2",
        occurred_at=101,
        received_at=101,
        correlation_id="corr:m2",
        payload={"text": "小爱说话", "direct_address": True},
    ))
    world = projector.apply(world, direct)
    frame = scheduler.on_event(direct, world, _persona(), now=101)[0]
    assert frame.trigger_kind == "FAST"
    assert frame.candidate_audiences == ("u2",)


def test_external_capability_never_advances_social_lease():
    projector = GroupWorldProjector()
    scheduler = AttentionScheduler()
    base = projector.apply(projector.empty("885617919"), _message(1, 100, "u1"))
    lease = ConversationLease("u1", "m1", "reply:1", 100, 300, 5)
    world = replace(base, conversation_lease=lease)
    external = SocialEventEnvelope.create(**social_event_values(
        event_id="qq:m2",
        source_message_id="m2",
        actor_id="u1",
        occurred_at=101,
        received_at=101,
        correlation_id="corr:m2",
        payload={"text": "bq 开心", "social_eligible": False},
    ))
    world = projector.apply(world, external)
    assert scheduler.on_event(external, world, _persona(), now=101) == ()
    assert world.conversation_lease == lease
```

Use real `GroupWorldProjector`, `AttentionScheduler`, and the bridge mainline rather than stubbing the lease.

- [ ] **Step 2: Run continuation tests and verify RED**

Run:

```bash
pytest -q tests/scenarios/test_attention_windows.py tests/scenarios/test_chat_mainline.py -k 'followup or preempts or capability_never_advances'
```

Expected: the differing topic follow-up does not continue and the enriched evidence fields do not exist.

- [ ] **Step 3: Add the minimum continuation evidence**

Extend the frozen lease:

```python
@dataclass(frozen=True)
class ConversationLease:
    target_id: str
    topic_id: str
    source_plan_id: str
    opened_at: int
    expires_at: int
    remaining_turns: int
    last_bot_event_id: str | None = None
    unresolved_intent: str | None = None
    last_activity_at: int | None = None
```

Populate those fields in `record_usable_reply`. Update `_matches_conversation_lease` in this order:

```python
if event.payload.get("social_eligible") is False:
    return False
if event.payload.get("direct_address"):
    return False  # FAST has already won and may change the target
if event.actor_id != lease.target_id or now > lease.expires_at or lease.remaining_turns <= 0:
    return False
if event.payload.get("reply_to_bot"):
    return True
if AttentionScheduler._topic_id(world, event) == lease.topic_id:
    return True
return _looks_like_short_followup(str(event.payload.get("text") or ""))
```

Keep `_looks_like_short_followup` bounded to conversational continuations such as `然后呢、后来呢、为什么、怎么了、那怎么办、继续、还有呢` and short answer fragments. Do not call a model from this method and do not treat arbitrary messages by the same member as continuation.

- [ ] **Step 4: Run focused lease tests**

Run:

```bash
pytest -q tests/scenarios/test_attention_windows.py tests/scenarios/test_chat_mainline.py -k 'lease or continuation or followup or preempts'
```

Expected: valid follow-ups continue, direct callers preempt, and external capabilities leave the lease untouched.

- [ ] **Step 5: Commit**

```bash
git add groupmate/social_runtime/world.py groupmate/social_runtime/manager.py groupmate/social_runtime/attention.py tests/scenarios/test_attention_windows.py tests/scenarios/test_chat_mainline.py
git commit -m "feat: continue bounded member conversations"
```

### Task 6: Plan Persona-specific group-chat expression

**Files:**
- Create: `groupmate/social_runtime/expression.py`
- Modify: `groupmate/social_runtime/replying.py`
- Modify: `groupmate/social_runtime/manager.py`
- Modify: `groupmate/adapters/astrbot_bridge.py`
- Test: `tests/social_runtime/actions/test_replying.py`
- Test: `tests/social_runtime/test_expression.py`

- [ ] **Step 1: Write failing expression-contract tests**

```python
def test_direct_expression_reacts_then_answers_and_may_leave_a_hook():
    profile = GroupmatePersonaProfile.default().to_mapping()
    profile["identity"]["name"] = "爱弥斯"
    plan = ExpressionPlanner().plan(
        lane="DIRECT_FAST",
        act="respond_to_direct_interaction",
        source_text="小爱你怎么不理我",
        persona_profile=profile,
    )
    assert plan.reaction_stance == "acknowledge_relationship"
    assert plan.core_response_goal == "respond_to_direct_interaction"
    assert plan.persona_cues[0] == "爱弥斯"
    assert plan.followup_hook == "optional_if_natural"
    assert plan.message_count in {1, 2}


def test_expression_uses_persona_cues_without_reference_bot_phrases():
    prompt = ReplyExecutor._system_prompt(reply_plan, persona_profile)
    assert "爱弥斯" in prompt
    assert "咪呀" not in prompt
    assert "花房" not in prompt
    assert "先接住对方的情绪和关系信号" in prompt
```

- [ ] **Step 2: Run expression tests and verify RED**

Run:

```bash
pytest -q tests/social_runtime/test_expression.py tests/social_runtime/actions/test_replying.py -k expression
```

Expected: `ExpressionPlan` and `ExpressionPlanner` do not exist and reply plans carry only a generic style directive.

- [ ] **Step 3: Implement the frozen expression plan**

```python
@dataclass(frozen=True)
class ExpressionPlan:
    reaction_stance: str
    core_response_goal: str
    persona_cues: tuple[str, ...]
    boundary_style: str
    followup_hook: str
    message_count: int
    capability_request: str | None = None

    def __post_init__(self) -> None:
        if self.message_count not in {1, 2}:
            raise ValueError("expression message_count must be 1 or 2")
        if len(self.persona_cues) > 6:
            raise ValueError("expression persona cues must be bounded")


class ExpressionPlanner:
    _RELATION_WORDS = ("不理我", "不要我", "喜欢", "想你", "生气", "难过", "高兴", "谢谢")

    def plan(
        self,
        *,
        lane: str,
        act: str,
        source_text: str,
        persona_profile: Mapping[str, object],
    ) -> ExpressionPlan:
        identity = persona_profile.get("identity", {})
        expression = persona_profile.get("expression", {})
        participation = persona_profile.get("participation", {})
        name = str(identity.get("name") or "Groupmate").strip()[:24]
        cues = tuple(
            value for value in (
                name,
                str(identity.get("background") or "").strip()[:120],
                str(expression.get("tone") or "").strip()[:120],
                str(expression.get("language_habits") or "").strip()[:120],
            ) if value
        )
        relationship = any(word in source_text for word in self._RELATION_WORDS)
        reaction = (
            "acknowledge_relationship" if relationship
            else "continue_current_exchange" if lane == "CONTINUATION"
            else "attentive"
        )
        return ExpressionPlan(
            reaction_stance=reaction,
            core_response_goal=act,
            persona_cues=cues,
            boundary_style=str(participation.get("stay_silent_when") or "明确且友好")[:120],
            followup_hook=(
                "optional_if_natural"
                if lane in {"DIRECT_FAST", "CONTINUATION"}
                else "only_if_it_adds_value"
            ),
            message_count=2 if relationship else 1,
        )
```

`ExpressionPlanner.plan` uses the approved lane, act, source text and safe Persona sections. It sets a relationship-aware reaction for direct emotional language, continuation posture for leased replies, and a low-interruption posture for ambient replies. It copies no literal phrase from the reference data.

Add `expression: ExpressionPlan` to `ReplyPlan`. In `ReplyPlanRepository._decode`, create a conservative default for older stored plans. Change the planner contract to receive the frozen Persona profile used by the same evaluation:

```python
def plan(
    self,
    evaluation: object,
    *,
    now: int,
    persona_profile: Mapping[str, object],
) -> ReplyPlan | None:
    frame = getattr(evaluation, "frame", None)
    governor = getattr(evaluation, "governor_result", None)
    if not getattr(evaluation, "accepted", False) or frame is None or governor is None:
        return None
    if governor.outcome != "ACT" or len(governor.selected_intention_ids) != 1:
        return None
    intention_id = governor.selected_intention_ids[0]
    selected = next(
        (item for item in getattr(evaluation, "candidates", ())
         if item.intention_id == intention_id),
        None,
    )
    if selected is None or selected.expires_at <= int(now):
        return None
    expression = self._expression_planner.plan(
        lane=str(getattr(evaluation, "participation_lane", "AMBIENT")),
        act=selected.proposed_act,
        source_text=str(evaluation.source_event.payload.get("text") or ""),
        persona_profile=persona_profile,
    )
    return self._build_plan(
        evaluation=evaluation,
        frame=frame,
        selected=selected,
        intention_id=intention_id,
        expression=expression,
        now=int(now),
    )
```

Extract the current `ReplyPlan(...)` construction verbatim into `_build_plan(...)` and add only `expression=expression`; this keeps identity, expiry, target, topic and style construction unchanged.

Expose a manager read method that returns `profile.to_mapping()` only when `group_id` and `config_version` match the frozen evaluation. In `_handle_evaluations`, obtain that mapping once, pass it to `ReplyPlanner.plan`, `ReplyExecutor.preview`, and `ReplyExecutor.execute_with_result`, replacing the current `{"persona_id": ...}` placeholder. This prevents trigger-time identity and generation-time identity from diverging.

Pass the expression plan to `_system_prompt` with these explicit instructions:

```python
"按顺序组织：可选即时反应、核心回应、少量人格化补充、可选续聊接口。"
"先接住对方的情绪和关系信号，再处理事实；没有明显情绪时不要硬演。"
"拒绝时明确边界并给简短理由；技术回答给可能原因和一个可执行步骤。"
"只使用 Persona 中提供的自称、语气和背景，不模仿任何参考 Bot 的固定口癖。"
```

- [ ] **Step 4: Run reply and expression tests**

Run:

```bash
pytest -q tests/social_runtime/test_expression.py tests/social_runtime/actions/test_replying.py
```

Expected: expression plans are persisted, old reply plans remain readable, and prompts contain only the active Persona cues.

- [ ] **Step 5: Commit**

```bash
git add groupmate/social_runtime/expression.py groupmate/social_runtime/replying.py groupmate/social_runtime/manager.py groupmate/adapters/astrbot_bridge.py tests/social_runtime/test_expression.py tests/social_runtime/actions/test_replying.py
git commit -m "feat: plan persona-specific group chat expression"
```

### Task 7: Show the trigger mechanism before technical diagnostics

**Files:**
- Modify: `groupmate/social_runtime/control/message_traces.py`
- Modify: `pages/settings/components/presenters.js`
- Modify: `pages/settings/components/inspector.js`
- Modify: `pages/settings/fixtures/fake_bridge.js`
- Test: `tests/page/test_product_ui.py`
- Test: `tests/page/test_shadow_console.py`

- [ ] **Step 1: Write failing presenter tests**

```python
def test_runtime_result_prefers_human_trigger_basis():
    result = _run_presenter(
        "const item={route:{owner:'GROUPMATE',address_kind:'ALIAS_PREFIX',"
        "matched_alias:'小爱'},decision:{outcome:'ACT',"
        "participation_lane:'DIRECT_FAST'}};"
        "console.log(JSON.stringify(presenter.traceResultReason(item)));"
    )
    assert result == "命中人格别称：小爱"


def test_continuation_result_does_not_say_generic_formal_runtime_will_not_reply():
    result = _run_presenter(
        "const item={decision:{outcome:'ACT',would_reply:true,"
        "participation_lane:'CONTINUATION'}};"
        "console.log(JSON.stringify(presenter.traceResultHeadline(item)));"
    )
    assert result == "继续当前对话"
```

Use the existing Node presenter harness in `tests/page/test_product_ui.py` rather than introducing a browser dependency.

- [ ] **Step 2: Run page tests and verify RED**

Run:

```bash
pytest -q tests/page/test_product_ui.py tests/page/test_shadow_console.py
```

Expected: alias basis is absent and ACT always renders the same generic headline.

- [ ] **Step 3: Implement concise result-first labels**

Update presenter mapping:

```javascript
export function traceResultHeadline(summary = {}) {
  if (String(summary.route?.owner || "").toUpperCase() === "EXTERNAL_PLUGIN") return "由外部能力处理";
  const decision = summary.decision || {};
  const outcome = String(decision.outcome || decision.pre_gate_outcome || "").toUpperCase();
  if (outcome === "ACT") {
    if (decision.participation_lane === "CONTINUATION") return "继续当前对话";
    if (decision.participation_lane === "DIRECT_FAST") return "会回应这次呼唤";
    return "适合加入当前话题";
  }
  if (!outcome || outcome === "PENDING") return "等待完成判断";
  if (String(summary.judgement?.status || "") === "unavailable") return "判断未完成";
  return ({
    OBSERVE: "本轮暂不参与",
    SILENCE: "本轮不回复",
    DEFER: "稍后重新判断",
  })[outcome] || "已完成判断";
}
```

Make `traceResultReason` prefer bounded address/lease/ownership evidence, then model judgement, then governance reason. Extend `record_plan` with a safe expression projection:

```python
expression = getattr(plan, "expression", None)
if expression is not None:
    summary["expression"] = {
        "reaction_stance": self._safe_text(expression.reaction_stance, 40),
        "core_response_goal": self._safe_text(expression.core_response_goal, 80),
        "followup_hook": self._safe_text(expression.followup_hook, 40),
        "message_count": max(1, min(2, int(expression.message_count))),
        "capability_request": self._safe_text(expression.capability_request, 60) or None,
    }
```

In the inspector, render `触发方式、命中别称、能力归属、对话对象、租约状态、表达计划` above the folded technical section. Never render raw IDs, `persona_cues`, unbounded model output, or Persona background.

- [ ] **Step 4: Run page tests and verify GREEN**

Run:

```bash
pytest -q tests/page/test_product_ui.py tests/page/test_shadow_console.py
```

Expected: direct, continuation, external and ambient results are visually distinct and existing theme/layout contracts pass.

- [ ] **Step 5: Commit**

```bash
git add groupmate/social_runtime/control/message_traces.py pages/settings/components/presenters.js pages/settings/components/inspector.js pages/settings/fixtures/fake_bridge.js tests/page/test_product_ui.py tests/page/test_shadow_console.py
git commit -m "feat: explain chat trigger and expression outcomes"
```

### Task 8: Verify the complete mechanism with a small scenario matrix

**Files:**
- Modify: `tests/scenarios/test_chat_mainline.py`
- Modify: `docs/operations/social-runtime-shadow.md`

- [ ] **Step 1: Add one end-to-end scenario matrix**

```python
from groupmate.adapters.deepseek_cognition import DirectCognitionResponse


class _MatrixCognition:
    model = "test-cognition"

    def __init__(self):
        self.calls = 0

    def input_bytes(self, facts):
        return len(json.dumps(facts, ensure_ascii=False).encode("utf-8"))

    async def classify(self, facts):
        self.calls += 1
        evidence = facts["events"][-1]["id"]
        return DirectCognitionResponse(
            verdict={
                "decision": "silence",
                "signal": "none",
                "target_id": None,
                "evidence_event_ids": [evidence],
                "confidence": 0.9,
                "disruption": 0.8,
                "novelty": 0.1,
                "reason": "普通正文提及，不构成直接呼唤",
            },
            latency_ms=1,
            request_bytes=1,
            backend="test",
            model=self.model,
        )

    async def close(self):
        return None


async def _trigger_case(tmp_path, message):
    context = _Context()
    cognition = _MatrixCognition()
    settings = SocialRuntimeSettings.from_mapping({
        "enabled_groups": ["885617919"],
        "runtime_mode": "SHADOW",
        "generation_provider": "provider:text",
        "cognition_api_key": "sk-test",
        "persona_name": "爱弥斯",
        "persona_aliases": ["小爱"],
        "external_command_prefixes": ["bq=astrbot.meme"],
    })
    bridge = AstrBotSocialRuntimeBridge(
        context,
        settings,
        tmp_path,
        clock=lambda: 100,
        cognition_client_factory=lambda _: cognition,
    )
    await bridge.start()
    await bridge.handle_event(_event("matrix", message))
    due = await bridge.manager.drain(now=102)
    await bridge._handle_evaluations(due)
    summary = bridge.trace_repository.query(
        persona_id=settings.persona_id,
        group_id="885617919",
    )["items"][0]["summary"]
    ambient_calls = cognition.calls
    await bridge.close()
    return summary, ambient_calls


@pytest.mark.parametrize(
    ("message", "expected_owner", "expected_lane", "ambient_calls"),
    (
        ("小爱", "GROUPMATE", "DIRECT_FAST", 0),
        ("小爱说话", "GROUPMATE", "DIRECT_FAST", 0),
        ("小爱 bq 开心", "EXTERNAL_PLUGIN", None, 0),
        ("我觉得小爱这个名字不错", "GROUPMATE", "AMBIENT", 1),
    ),
)
def test_persona_trigger_matrix(message, expected_owner, expected_lane, ambient_calls, tmp_path):
    summary, actual_ambient_calls = asyncio.run(_trigger_case(tmp_path, message))
    assert summary["route"]["owner"] == expected_owner
    assert summary["decision"].get("participation_lane") == expected_lane
    assert actual_ambient_calls == ambient_calls
```

Add a second scenario: direct call -> usable reply -> `然后呢` -> continuation reply -> external command, asserting two sent/preview replies, a decremented lease and no lease advancement by the external command.

- [ ] **Step 2: Run the scenario matrix**

Run:

```bash
pytest -q tests/scenarios/test_chat_mainline.py
```

Expected: all direct, external, continuation and ambient cases pass with no unexpected model calls.

- [ ] **Step 3: Document SHADOW verification**

Add this operator checklist to `docs/operations/social-runtime-shadow.md`:

```markdown
## Persona 触发机制复核

1. 分别发送主名称、确认别称、带别称的外部命令、普通正文提及和一次自然追问。
2. 运行中心应依次显示“会回应这次呼唤”“由外部能力处理”“普通群聊观察”“继续当前对话”。
3. 主名称、别称和续聊不得出现 `ambient_social_assessor`；普通正文提及可以出现。
4. SHADOW 只预览表达，不建立真实对话租约；正式运行仅在可用回复成功发送后建立租约。
5. 技术详情中不得出现 API Key、原始模型异常、内部提示词或原始成员 ID。
```

- [ ] **Step 4: Run only the relevant regression set**

Run:

```bash
pytest -q tests/shared/test_plugin_skeleton.py tests/social_runtime/test_persona_profile.py tests/social_runtime/test_addressing.py tests/social_runtime/test_expression.py tests/social_runtime/actions/test_replying.py tests/contracts/test_astrbot_events.py tests/scenarios/test_attention_windows.py tests/scenarios/test_chat_mainline.py tests/page/test_product_ui.py tests/page/test_shadow_console.py
git diff --check
```

Expected: focused tests pass and `git diff --check` prints no output. Do not run the unrelated full suite unless a focused failure indicates a cross-cutting regression.

- [ ] **Step 5: Commit**

```bash
git add tests/scenarios/test_chat_mainline.py docs/operations/social-runtime-shadow.md
git commit -m "test: verify persona trigger and continuation mechanism"
```
