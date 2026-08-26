# Groupmate 群成员画像与社交关系图 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 Groupmate 增加持续、独立、可纠正的群成员画像，建立成员之间的有证据关系网络，并将相关画像安全地接入参与判断、回复表达、群聊查询和管理页面。

**Architecture:** 在 `social_runtime/profile` 新建独立领域，以 SQLite v2 迁移提供身份、别称、事实、经历、关系边、快照和后台观察队列；模型只在后台提出结构化候选，本地策略决定是否确认。实时消息仅做确定性身份记录和轻量入队，回复时通过有界检索读取已有快照，因此模型失败不会阻塞主链路。

**Tech Stack:** Python 3.10+、dataclasses、asyncio、SQLite、现有 DeepSeek JSON HTTP transport、pytest、原生 ES modules/CSS。

---

## 文件职责

- 新建 `groupmate/social_runtime/profile/contracts.py`：身份、别称、事实、经历、关系边、快照和候选契约。
- 新建 `groupmate/social_runtime/profile/repository.py`：只提供按 `persona_id × group_id` 作用域访问的 SQLite 仓库。
- 新建 `groupmate/social_runtime/profile/identity.py`：稳定 ID、昵称历史和别称合并规则。
- 新建 `groupmate/social_runtime/profile/policy.py`：候选可信度、冲突、过时、第三方说法和敏感关系策略。
- 新建 `groupmate/social_runtime/profile/extractor.py`：后台批次、严格模型输出解析和重试调度。
- 新建 `groupmate/social_runtime/profile/snapshot.py`：画像成熟度、个体化校验和紧凑快照生成。
- 新建 `groupmate/social_runtime/profile/graph.py`：群友关系边投影与文字摘要。
- 新建 `groupmate/social_runtime/profile/group_portrait.py`：从成员快照和群事件生成有界群体画像。
- 新建 `groupmate/social_runtime/profile/retrieval.py`：回复和参与判断使用的有界画像上下文。
- 新建 `groupmate/social_runtime/profile/service.py`：组合仓库、规则、后台任务和查询接口。
- 新建 `groupmate/adapters/deepseek_profile.py`：复用现有 HTTP transport 的后台画像模型边界。
- 新建 `groupmate/adapters/profile_query.py`：精确匹配自查、纠错、删除和停用命令。
- 修改 `groupmate/social_runtime/persistence/schema.py`：从 v1 原地升级到 v2，建立画像表与索引。
- 修改 `groupmate/adapters/participants.py`：同步稳定身份和昵称历史，不改变现有私有展示引用。
- 修改 `groupmate/adapters/astrbot_bridge.py`：消息入队、后台服务生命周期、画像检索和查询准备。
- 修改 `groupmate/social_runtime/manager.py`：只接收已裁剪的画像检索结果。
- 修改 `groupmate/social_runtime/cognition/ambient_worker.py`：将最小相关画像信号加入现有一次 AMBIENT 请求。
- 修改 `groupmate/social_runtime/actions/generation.py`：将相关画像事实和经历加入最终回复提示。
- 修改 `main.py`：画像命令确定性接管并停止 AstrBot 后续传播。
- 修改 `groupmate/social_runtime/control/queries.py` 与 `groupmate/adapters/web_api.py`：提供管理员画像只读端点。
- 新建 `pages/settings/workspaces/profiles.js`：成员画像、经历和关系列表页面。
- 修改 `pages/settings/router.js`、`pages/settings/app.js`、`pages/settings/index.html` 与样式：增加群成员画像入口。

### Task 1: SQLite v2 画像数据底座

**Files:**
- Modify: `groupmate/social_runtime/persistence/schema.py`
- Create: `groupmate/social_runtime/profile/__init__.py`
- Create: `groupmate/social_runtime/profile/contracts.py`
- Create: `groupmate/social_runtime/profile/repository.py`
- Test: `tests/social_runtime/profile/test_profile_schema.py`
- Test: `tests/social_runtime/profile/test_profile_repository.py`

- [ ] **Step 1: Write failing migration and scope tests**

```python
def test_v1_database_upgrades_in_place_without_losing_runtime_rows(tmp_path):
    path = tmp_path / "runtime.db"
    _create_v1_database(path)
    with connect_database(path) as db:
        db.execute("INSERT INTO culture VALUES(?,?,?,?,?,?,?)", _culture_row())
    initialize_database(path)
    with connect_database(path) as db:
        assert db.execute("SELECT version FROM social_runtime_schema").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM culture").fetchone()[0] == 1
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='profile_facts'").fetchone()


def test_profile_repository_never_crosses_group_scope(tmp_path):
    repo = ProfileRepository(tmp_path / "runtime.db")
    repo.upsert_identity(MemberIdentity("p", "qq", "u1", "甲", 10))
    repo.put_fact(_fact(group_id="g1", subject_id="u1", summary="喜欢冷饮"))
    assert len(repo.facts("p", "g1", "u1")) == 1
    assert repo.facts("p", "g2", "u1") == ()
```

- [ ] **Step 2: Run tests and confirm red**

Run: `.venv/bin/pytest -q tests/social_runtime/profile/test_profile_schema.py tests/social_runtime/profile/test_profile_repository.py`

Expected: FAIL because schema v2 and profile repository do not exist.

- [ ] **Step 3: Add frozen contracts and an idempotent v1 → v2 migration**

Define these frozen dataclasses with bounded normalization in `contracts.py`:

```python
@dataclass(frozen=True)
class MemberIdentity:
    persona_id: str
    platform: str
    actor_id: str
    display_name: str
    updated_at: int
    avatar_ref: str | None = None
    system_roles: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProfileFact:
    fact_id: str
    persona_id: str
    group_id: str
    subject_id: str
    category: str
    summary: str
    source_kind: str
    source_actor_id: str
    source_event_ids: tuple[str, ...]
    confidence: float
    status: str
    evidence_count: int
    valid_from: int
    valid_until: int | None = None
    supersedes_fact_id: str | None = None
    injectable: bool = False


@dataclass(frozen=True)
class ProfileEpisode:
    episode_id: str
    persona_id: str
    group_id: str
    title: str
    summary: str
    participants: tuple[str, ...]
    source_event_ids: tuple[str, ...]
    episode_type: str
    valence: float
    importance: float
    confidence: float
    status: str
    occurred_at: int
    last_reinforced_at: int


@dataclass(frozen=True)
class SocialEdge:
    edge_id: str
    persona_id: str
    group_id: str
    source_member_id: str
    target_member_id: str
    relation_type: str
    direction: str
    strength: float
    confidence: float
    source_event_ids: tuple[str, ...]
    status: str
    valid_from: int
    valid_until: int | None
    last_observed_at: int


@dataclass(frozen=True)
class ProfileSnapshot:
    persona_id: str
    group_id: str
    subject_id: str
    one_line_portrait: str
    group_roles: tuple[str, ...]
    individual_fingerprints: tuple[str, ...]
    preferences_and_boundaries: tuple[str, ...]
    representative_episode_ids: tuple[str, ...]
    relationship_summary: str
    maturity: str
    source_revision: int
    generated_at: int
```

Set `SCHEMA_VERSION = 2`. When an owned v1 database is opened, execute a single transaction that creates `member_identities`, `member_aliases`, `profile_observations`, `profile_facts`, `profile_episodes`, `social_edges`, `profile_snapshots`, `group_portraits`, `profile_preferences`, `profile_audit`, and their scope/status indexes, then updates the schema version. A v2 database remains a no-op; foreign databases remain rejected.

Implement repository methods with explicit scope parameters:

```python
upsert_identity(identity: MemberIdentity) -> MemberIdentity
remember_alias(alias: MemberAlias) -> MemberAlias
enqueue_observation(observation: ProfileObservation) -> bool
claim_observations(persona_id: str, group_id: str, *, limit: int, now: int) -> tuple[ProfileObservation, ...]
complete_observations(event_ids: tuple[str, ...], *, status: str, diagnostic_code: str | None) -> None
put_fact(fact: ProfileFact) -> ProfileFact
facts(persona_id: str, group_id: str, subject_id: str, *, injectable_only: bool = False) -> tuple[ProfileFact, ...]
put_episode(episode: ProfileEpisode) -> ProfileEpisode
put_edge(edge: SocialEdge) -> SocialEdge
put_snapshot(snapshot: ProfileSnapshot) -> ProfileSnapshot
snapshot(persona_id: str, group_id: str, subject_id: str) -> ProfileSnapshot | None
```

- [ ] **Step 4: Run focused tests**

Run: `.venv/bin/pytest -q tests/social_runtime/profile/test_profile_schema.py tests/social_runtime/profile/test_profile_repository.py tests/social_runtime/test_event_store.py`

Expected: PASS; existing v1 runtime rows survive migration.

- [ ] **Step 5: Commit**

```bash
git add groupmate/social_runtime/persistence/schema.py groupmate/social_runtime/profile tests/social_runtime/profile
git commit -m "feat: add profile persistence foundation"
```

### Task 2: Stable identity and alias history

**Files:**
- Create: `groupmate/social_runtime/profile/identity.py`
- Modify: `groupmate/adapters/participants.py`
- Modify: `groupmate/adapters/astrbot_bridge.py`
- Test: `tests/social_runtime/profile/test_profile_identity.py`
- Test: `tests/contracts/test_participants.py`

- [ ] **Step 1: Write failing identity tests**

```python
def test_nickname_change_updates_one_identity_and_keeps_alias_history(tmp_path):
    service = _identity_service(tmp_path)
    first = service.observe(_event(actor_id="u151", name="玲151"))
    second = service.observe(_event(actor_id="u151", name="玲151🍅（已离线）"))
    assert first.actor_id == second.actor_id == "u151"
    assert service.aliases("p", "g", "u151") == ("玲151", "玲151🍅（已离线）")


def test_similar_aliases_never_merge_different_actor_ids(tmp_path):
    service = _identity_service(tmp_path)
    service.observe(_event(actor_id="u-ling", name="玲151"))
    service.observe(_event(actor_id="u-sai", name="小赛151"))
    assert service.resolve("p", "qq", "u-ling").actor_id == "u-ling"
    assert service.resolve("p", "qq", "u-sai").actor_id == "u-sai"
```

- [ ] **Step 2: Run tests and confirm red**

Run: `.venv/bin/pytest -q tests/social_runtime/profile/test_profile_identity.py tests/contracts/test_participants.py`

Expected: FAIL because participant presentation does not keep alias history.

- [ ] **Step 3: Implement deterministic identity observation**

`IdentityService.observe(event)` uses `(persona_id, platform, actor_id)` as identity and `(persona_id, group_id, actor_id, alias)` as alias history. It accepts only the display name carried by the platform sender payload or an explicit administrator correction. It must not merge by substring, edit distance, shared digits, avatar or model suggestion.

`ParticipantDirectory.remember()` continues returning the existing opaque `member_ref` and `avatar_ref`, then delegates the private ID and normalized name to `IdentityService`. `AstrBotSocialRuntimeBridge` calls this once per translated group event before profile observation enqueueing.

- [ ] **Step 4: Run focused tests**

Run: `.venv/bin/pytest -q tests/social_runtime/profile/test_profile_identity.py tests/contracts/test_participants.py tests/contracts/test_message_trace_bridge.py`

Expected: PASS with unchanged page-safe participant references.

- [ ] **Step 5: Commit**

```bash
git add groupmate/social_runtime/profile/identity.py groupmate/adapters/participants.py groupmate/adapters/astrbot_bridge.py tests/social_runtime/profile/test_profile_identity.py tests/contracts/test_participants.py
git commit -m "feat: preserve stable member identity and aliases"
```

### Task 3: Evidence policy, correction and temporal validity

**Files:**
- Create: `groupmate/social_runtime/profile/policy.py`
- Test: `tests/social_runtime/profile/test_profile_policy.py`
- Test: `tests/shared/test_group_scope_privacy.py`

- [ ] **Step 1: Write failing policy tests**

```python
def test_self_statement_can_confirm_but_third_party_claim_stays_pending():
    policy = ProfileEvidencePolicy()
    own = policy.decide(_candidate(source_kind="self_statement", source_actor_id="u1", subject_id="u1", confidence=.91))
    hearsay = policy.decide(_candidate(source_kind="third_party_claim", source_actor_id="u2", subject_id="u1", confidence=.99))
    assert (own.status, own.injectable) == ("confirmed", True)
    assert (hearsay.status, hearsay.injectable) == ("proposed", False)


def test_admin_correction_supersedes_old_fact_immediately():
    result = ProfileEvidencePolicy().correct(
        old=_fact(fact_id="old", status="confirmed", injectable=True),
        replacement=_candidate(source_kind="admin_correction", summary="现在不喝冷饮"),
    )
    assert result.old.status == "superseded"
    assert result.new.supersedes_fact_id == "old"
    assert result.new.injectable is True


def test_sensitive_relationship_candidate_is_rejected():
    result = ProfileEvidencePolicy().decide_edge(_edge_candidate(relation_type="romantic"))
    assert result.status == "rejected"
```

- [ ] **Step 2: Run tests and confirm red**

Run: `.venv/bin/pytest -q tests/social_runtime/profile/test_profile_policy.py tests/shared/test_group_scope_privacy.py`

Expected: FAIL because no profile evidence policy exists.

- [ ] **Step 3: Implement local validation**

Allow fact categories from the spec and relation types `frequent_interaction`, `familiar`, `supportive`, `technical_peer`, `teasing`, `conflict`, `avoidance`, `custom`. Apply these rules:

```python
SYSTEM_OR_ADMIN = {"system", "admin_correction"}
SELF_CONFIRM_THRESHOLD = 0.86
PATTERN_CONFIRM_THRESHOLD = 0.88
PATTERN_MIN_EVIDENCE = 3

if source_kind in SYSTEM_OR_ADMIN:
    return confirmed(injectable=True)
if source_kind == "self_statement" and source_actor_id == subject_id and confidence >= SELF_CONFIRM_THRESHOLD:
    return confirmed(injectable=True)
if source_kind == "observed_pattern" and evidence_count >= PATTERN_MIN_EVIDENCE and confidence >= PATTERN_CONFIRM_THRESHOLD:
    return confirmed(injectable=True)
if source_kind == "third_party_claim":
    return proposed(injectable=False)
return proposed(injectable=False)
```

Reject sensitive categories, empty scope, evidence IDs not present in the processed batch, summaries over 160 characters, non-finite confidence and aliases that attempt identity merge. Implement `correct`, `delete`, `mark_stale` and audit entries in the same transaction as the affected fact.

- [ ] **Step 4: Run focused tests**

Run: `.venv/bin/pytest -q tests/social_runtime/profile/test_profile_policy.py tests/shared/test_group_scope_privacy.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add groupmate/social_runtime/profile/policy.py tests/social_runtime/profile/test_profile_policy.py tests/shared/test_group_scope_privacy.py
git commit -m "feat: validate evolving profile evidence"
```

### Task 4: Background profile extraction without blocking replies

**Files:**
- Create: `groupmate/adapters/deepseek_profile.py`
- Create: `groupmate/social_runtime/profile/extractor.py`
- Create: `groupmate/social_runtime/profile/service.py`
- Modify: `groupmate/settings.py`
- Modify: `_conf_schema.json`
- Modify: `groupmate/adapters/astrbot_bridge.py`
- Test: `tests/contracts/test_deepseek_profile.py`
- Test: `tests/social_runtime/profile/test_profile_extractor.py`
- Test: `tests/scenarios/test_profile_background_pipeline.py`

- [ ] **Step 1: Write failing model-boundary and non-blocking tests**

```python
def test_profile_model_request_uses_separate_prompt_and_bounded_batch():
    client = DeepSeekProfileClient(api_key="sk", api_base="https://api.deepseek.com", model="m", transport=_CaptureTransport())
    payload = client.request_payload(_batch(20))
    assert "群成员画像候选提取器" in payload["messages"][0]["content"]
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["temperature"] == 0.1


def test_slow_profile_extractor_never_delays_message_ingest(tmp_path):
    service = _service(tmp_path, extractor=_BlockingExtractor())
    started = time.monotonic()
    asyncio.run(service.observe(_event("e1")))
    assert time.monotonic() - started < 0.1
    assert service.pending_count("p", "g") == 1


def test_invalid_model_output_records_safe_code_and_no_fact(tmp_path):
    service = _service(tmp_path, extractor=_Extractor({"facts": [{"category": "invented"}]}))
    asyncio.run(service.process_due(now=100))
    assert service.facts("p", "g", "u1") == ()
    assert service.diagnostics("p", "g")[0].code == "profile_output_invalid"
```

- [ ] **Step 2: Run tests and confirm red**

Run: `.venv/bin/pytest -q tests/contracts/test_deepseek_profile.py tests/social_runtime/profile/test_profile_extractor.py tests/scenarios/test_profile_background_pipeline.py`

Expected: FAIL because the profile model boundary and background service do not exist.

- [ ] **Step 3: Implement the independent background worker**

Add settings:

```python
profile_enabled: bool = True
profile_batch_messages: int = 20
profile_batch_interval_seconds: int = 600
profile_timeout_seconds: int = 30
```

`DeepSeekProfileClient.extract(batch)` reuses `AioHttpJsonTransport` but has its own system prompt, `max_tokens=1600`, JSON-object response and 30-second background timeout. The schema accepts arrays `facts`, `episodes`, and `edges`; every item must copy subject and evidence IDs from input.

`ProfileService.observe(event)` performs deterministic identity update and one SQLite insert only. `start()` launches a cancellable scheduler. `process_due()` claims at most 20 observations, calls the model outside the database transaction, validates candidates locally, commits valid decisions, and marks the batch `completed`, `retry` or `discarded`. Exponential retry delays are 60, 300 and 1800 seconds; raw exception strings are never persisted.

The bridge starts and closes the service with its existing lifecycle. The background task is not awaited from `handle_event()` after the durable observation insert.

- [ ] **Step 4: Run focused tests**

Run: `.venv/bin/pytest -q tests/contracts/test_deepseek_profile.py tests/social_runtime/profile/test_profile_extractor.py tests/scenarios/test_profile_background_pipeline.py tests/scenarios/test_chat_mainline.py`

Expected: PASS; existing chat latency and one-call AMBIENT assertions remain unchanged.

- [ ] **Step 5: Commit**

```bash
git add groupmate/adapters/deepseek_profile.py groupmate/social_runtime/profile/extractor.py groupmate/social_runtime/profile/service.py groupmate/settings.py _conf_schema.json groupmate/adapters/astrbot_bridge.py tests/contracts/test_deepseek_profile.py tests/social_runtime/profile/test_profile_extractor.py tests/scenarios/test_profile_background_pipeline.py
git commit -m "feat: learn member profiles in background"
```

### Task 5: Individualized snapshots and member social graph

**Files:**
- Create: `groupmate/social_runtime/profile/snapshot.py`
- Create: `groupmate/social_runtime/profile/graph.py`
- Create: `groupmate/social_runtime/profile/group_portrait.py`
- Test: `tests/social_runtime/profile/test_profile_snapshot.py`
- Test: `tests/social_runtime/profile/test_social_graph.py`
- Test: `tests/social_runtime/profile/test_group_portrait.py`

- [ ] **Step 1: Write failing snapshot and graph tests**

```python
def test_snapshot_refuses_generic_portrait_without_discriminative_evidence():
    result = SnapshotBuilder().build(_member(), facts=(_fact(summary="友善活跃", evidence_count=1),), episodes=(), edges=())
    assert result.maturity == "new"
    assert result.one_line_portrait == "正在形成画像"


def test_snapshot_keeps_specific_behavior_and_representative_episode():
    result = SnapshotBuilder().build(
        _member(),
        facts=(
            _fact(category="behavior_pattern", summary="会持续追问到问题真正落地", evidence_count=4),
            _fact(category="boundary", summary="不接受只有技术完成但用户看不懂的结果", evidence_count=2),
        ),
        episodes=(_episode(summary="连续指出好感度卡片的字体与错行问题"),),
        edges=(),
    )
    assert "持续追问" in result.one_line_portrait
    assert result.representative_episode_ids


def test_graph_keeps_ling151_and_xiaosai151_as_separate_nodes():
    graph = SocialGraph((_identity("u-ling", "玲151"), _identity("u-sai", "小赛151")), ())
    assert {node.actor_id for node in graph.nodes} == {"u-ling", "u-sai"}


def test_group_portrait_uses_aggregate_roles_without_leaking_private_facts():
    result = GroupPortraitBuilder().build(
        group_id="g",
        member_snapshots=(_snapshot(role="技术解答者", private_fact="不公开的旧争执"),),
        culture=("重视问题落地",),
        topic_counts={"插件开发": 8},
    )
    assert result.role_counts == {"技术解答者": 1}
    assert "插件开发" in result.common_topics
    assert "旧争执" not in result.summary
```

- [ ] **Step 2: Run tests and confirm red**

Run: `.venv/bin/pytest -q tests/social_runtime/profile/test_profile_snapshot.py tests/social_runtime/profile/test_social_graph.py tests/social_runtime/profile/test_group_portrait.py`

Expected: FAIL because snapshot and graph builders do not exist.

- [ ] **Step 3: Implement deterministic selection and bounded summaries**

`SnapshotBuilder` ranks only confirmed injectable facts by category diversity, confidence, evidence count and recency. It selects at most six fingerprints, four preferences/boundaries and three episodes. Maturity is `new` with fewer than two distinct confirmed categories, `forming` with two to four, and `stable` with at least five plus one repeated behavior or representative episode.

The one-line portrait must contain at least one specific action, preference, boundary or episode phrase. Reject portraits composed only of generic adjectives from a fixed stop set such as `友善、活跃、认真、热情、善良、开朗`. Data-insufficient snapshots use `正在形成画像`.

`SocialGraph` returns scoped nodes, validated edges and a textual relation list. It never resolves nodes by nickname and never emits rejected or expired edges.

`GroupPortraitBuilder` aggregates only non-sensitive member roles, confirmed culture artifacts, topic frequencies, activity rhythm and validated edge counts. It emits one group positioning sentence, up to six common topics, an activity rhythm, role counts and recent group milestones; it never concatenates members' private facts into a public group summary.

- [ ] **Step 4: Run focused tests**

Run: `.venv/bin/pytest -q tests/social_runtime/profile/test_profile_snapshot.py tests/social_runtime/profile/test_social_graph.py tests/social_runtime/profile/test_group_portrait.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add groupmate/social_runtime/profile/snapshot.py groupmate/social_runtime/profile/graph.py groupmate/social_runtime/profile/group_portrait.py tests/social_runtime/profile/test_profile_snapshot.py tests/social_runtime/profile/test_social_graph.py tests/social_runtime/profile/test_group_portrait.py
git commit -m "feat: build individualized profiles and social graph"
```

### Task 6: Bounded profile retrieval for participation and replies

**Files:**
- Create: `groupmate/social_runtime/profile/retrieval.py`
- Modify: `groupmate/social_runtime/manager.py`
- Modify: `groupmate/social_runtime/cognition/ambient_worker.py`
- Modify: `groupmate/social_runtime/actions/generation.py`
- Test: `tests/social_runtime/profile/test_profile_retrieval.py`
- Test: `tests/contracts/test_direct_ambient_worker.py`
- Test: `tests/social_runtime/actions/test_replying.py`

- [ ] **Step 1: Write failing relevance and budget tests**

```python
def test_retrieval_returns_current_actor_and_at_most_two_referenced_members():
    result = _retriever().for_message(_message(actor="u1", mentions=("u2", "u3", "u4")), max_chars=1200)
    assert [item.subject_id for item in result.members] == ["u1", "u2", "u3"]


def test_unrelated_private_fact_is_not_injected():
    result = _retriever(facts=(_fact(subject="u1", summary="喜欢冷饮"), _fact(subject="u2", summary="不公开的旧争执"))).for_message(_message(actor="u1"), max_chars=1200)
    assert "喜欢冷饮" in result.prompt_text
    assert "旧争执" not in result.prompt_text


def test_profile_context_obeys_hard_character_budget():
    result = _large_retriever().for_message(_message(actor="u1"), max_chars=800)
    assert len(result.prompt_text) <= 800
```

- [ ] **Step 2: Run tests and confirm red**

Run: `.venv/bin/pytest -q tests/social_runtime/profile/test_profile_retrieval.py tests/contracts/test_direct_ambient_worker.py tests/social_runtime/actions/test_replying.py`

Expected: FAIL because no profile retriever is wired.

- [ ] **Step 3: Implement relevance retrieval and prompt sections**

`ProfileRetriever.for_message()` selects the current actor, then at most two explicitly mentioned/replied members. It returns at most three facts, two episodes and necessary relation edges, sorted by explicit reference, topic overlap, confidence and recency. Only `confirmed + injectable` facts can enter prompts.

Add a compact `member_context` mapping to the manager's world summary. `DirectAmbientWorker` receives only alias/addressing habits and relevant relationship edges needed for interruption judgment. Final generation receives the portrait, relevant facts, episodes and current Bot relationship under an instruction that the model must not recite labels mechanically.

- [ ] **Step 4: Run focused tests**

Run: `.venv/bin/pytest -q tests/social_runtime/profile/test_profile_retrieval.py tests/contracts/test_direct_ambient_worker.py tests/social_runtime/actions/test_replying.py tests/scenarios/test_chat_mainline.py`

Expected: PASS and profile prompt text remains within its hard budget.

- [ ] **Step 5: Commit**

```bash
git add groupmate/social_runtime/profile/retrieval.py groupmate/social_runtime/manager.py groupmate/social_runtime/cognition/ambient_worker.py groupmate/social_runtime/actions/generation.py tests/social_runtime/profile/test_profile_retrieval.py tests/contracts/test_direct_ambient_worker.py tests/social_runtime/actions/test_replying.py tests/scenarios/test_chat_mainline.py
git commit -m "feat: retrieve relevant member cognition for replies"
```

### Task 7: Self-service profile commands without duplicate replies

**Files:**
- Create: `groupmate/adapters/profile_query.py`
- Modify: `groupmate/adapters/astrbot_bridge.py`
- Modify: `main.py`
- Test: `tests/contracts/test_profile_query.py`
- Test: `tests/shared/test_plugin_skeleton.py`
- Test: `tests/scenarios/test_profile_query_flow.py`

- [ ] **Step 1: Write failing command ownership tests**

```python
@pytest.mark.parametrize("text", ("查看我的画像", "我的画像"))
def test_profile_query_is_exact_and_local(text):
    assert parse_profile_command(text).kind == "show_self"


def test_natural_chat_does_not_claim_profile_command():
    assert parse_profile_command("我觉得我的画像应该更具体") is None


def test_query_stops_astrbot_and_returns_one_response(tmp_path):
    event = _event("查看我的画像", actor_id="u1")
    results = asyncio.run(_collect_plugin_results(tmp_path, event))
    assert event.stop_calls == 1
    assert len(results) == 1
    assert "我的画像" in results[0]
```

- [ ] **Step 2: Run tests and confirm red**

Run: `.venv/bin/pytest -q tests/contracts/test_profile_query.py tests/scenarios/test_profile_query_flow.py tests/shared/test_plugin_skeleton.py`

Expected: FAIL because profile commands do not exist.

- [ ] **Step 3: Implement deterministic local commands**

Support exact normalized commands:

```text
查看我的画像
我的画像
纠正画像 <事实编号> <新内容>
删除画像 <事实编号>
停止画像个性化
恢复画像个性化
```

`prepare_profile_command(event)` translates once, verifies enabled group and current actor, reads or mutates only that actor's scoped profile, and returns a bounded text result. It never calls DeepSeek or the reply generation Provider. `main.py` checks profile commands before affection queries, calls `event.stop_event()` once, yields exactly one result and returns.

- [ ] **Step 4: Run focused tests**

Run: `.venv/bin/pytest -q tests/contracts/test_profile_query.py tests/scenarios/test_profile_query_flow.py tests/shared/test_plugin_skeleton.py tests/contracts/test_affection_query.py`

Expected: PASS; affection query ownership remains unchanged.

- [ ] **Step 5: Commit**

```bash
git add groupmate/adapters/profile_query.py groupmate/adapters/astrbot_bridge.py main.py tests/contracts/test_profile_query.py tests/scenarios/test_profile_query_flow.py tests/shared/test_plugin_skeleton.py
git commit -m "feat: add private member profile commands"
```

### Task 8: Admin profile API and focused management workspace

**Files:**
- Modify: `groupmate/social_runtime/control/queries.py`
- Modify: `groupmate/social_runtime/control/commands.py`
- Modify: `groupmate/adapters/web_api.py`
- Modify: `main.py`
- Create: `pages/settings/workspaces/profiles.js`
- Modify: `pages/settings/router.js`
- Modify: `pages/settings/app.js`
- Modify: `pages/settings/index.html`
- Modify: `pages/settings/styles/components.css`
- Modify: `pages/settings/styles/layout.css`
- Test: `tests/contracts/test_profile_web_api.py`
- Test: `tests/contracts/test_commands.py`
- Test: `tests/page/test_profile_workspace.py`
- Test: `tests/page/test_router_contract.py`

- [ ] **Step 1: Write failing API and UI contract tests**

```python
def test_profile_endpoint_is_admin_only_and_group_scoped(tmp_path):
    api = _api_with_profiles(tmp_path)
    allowed = asyncio.run(api.handle(_request("/profiles", username="admin:root")))
    denied = asyncio.run(api.handle(_request("/profiles", username="member:1")))
    assert allowed.status == 200
    assert allowed.body["scope"] == {"persona_id": "p", "group_id": "g1"}
    assert denied.status == 403


def test_profile_workspace_leads_with_member_identity_not_model_diagnostics():
    source = (PAGE / "workspaces" / "profiles.js").read_text(encoding="utf-8")
    assert "一句话画像" in source
    assert "代表经历" in source
    assert "群友关系" in source
    assert source.index("一句话画像") < source.index("证据与审计")
```

- [ ] **Step 2: Run tests and confirm red**

Run: `.venv/bin/pytest -q tests/contracts/test_profile_web_api.py tests/contracts/test_commands.py tests/page/test_profile_workspace.py tests/page/test_router_contract.py`

Expected: FAIL because the endpoint and workspace do not exist.

- [ ] **Step 3: Implement the scoped profile projection**

Add `ProjectionQueries.profiles(persona_id, group_id)` returning member summaries, `ProjectionQueries.profile_detail(persona_id, group_id, member_ref)` returning only that scope's facts, episodes, Bot relationship, social edges and audit summaries, and `ProjectionQueries.group_portrait(persona_id, group_id)` returning the safe aggregate. Expose `/profiles`, `/profile`, and `/group-portrait` through the existing administrator gate; raw platform IDs and raw model output stay private.

Add administrator command payloads `profile_fact_correct`, `profile_fact_invalidate`, `profile_identity_merge`, and `profile_identity_split`. Every command requires expected profile revision, writes `profile_audit`, and never mutates another group. Identity merge moves aliases and profile references only after verifying both identities belong to the same platform account or the administrator explicitly supplies the stable target ID; similar nicknames alone remain insufficient.

Add `/profiles` to `ROUTES` and a navigation item. The workspace has a searchable member list and detail panel ordered as: one-line portrait, group roles/fingerprints, preferences/boundaries, Bot relationship, representative episodes, member relationships, recent changes, collapsed evidence/audit. Network edges also render as readable relation rows; a graph view may supplement but not replace them.

- [ ] **Step 4: Run focused tests**

Run: `.venv/bin/pytest -q tests/contracts/test_profile_web_api.py tests/contracts/test_web_api.py tests/contracts/test_commands.py tests/page/test_profile_workspace.py tests/page/test_router_contract.py tests/page/test_product_ui.py`

Expected: PASS; runtime workspace remains available and the new route is administrator-only.

- [ ] **Step 5: Commit**

```bash
git add groupmate/social_runtime/control/queries.py groupmate/social_runtime/control/commands.py groupmate/adapters/web_api.py main.py pages/settings tests/contracts/test_profile_web_api.py tests/contracts/test_commands.py tests/page/test_profile_workspace.py tests/page/test_router_contract.py tests/page/test_product_ui.py
git commit -m "feat: add member profile management workspace"
```

### Task 9: End-to-end privacy, degradation and acceptance

**Files:**
- Create: `tests/scenarios/test_member_profile_acceptance.py`
- Modify: `docs/operations/social-runtime-control-plane.md`
- Modify: `README.md`

- [ ] **Step 1: Add acceptance scenarios**

```python
def test_member_profile_accumulates_and_changes_reply_context(tmp_path):
    result = asyncio.run(_learn_then_reply(tmp_path))
    assert "不喜欢含糊解释" in result.profile_context
    assert result.reply_calls == 1


def test_third_party_claim_never_enters_reply_context(tmp_path):
    result = asyncio.run(_third_party_claim(tmp_path))
    assert result.fact.status == "proposed"
    assert result.fact.injectable is False
    assert result.fact.summary not in result.profile_context


def test_profile_provider_failure_keeps_chat_and_query_available(tmp_path):
    result = asyncio.run(_provider_failure_case(tmp_path))
    assert result.chat_processed is True
    assert result.query_succeeded is True
    assert result.safe_diagnostic == "profile_provider_unavailable"


def test_same_actor_profiles_remain_group_local(tmp_path):
    service = _service(tmp_path)
    service.confirm(_fact(group="g1", subject="u1", summary="只在一群发生的事情"))
    assert "一群" in service.context("p", "g1", "u1")
    assert "一群" not in service.context("p", "g2", "u1")
```

- [ ] **Step 2: Run the targeted acceptance suite**

Run:

```bash
.venv/bin/pytest -q \
  tests/social_runtime/profile \
  tests/contracts/test_deepseek_profile.py \
  tests/contracts/test_profile_query.py \
  tests/contracts/test_profile_web_api.py \
  tests/scenarios/test_profile_background_pipeline.py \
  tests/scenarios/test_profile_query_flow.py \
  tests/scenarios/test_member_profile_acceptance.py \
  tests/page/test_profile_workspace.py
```

Expected: PASS.

- [ ] **Step 3: Run limited regression checks**

Run:

```bash
.venv/bin/pytest -q \
  tests/contracts/test_participants.py \
  tests/contracts/test_affection_query.py \
  tests/contracts/test_direct_ambient_worker.py \
  tests/scenarios/test_chat_mainline.py \
  tests/shared/test_group_scope_privacy.py \
  tests/shared/test_plugin_skeleton.py \
  tests/page/test_router_contract.py \
  tests/page/test_product_ui.py
```

Expected: PASS without running the unrelated full repository suite.

- [ ] **Step 4: Document operation and privacy behavior**

Document the background queue, retry states, profile commands, administrator endpoint, cross-group isolation and the fact that model failures never block messages. Add a short README section listing `查看我的画像` and the private visibility rule.

- [ ] **Step 5: Verify scope and commit**

Run: `git diff --check && git status --short`

Expected: no whitespace errors; only intended implementation files plus the existing user-owned untracked `analysis/` directory.

```bash
git add README.md docs/operations/social-runtime-control-plane.md tests/scenarios/test_member_profile_acceptance.py
git commit -m "docs: document member profile operations"
```

### Task 10: Package the installable plugin

**Files:**
- Modify: `metadata.yaml`
- Test: `tests/shared/test_astrbot_package_loading.py`

- [ ] **Step 1: Bump the release candidate after all acceptance tests pass**

Change `metadata.yaml` from `1.0.0-rc.28` to `1.0.0-rc.29` and keep the package name unchanged.

- [ ] **Step 2: Verify package import and contents**

Run:

```bash
.venv/bin/pytest -q tests/shared/test_astrbot_package_loading.py tests/shared/test_plugin_skeleton.py
mkdir -p dist
git archive --format=zip --prefix=astrbot_plugin_groupmate/ \
  -o dist/astrbot_plugin_groupmate-1.0.0-rc.29.zip HEAD \
  README.md __init__.py _conf_schema.json eval groupmate logo.png main.py \
  metadata.yaml pages requirements.txt
unzip -t dist/astrbot_plugin_groupmate-1.0.0-rc.29.zip
```

Expected: package loading tests pass, `unzip -t` reports no errors and `dist/astrbot_plugin_groupmate-1.0.0-rc.29.zip` is created.

- [ ] **Step 3: Record the artifact checksum and commit release metadata**

Run: `shasum -a 256 dist/astrbot_plugin_groupmate-1.0.0-rc.29.zip`

```bash
git add metadata.yaml
git commit -m "chore: release 1.0.0-rc.29"
```

Report the absolute artifact path and SHA-256 to the user.
