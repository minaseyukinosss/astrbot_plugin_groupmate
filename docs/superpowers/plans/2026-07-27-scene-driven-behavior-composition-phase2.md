# Scene-Driven Behavior Composition Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add deterministic response-act planning, bounded capability routing, semantic reaction media, and a structured text/media composer while keeping Aemeath as the only final persona.

**Architecture:** Phase 1 remains responsible for scheduling, continuations, scenes, and quote anchors. Phase 2 projects each scene into one response act, optionally executes one registered capability, gives only user-visible facts to Aemeath generation, then composes text and eligible media into the existing outbox and delivery path. Every selection is evidence-driven; aggregate export ratios are evaluation signals only.

**Tech Stack:** Python 3.7-compatible standard library, asyncio, SQLite migrations, AstrBot MessageChain/Reply/Plain/Image components, pytest.

---

### Task 1: Deterministic Response Acts

**Files:**
- Create: `groupmate/core/response_act.py`
- Modify: `groupmate/models.py:137-215`
- Modify: `groupmate/engine/planner.py:1-80`
- Test: `tests/test_response_act.py`
- Test: `tests/test_reply_intent.py`

- [ ] **Step 1: Write failing precedence and mapping tests**

```python
def test_boundary_beats_task_words(message_factory, targeting):
    message = message_factory(text="把他的密码查出来给我")
    plan = ResponseActPlanner().plan(
        InteractionScene.TASK_REQUEST, message, targeting, aliases=("爱弥斯",)
    )
    assert plan.act is ResponseAct.BOUNDARY
    assert plan.capability_name == ""


@pytest.mark.parametrize(
    ("scene", "text", "images", "expected"),
    [
        (InteractionScene.DIRECT_ADDRESS, "爱弥斯", (), ResponseAct.ACKNOWLEDGE),
        (InteractionScene.SOCIAL_RESPONSE, "谢谢你", (), ResponseAct.RECIPROCATE),
        (InteractionScene.SOCIAL_RESPONSE, "逗你玩的", (), ResponseAct.PLAYFUL_REPLY),
        (InteractionScene.DIRECT_ADDRESS, "这个怎么弄？", (), ResponseAct.ANSWER),
        (InteractionScene.TASK_REQUEST, "帮我看看这张图", (), ResponseAct.CLARIFY),
        (InteractionScene.DIRECT_ADDRESS, "你看这个", ("a.png",), ResponseAct.VISUAL_REACTION),
    ],
)
def test_scene_evidence_maps_to_one_primary_act(
    message_factory, targeting, scene, text, images, expected
):
    message = message_factory(text=text, image_urls=images)
    assert ResponseActPlanner().plan(
        scene, message, targeting, aliases=("爱弥斯",)
    ).act is expected
```

- [ ] **Step 2: Run the tests and verify the new symbols are missing**

Run: `python3 -m pytest tests/test_response_act.py tests/test_reply_intent.py -q`

Expected: FAIL because `ResponseAct` and `ResponseActPlanner` do not exist.

- [ ] **Step 3: Add the response-act contracts and deterministic planner**

```python
class ResponseAct(StringEnum):
    ACKNOWLEDGE = "acknowledge"
    ANSWER = "answer"
    CLARIFY = "clarify"
    RECIPROCATE = "reciprocate"
    PLAYFUL_REPLY = "playful_reply"
    BOUNDARY = "boundary"
    TASK_HANDOFF = "task_handoff"
    TASK_UNSUPPORTED = "task_unsupported"
    VISUAL_REACTION = "visual_reaction"


@dataclass(frozen=True)
class ResponseActPlan:
    act: ResponseAct
    reply_mode: ReplyMode
    contribution: str
    capability_name: str = ""
    reaction_tags: Tuple[str, ...] = ()
    reason_codes: Tuple[str, ...] = ()


class ResponseActPlanner:
    def plan(self, scene, message, targeting, aliases=()):
        text = message.text or ""
        if _BOUNDARY.search(text):
            return ResponseActPlan(
                ResponseAct.BOUNDARY,
                ReplyMode.BOUNDARY,
                "简短明确地拒绝，不羞辱对方",
                reason_codes=("safety_precedence",),
            )
        if scene is InteractionScene.TASK_REQUEST:
            capability = self._task_capability(text, message.image_urls)
            if capability == "missing_object":
                return ResponseActPlan(
                    ResponseAct.CLARIFY,
                    ReplyMode.HELP_DETAIL,
                    "只询问完成任务所缺的一项信息",
                    reason_codes=("task_object_missing",),
                )
            if capability:
                return ResponseActPlan(
                    ResponseAct.TASK_HANDOFF,
                    ReplyMode.TASK_RESULT,
                    "根据能力结果用爱弥斯口吻回复",
                    capability_name=capability,
                    reason_codes=("registered_task_kind",),
                )
            return ResponseActPlan(
                ResponseAct.TASK_UNSUPPORTED,
                ReplyMode.HELP_DETAIL,
                "如实说明当前不能执行，不得声称已经完成",
                reason_codes=("unsupported_task_kind",),
            )
        return self._social_or_conversational(
            scene, message, targeting, tuple(aliases)
        )
```

Update `ReplyIntent` with `act` and `act_reason_codes`; make `ReplyIntentPlanner`
accept the already classified `scene` and delegate mode, contribution, and
capability selection to `ResponseActPlanner`. Alias-only acknowledgement detection
must use the supplied policy aliases; Core must not hard-code Aemeath names.

- [ ] **Step 4: Run the focused tests and verify precedence is green**

Run: `python3 -m pytest tests/test_response_act.py tests/test_reply_intent.py tests/test_scenes.py -q`

Expected: PASS. Each input produces exactly one primary act, and boundary evidence
cannot route to a capability.

- [ ] **Step 5: Commit the response-act layer**

```bash
git add groupmate/models.py groupmate/core/response_act.py groupmate/engine/planner.py tests/test_response_act.py tests/test_reply_intent.py
git commit -m "feat: add scene-driven response acts"
```

### Task 2: Capability Contracts And Bounded Registry

**Files:**
- Create: `groupmate/capabilities/__init__.py`
- Create: `groupmate/capabilities/contracts.py`
- Create: `groupmate/capabilities/registry.py`
- Create: `groupmate/capabilities/builtin.py`
- Modify: `groupmate/ports.py:35-51`
- Test: `tests/test_capability_registry.py`

- [ ] **Step 1: Write failing registry, timeout, and result-state tests**

```python
def test_registry_rejects_duplicate_names():
    registry = CapabilityRegistry()
    registry.register(
        StaticCapability("vision", CapabilityResult.success("vision", ("图里有花",)))
    )
    with pytest.raises(ValueError, match="duplicate capability"):
        registry.register(
            StaticCapability("vision", CapabilityResult.success("vision", ()))
        )


def test_unknown_capability_is_explicitly_unsupported():
    request = CapabilityRequest("r1", "g1", "查一下")
    result = asyncio.run(
        CapabilityRegistry().execute("missing", request)
    )
    assert result.status is CapabilityStatus.UNSUPPORTED


def test_timeout_is_terminal_and_does_not_leak_exception():
    registry = CapabilityRegistry(default_timeout_seconds=0.01)
    registry.register(BlockingCapability("slow"))
    request = CapabilityRequest("r2", "g1", "等一下")
    result = asyncio.run(registry.execute("slow", request))
    assert result.status is CapabilityStatus.TIMEOUT
    assert result.user_facts == ()
```

- [ ] **Step 2: Run the registry tests and verify import failure**

Run: `python3 -m pytest tests/test_capability_registry.py -q`

Expected: FAIL because `groupmate.capabilities` is missing.

- [ ] **Step 3: Implement immutable contracts and a static registry**

```python
class CapabilityStatus(StringEnum):
    SUCCESS = "success"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"
    TIMEOUT = "timeout"
    HANDOFF = "handoff"


@dataclass(frozen=True)
class MediaCandidate:
    media_id: str
    kind: str
    media_ref: str
    tags: Tuple[str, ...] = ()
    safe: bool = False
    source: str = ""


@dataclass(frozen=True)
class CapabilityRequest:
    request_id: str
    group_id: str
    text: str
    image_urls: Tuple[str, ...] = ()
    created_at: int = 0
    expires_at: int = 0


@dataclass(frozen=True)
class CapabilityResult:
    capability_name: str
    status: CapabilityStatus
    user_facts: Tuple[str, ...] = ()
    media: Tuple[MediaCandidate, ...] = ()
    error_code: str = ""
    handoff_owner: str = ""

    @classmethod
    def success(cls, name, facts, media=()):
        return cls(
            str(name), CapabilityStatus.SUCCESS,
            tuple(str(item) for item in facts), tuple(media),
        )


class CapabilityRegistry:
    async def execute(self, name, request):
        capability = self._items.get(str(name))
        if capability is None:
            return CapabilityResult(str(name), CapabilityStatus.UNSUPPORTED)
        try:
            return await asyncio.wait_for(
                capability.execute(request), self.default_timeout_seconds
            )
        except asyncio.TimeoutError:
            return CapabilityResult(str(name), CapabilityStatus.TIMEOUT, error_code="timeout")
        except Exception as exc:
            return CapabilityResult(
                str(name), CapabilityStatus.FAILED,
                error_code="execution_error:" + exc.__class__.__name__,
            )
```

Implement `VisionCapability` as an adapter around the existing `VisionPort` and
`ExternalKnowledgeHandoffCapability` as a non-sending result with
`status=HANDOFF` and `handoff_owner="astrbot_agent"`. Capability request context
must not contain `PlatformPort`, memory repositories, or social projectors.

- [ ] **Step 4: Verify timeout isolation and existing vision behavior**

Run: `python3 -m pytest tests/test_capability_registry.py tests/test_external_knowledge.py tests/test_workflow.py -q`

Expected: PASS. A timeout is returned as data and does not block another registry
execution.

- [ ] **Step 5: Commit the capability boundary**

```bash
git add groupmate/capabilities groupmate/ports.py tests/test_capability_registry.py
git commit -m "feat: add bounded capability registry"
```

### Task 3: Single-Owner AstrBot Handoff

**Files:**
- Modify: `groupmate/host/bridge.py:77-135`
- Modify: `main.py:28-53`
- Test: `tests/test_native_wake_suppress.py`
- Test: `tests/test_plugin_loading.py`

- [ ] **Step 1: Write failing ownership tests**

```python
def test_native_wake_has_exactly_one_owner(tmp_path):
    bridge = _bridge(tmp_path)
    assert bridge.owner_for_event(_FakeEvent(text="你今天怎样")) is TurnOwner.GROUPMATE
    assert bridge.owner_for_event(
        _FakeEvent(text="查一下今天发布的公告")
    ) is TurnOwner.ASTRBOT_AGENT


def test_agent_owned_turn_is_observed_without_groupmate_dispatch(tmp_path):
    event = _FakeEvent(text="搜索今天的新闻")
    bridge = _bridge(tmp_path)
    assert bridge.owner_for_event(event) is TurnOwner.ASTRBOT_AGENT
    assert event.call_llm is False
```

- [ ] **Step 2: Run the ownership tests and verify the API is missing**

Run: `python3 -m pytest tests/test_native_wake_suppress.py tests/test_plugin_loading.py -q`

Expected: FAIL because `owner_for_event` and `TurnOwner` do not exist.

- [ ] **Step 3: Replace paired booleans with an explicit owner decision**

```python
class TurnOwner(StringEnum):
    GROUPMATE = "groupmate"
    ASTRBOT_AGENT = "astrbot_agent"
    OBSERVE_ONLY = "observe_only"


def owner_for_event(self, event):
    if not self.should_take_native_wake(event):
        return TurnOwner.OBSERVE_ONLY
    message = self._message_from_event(event)
    if needs_external_knowledge(message.text):
        return TurnOwner.ASTRBOT_AGENT
    return TurnOwner.GROUPMATE
```

In `main.py`, compute the owner once. `GROUPMATE` sets `call_llm=True` and dispatches
Groupmate; `ASTRBOT_AGENT` only calls `observe_only` and leaves propagation enabled;
`OBSERVE_ONLY` follows the existing ambient path. Keep the old boolean methods as
thin compatibility wrappers until all current tests are migrated.

- [ ] **Step 4: Verify no native wake can take both final-response paths**

Run: `python3 -m pytest tests/test_native_wake_suppress.py tests/test_plugin_loading.py tests/test_astrbot_translation.py -q`

Expected: PASS. Each native wake has exactly one final-response owner.

- [ ] **Step 5: Commit host ownership routing**

```bash
git add main.py groupmate/host/bridge.py tests/test_native_wake_suppress.py tests/test_plugin_loading.py
git commit -m "feat: enforce single-owner task handoff"
```

### Task 4: Semantic Reaction Catalog And Media Policy

**Files:**
- Create: `groupmate/media/__init__.py`
- Create: `groupmate/media/reactions.py`
- Modify: `groupmate/models.py:504-526`
- Modify: `groupmate/config.py:21-168`
- Modify: `_conf_schema.json`
- Test: `tests/test_reaction_media.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write failing semantic, safety, and deduplication tests**

```python
def test_boundary_and_ambiguous_targets_never_get_decorative_media(tmp_path):
    policy = ReactionPolicy()
    assert not policy.allowed(ResponseAct.BOUNDARY, InteractionScene.SOCIAL_RESPONSE, False)
    assert not policy.allowed(ResponseAct.RECIPROCATE, InteractionScene.SOCIAL_RESPONSE, True)


def test_catalog_selects_by_tags_and_excludes_recent_ids(tmp_path):
    catalog = LocalReactionCatalog.from_items(
        tmp_path,
        [
            ReactionAsset("warm-1", "warm.png", ("warm",), True),
            ReactionAsset("play-1", "play.png", ("playful",), True),
        ],
    )
    selected = catalog.select(("warm",), recent_ids=("warm-1",))
    assert selected is None


def test_catalog_rejects_unsafe_or_outside_root_assets(tmp_path):
    catalog = LocalReactionCatalog.from_items(
        tmp_path, [ReactionAsset("bad", "../outside.png", ("warm",), True)]
    )
    assert catalog.select(("warm",), ()) is None
```

- [ ] **Step 2: Run the media tests and verify the package is missing**

Run: `python3 -m pytest tests/test_reaction_media.py tests/test_config.py -q`

Expected: FAIL because the reaction catalog contracts do not exist.

- [ ] **Step 3: Implement rule-based eligibility and deterministic selection**

```python
@dataclass(frozen=True)
class ReactionAsset:
    media_id: str
    relative_path: str
    tags: Tuple[str, ...]
    safe: bool


class ReactionPolicy:
    _ALLOWED = frozenset(
        {ResponseAct.RECIPROCATE, ResponseAct.PLAYFUL_REPLY, ResponseAct.VISUAL_REACTION}
    )

    def allowed(self, act, scene, ambiguous):
        if ambiguous or act not in self._ALLOWED:
            return False
        return scene in {
            InteractionScene.SOCIAL_RESPONSE,
            InteractionScene.DIRECT_ADDRESS,
            InteractionScene.REPLY_TO_BOT,
            InteractionScene.ACTIVE_CONTINUATION,
        }


def select(self, required_tags, recent_ids):
    recent = set(recent_ids)
    eligible = [
        item for item in self._items
        if item.safe and item.media_id not in recent
        and set(required_tags).issubset(set(item.tags))
        and self._inside_root(item.relative_path)
    ]
    asset = sorted(eligible, key=lambda item: item.media_id)[0] if eligible else None
    if asset is None:
        return None
    return MediaCandidate(
        media_id=asset.media_id,
        kind="image",
        media_ref=str((self.root / asset.relative_path).resolve()),
        tags=asset.tags,
        safe=True,
        source="local_reaction_catalog",
    )
```

Add `v3_composition_enabled`, `reaction_media_enabled`, and
`reaction_catalog_path` settings. Default composition to enabled, reaction media
to disabled when no catalog path is configured. Do not add target export assets.

- [ ] **Step 4: Verify forbidden scenes and text fallback**

Run: `python3 -m pytest tests/test_reaction_media.py tests/test_config.py -q`

Expected: PASS. Missing, unsafe, mismatched, and recently used assets return no
candidate without altering the response act.

- [ ] **Step 5: Commit the reaction policy**

```bash
git add groupmate/media groupmate/models.py groupmate/config.py _conf_schema.json tests/test_reaction_media.py tests/test_config.py
git commit -m "feat: add scene-conditional reaction media"
```

### Task 5: Structured Composer And Rich Outbox

**Files:**
- Create: `groupmate/engine/composer.py`
- Modify: `groupmate/models.py:441-500`
- Modify: `groupmate/engine/delivery.py:22-318`
- Modify: `groupmate/core/projections.py`
- Modify: `groupmate/memory/migrations.py`
- Modify: `groupmate/memory/store.py:830-960`
- Test: `tests/test_composer.py`
- Test: `tests/test_delivery_service.py`
- Test: `tests/test_phase2_projections.py`
- Test: `tests/test_phase6_migrations.py`

- [ ] **Step 1: Write failing composition and persistence tests**

```python
def test_composer_keeps_normal_dialogue_in_one_draft():
    draft = ResponseComposer().compose(
        text="谢谢你呀",
        act_plan=act_plan(ResponseAct.RECIPROCATE, tags=("warm",)),
        quote_message_id="m1",
        reaction=media("warm-1", "/assets/warm.png"),
    )
    assert [item.kind for item in draft.segments] == [
        OutboundKind.TEXT, OutboundKind.IMAGE
    ]
    assert draft.quote_message_id == "m1"


def test_task_media_is_kept_but_boundary_reaction_is_dropped():
    result = capability_result(media=(media("result", "https://example/result.png"),))
    task_draft = ResponseComposer().compose(
        text="结果在这里",
        act_plan=act_plan(ResponseAct.TASK_HANDOFF),
        quote_message_id="m2",
        capability_result=result,
    )
    boundary_draft = ResponseComposer().compose(
        text="不行。",
        act_plan=act_plan(ResponseAct.BOUNDARY),
        quote_message_id="m3",
        reaction=media("decorative", "/assets/no.png"),
    )
    assert [item.media_id for item in task_draft.segments if item.kind is OutboundKind.IMAGE] == ["result"]
    assert [item.kind for item in boundary_draft.segments] == [OutboundKind.TEXT]


def test_v9_database_migrates_with_structured_outbound_json(tmp_path):
    path = tmp_path / "legacy-v9.db"
    db = sqlite3.connect(str(path))
    with db:
        _bootstrap_v5(db)
        _v5_to_v6(db)
        _v6_to_v7(db)
        _v7_to_v8(db)
        _v8_to_v9(db)
        db.execute("UPDATE schema_meta SET value='9' WHERE key='version'")
    db.close()
    store = SQLiteMemoryStore(path)
    columns = {
        row[1] for row in store._db.execute("PRAGMA table_info(outbox)").fetchall()
    }
    assert store.schema_version() == 10
    assert "outbound_json" in columns
    store.close()


def test_projection_restores_recent_media_ids(delivered_media_store, policy):
    snapshot = StateProjector(delivered_media_store).rebuild(
        "g1", now=200, policy=policy
    )
    assert snapshot.recent_media_ids == ("warm-1",)
```

- [ ] **Step 2: Run focused tests and verify structured delivery is absent**

Run: `python3 -m pytest tests/test_composer.py tests/test_delivery_service.py tests/test_phase6_migrations.py -q`

Expected: FAIL because `ResponseComposer`, outbound segment contracts, and schema
version 10 are missing.

- [ ] **Step 3: Implement structured drafts with text-only compatibility**

```python
class OutboundKind(StringEnum):
    TEXT = "text"
    IMAGE = "image"


@dataclass(frozen=True)
class OutboundSegment:
    kind: OutboundKind
    text: str = ""
    media_id: str = ""
    media_ref: str = ""


@dataclass(frozen=True)
class ResponseDraft:
    segments: Tuple[OutboundSegment, ...]
    quote_message_id: Optional[str]
    response_act: ResponseAct
    capability_name: str = ""


@dataclass(frozen=True)
class DeliveryPlan:
    decision_id: str
    group_id: str
    segments: Tuple[str, ...]
    delay_seconds: float
    expires_at: int
    quote_message_id: Optional[str] = None
    outbound: Tuple[OutboundSegment, ...] = ()
```

`ResponseComposer` includes only safe capability media regardless of decorative
reaction eligibility, but decorative media only when the `ReactionPolicy` already
approved it. Migration v10 adds `outbound_json TEXT NOT NULL DEFAULT '[]'`. Store
the full ordered segment metadata in outbox and project confirmed Bot messages with
accurate `segment_types`, `image_urls`, and `metadata["media_ids"]`. Extend
`ProjectionSnapshot` with `recent_media_ids` rebuilt from confirmed Bot deliveries,
then hydrate the workflow's bounded recent-media deque. Existing callers that only
fill string `segments` continue unchanged.

- [ ] **Step 4: Verify text-only and structured outbox paths together**

Run: `python3 -m pytest tests/test_composer.py tests/test_delivery.py tests/test_delivery_service.py tests/test_phase2_projections.py tests/test_phase1_migrations.py tests/test_phase2_migrations.py tests/test_phase3_migrations.py tests/test_phase5_migrations.py tests/test_phase6_migrations.py -q`

Expected: PASS. Old delivery tests remain unchanged; rich deliveries persist ordered
metadata and still produce one terminal outbox row.

- [ ] **Step 5: Commit the composer and migration**

```bash
git add groupmate/engine/composer.py groupmate/models.py groupmate/engine/delivery.py groupmate/core/projections.py groupmate/memory/migrations.py groupmate/memory/store.py tests/test_composer.py tests/test_delivery_service.py tests/test_phase2_projections.py tests/test_phase6_migrations.py
git commit -m "feat: compose structured text and media replies"
```

### Task 6: AstrBot Rich-Media Platform Adapter

**Files:**
- Modify: `groupmate/ports.py:53-69`
- Modify: `groupmate/host/llm.py:100-180`
- Test: `tests/test_platform_port.py`

- [ ] **Step 1: Write failing ordered-chain tests**

```python
def test_platform_port_sends_quote_text_and_image_in_one_chain(monkeypatch):
    install_fake_astrbot_components(monkeypatch)
    port, context = platform_port()
    result = asyncio.run(
        port.send_outbound(
            "g1",
            (
                OutboundSegment(OutboundKind.TEXT, text="给你看"),
                OutboundSegment(OutboundKind.IMAGE, media_id="r1", media_ref="/tmp/r1.png"),
            ),
            "d1",
            quote_message_id="m1",
        )
    )
    chain = context.calls[0][2].chain
    assert [type(item).__name__ for item in chain] == ["FakeReply", "FakePlain", "FakeImage"]
    assert result.kind is SendReceiptKind.CONFIRMED
```

- [ ] **Step 2: Run the adapter tests and verify `send_outbound` is missing**

Run: `python3 -m pytest tests/test_platform_port.py -q`

Expected: FAIL because the rich platform method is absent.

- [ ] **Step 3: Build one ordered AstrBot MessageChain**

```python
async def send_outbound(self, group_id, segments, decision_id, quote_message_id=None):
    del decision_id
    from astrbot.api.event import MessageChain
    from astrbot.api.message_components import Image, Plain, Reply

    chain = MessageChain()
    if quote_message_id:
        chain.chain.append(Reply(id=str(quote_message_id)))
    for segment in segments:
        if segment.kind is OutboundKind.TEXT and segment.text:
            chain.chain.append(Plain(segment.text))
        elif segment.kind is OutboundKind.IMAGE:
            image = (
                Image.fromURL(segment.media_ref)
                if segment.media_ref.startswith(("http://", "https://"))
                else Image.fromFileSystem(segment.media_ref)
            )
            chain.chain.append(image)
    return await self._send_chain(group_id, chain)
```

Keep `send_text` and `send_segments` as compatibility wrappers. The implementation
must follow AstrBot's current official `MessageChain` order and
`Image.fromFileSystem` / `Image.fromURL` constructors.

- [ ] **Step 4: Verify rich and legacy adapters**

Run: `python3 -m pytest tests/test_platform_port.py tests/test_delivery_service.py -q`

Expected: PASS. Quote is first, text/media order is stable, and text-only tests stay
green.

- [ ] **Step 5: Commit the AstrBot media adapter**

```bash
git add groupmate/ports.py groupmate/host/llm.py tests/test_platform_port.py
git commit -m "feat: deliver ordered rich media chains"
```

### Task 7: Integrate Acts, Capabilities, Persona, And Composition

**Files:**
- Modify: `groupmate/core/context_assembly.py:33-310`
- Modify: `groupmate/engine/workflow.py:1-840`
- Modify: `groupmate/host/bridge.py:262-332`
- Modify: `groupmate/config.py:88-168`
- Modify: `groupmate/persona/aemeath/output_firewall.py:45-90`
- Test: `tests/test_core_assembly.py`
- Test: `tests/test_workflow.py`
- Test: `tests/test_runtime.py`

- [ ] **Step 1: Write failing end-to-end workflow tests**

```python
def test_supported_task_uses_facts_then_aemeath_generation(workflow_factory, topic):
    capability = StaticCapability(
        "vision", CapabilityResult.success("vision", ("图片里是一盆开花的植物",))
    )
    workflow = workflow_factory(capabilities=(capability,), model_output="开得很好看呀。")
    outcome = asyncio.run(workflow.evaluate(topic, TriggerKind.NATIVE_DIRECT, policy()))
    assert outcome.sent is True
    assert "图片里是一盆开花的植物" in workflow.generation_model.last_prompt
    assert "capability" not in outcome.text.lower()


def test_unsupported_task_never_claims_completion(workflow_factory, task_topic):
    workflow = workflow_factory(
        model_output="已经帮你查好了。",
        repair_output="这个我现在做不了，没法替你完成。",
    )
    outcome = asyncio.run(
        workflow.evaluate(task_topic, TriggerKind.ALIAS_DIRECT, policy())
    )
    assert outcome.sent is True
    assert "已经" not in outcome.text
    assert workflow.trace_has("ACT", "task_unsupported")


def test_failed_task_claim_is_rejected_after_failed_repair(output_firewall):
    result = output_firewall.validate(
        "已经弄好了。", (),
        reply_mode=ReplyMode.TASK_RESULT,
        response_act=ResponseAct.TASK_UNSUPPORTED,
        capability_status=CapabilityStatus.UNSUPPORTED,
    )
    assert "false_task_completion" in result.codes


def test_boundary_scene_never_emits_reaction_media(workflow_factory, boundary_topic):
    workflow = workflow_factory(
        model_output="不行，别拿别人的隐私开玩笑。",
        reactions=(warm_reaction(),),
    )
    asyncio.run(workflow.evaluate(boundary_topic, TriggerKind.ALIAS_DIRECT, policy()))
    assert workflow.platform.last_outbound_kinds == (OutboundKind.TEXT,)
```

- [ ] **Step 2: Run integration tests and verify the old workflow ignores acts**

Run: `python3 -m pytest tests/test_core_assembly.py tests/test_workflow.py tests/test_runtime.py -q`

Expected: FAIL because act/capability facts and structured drafts are not connected.

- [ ] **Step 3: Wire the pipeline behind the composition flag**

```python
act_plan = self.act_planner.plan(scene, topic.latest, targeting)
self._record(decision_id, topic.group_id, "ACT", act_plan.act.value, now)

capability_result = None
if act_plan.capability_name:
    capability_result = await self.capabilities.execute(
        act_plan.capability_name,
        CapabilityRequest(
            request_id=decision_id,
            group_id=topic.group_id,
            text=topic.latest.text,
            image_urls=topic.latest.image_urls,
            created_at=now,
            expires_at=now + policy.candidate_ttl_seconds,
        ),
    )
    self._record(
        decision_id, topic.group_id, "CAPABILITY", capability_result.status.value, now
    )

contribution = contribution_for(act_plan, capability_result)
reply_plan = ReplyPlan(
    decision_id=decision_id,
    group_id=topic.group_id,
    trigger=trigger,
    contribution=contribution,
    target_message_id=target_message_id,
    urgency=urgency,
    persona_prompt=persona_prompt,
    user_prompt=user_prompt,
    soft_trigger=soft_trigger,
    image_urls=(),
    reply_mode=act_plan.reply_mode,
    opportunity_id=opportunity.opportunity_id,
)
text = await self.generation_model.generate(
    reply_plan, topic, memories
)
draft = self.composer.compose(
    text=text,
    act_plan=act_plan,
    quote_message_id=quote_message_id,
    capability_result=capability_result,
    reaction=self._select_reaction(act_plan, scene, targeting),
)
```

Add a `<response_act>` block before `<reply_mode>` in `DYNAMIC_BLOCK_ORDER`. Include
only the act name, user-facing capability facts, and explicit no-false-completion
instruction. Do not expose capability error details, registry names, request IDs,
or host ownership. When `v3_composition_enabled=False`, execute the current
text-only path unchanged. Extend the Aemeath firewall with a
`false_task_completion` check: completion phrases are invalid when the act is
`TASK_UNSUPPORTED` or the capability status is not `SUCCESS`. Run the existing
single repair pass; if the repaired output still claims completion, return silence
rather than sending a fabricated result.

- [ ] **Step 4: Verify scheduling remains non-blocking and persona stays authoritative**

Run: `python3 -m pytest tests/test_core_assembly.py tests/test_workflow.py tests/test_runtime.py tests/test_persona.py tests/test_guardrails.py tests/test_native_wake_suppress.py tests/test_phase2_projections.py -q`

Expected: PASS. Capability timeout is a result, later hard turns still run, and the
final reply still passes the Aemeath firewall.

- [ ] **Step 5: Commit workflow integration**

```bash
git add groupmate/core/context_assembly.py groupmate/engine/workflow.py groupmate/host/bridge.py groupmate/config.py groupmate/persona/aemeath/output_firewall.py tests/test_core_assembly.py tests/test_workflow.py tests/test_runtime.py tests/test_phase2_projections.py
git commit -m "feat: integrate scene-driven behavior composition"
```

### Task 8: Scene-And-Act Conditional Evaluation

**Files:**
- Create: `eval/behavior_metrics.py`
- Create: `tests/test_behavior_metrics.py`
- Create: `eval/scenarios/phase2_behavior.jsonl`
- Modify: `eval/README.md`

- [ ] **Step 1: Write failing conditional metric tests**

```python
def test_metrics_report_scene_and_act_without_runtime_targets():
    report = aggregate_behavior_metrics(
        [
            obs("social_response", "reciprocate", media=True, allowed=True),
            obs("social_response", "boundary", media=False, allowed=False),
            obs("task_request", "task_unsupported", false_completion=True),
        ]
    )
    assert report["by_scene"]["social_response"]["replies"] == 2
    assert report["by_act"]["reciprocate"]["media_given_reply"] == 1.0
    assert report["violations"]["forbidden_media"] == 0
    assert report["violations"]["false_completion"] == 1
    assert "runtime_probability" not in report
```

- [ ] **Step 2: Run the metric tests and verify the module is missing**

Run: `python3 -m pytest tests/test_behavior_metrics.py -q`

Expected: FAIL because `eval.behavior_metrics` does not exist.

- [ ] **Step 3: Implement conditional aggregation and add labeled scenarios**

```python
@dataclass(frozen=True)
class BehaviorObservation:
    scene: str
    act: str
    replied: bool
    media: bool
    media_allowed: bool
    false_completion: bool
    duplicate_media: bool
    reply_chars: int
    latency_ms: int


def aggregate_behavior_metrics(observations):
    return {
        "by_scene": _group(observations, key=lambda item: item.scene),
        "by_act": _group(observations, key=lambda item: item.act),
        "violations": {
            "forbidden_media": sum(
                item.media and not item.media_allowed for item in observations
            ),
            "false_completion": sum(item.false_completion for item in observations),
            "duplicate_media": sum(item.duplicate_media for item in observations),
        },
    }
```

Create a separate `phase2_behavior.jsonl` corpus with labeled direct
acknowledgement, praise, gift, teasing, boundary, image reaction, supported vision,
missing task object, unsupported task, and external handoff cases. Keep
`baseline.jsonl` at 120 cases. Encode the new labels in the existing `tags` array,
for example `act:reciprocate`, `media:allowed`, and `capability:vision`, so the
version-1 scenario schema remains valid. Labels describe expected acts and allowed
output forms; they do not encode response/media probabilities.

- [ ] **Step 4: Verify evaluation and deterministic baseline**

Run: `python3 -m pytest tests/test_behavior_metrics.py tests/test_eval_schema.py tests/test_eval_runner.py -q`

Run: `python3 -m eval.runner --mode deterministic --enforce --scenarios eval/scenarios/phase2_behavior.jsonl --output /tmp/groupmate-phase2-behavior.json`

Expected: tests PASS; deterministic evaluation exits 0 with no schema or privacy
errors.

- [ ] **Step 5: Commit conditional evaluation**

```bash
git add eval/behavior_metrics.py eval/README.md eval/scenarios/phase2_behavior.jsonl tests/test_behavior_metrics.py
git commit -m "test: evaluate behavior by scene and response act"
```

### Task 9: Full Regression And Compatibility Verification

**Files:**
- Modify only files required by failures introduced in Tasks 1-8.

- [ ] **Step 1: Run the full Python test suite**

Run: `python3 -m pytest -q`

Expected: all tests pass with no pending-task warnings.

- [ ] **Step 2: Run the deterministic evaluation gate**

Run: `python3 -m eval.runner --mode deterministic --enforce --output /tmp/groupmate-phase2-final.json`

Expected: exit 0, zero runner errors, and all enforced checks pass.

- [ ] **Step 3: Verify Python 3.7 compatibility**

Run: `python3.7 -m compileall -q groupmate eval tests`

Expected: exit 0. Do not use `str | None`, `match`, `asyncio.to_thread`, or APIs
introduced after Python 3.7 in production modules.

- [ ] **Step 4: Inspect repository state and migration compatibility**

Run: `git diff --check`

Run: `git status --short`

Run: `git log --oneline --decorate -12`

Expected: no whitespace errors, only intended Phase 2 files changed, and each task
has its own commit.

- [ ] **Step 5: Review requirements against the design specification**

Confirm every requirement in
`docs/superpowers/specs/2026-07-27-scene-driven-behavior-composition-phase2.md`
has a test or an explicitly documented non-goal. Specifically verify zero false
task completion, zero decorative media in forbidden scenes, single-owner handoff,
one outbox terminal result, and unchanged Aemeath identity.

## Official AstrBot References

- Message sending guide: <https://github.com/AstrBotDevs/AstrBot/wiki/zh-dev-star-guides-send-message>
- Message components: <https://raw.githubusercontent.com/AstrBotDevs/AstrBot/master/astrbot/core/message/components.py>
- MessageChain and event propagation: <https://github.com/AstrBotDevs/AstrBot/blob/master/astrbot/core/message/message_event_result.py>
