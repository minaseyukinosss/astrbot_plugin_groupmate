# Direct DeepSeek Ambient Cognition Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task.

**Goal:** Replace only the slow AMBIENT group-chat understanding call with a plugin-owned DeepSeek request that returns a compact, locally validated participation verdict within the existing eight-second decision budget.

**Architecture:** Groupmate will own one reusable asynchronous DeepSeek client and one `ambient_social_assessor` worker. The worker sends a bounded fact envelope, asks for a small JSON verdict with thinking disabled, validates every referenced event/member against the frozen `AttentionFrame`, and converts the verdict into the existing `CognitiveObservation` contract. FAST/CONTINUATION rules, participation policy, governor, final reply generation, delivery, and AstrBot plugin interoperability stay unchanged.

**Tech Stack:** Python 3.10+, `aiohttp`, existing Social Runtime cognition contracts, pytest, vanilla JavaScript control-plane UI.

---

## Non-negotiable behavior

- Direct DeepSeek is used only by `ambient_social_assessor`.
- Final reply generation continues through `AstrBotModelPort` and the configured AstrBot `generation_provider`.
- The direct HTTP request uses `stream: false`, `thinking: {"type": "disabled"}`, `response_format: {"type": "json_object"}`, `max_tokens: 192`, `temperature: 0.1`, and no retry.
- HTTP timeout is six seconds; the existing cognition service retains the absolute eight-second AMBIENT deadline.
- Missing/invalid direct cognition configuration is a readiness blocker. It never silently falls back to the currently slow AstrBot cognition route.
- Remote output is untrusted. Unknown targets, unknown evidence IDs, malformed JSON, unsupported signals, or invalid numbers result in safe observation/degradation, never an ACT decision.
- Logs, traces, UI payloads, and exceptions must not contain the API key, raw request prompt, response body, or chain-of-thought.
- Unit and scenario tests use fakes only. A live DeepSeek request is a manual SHADOW acceptance step after packaging.

## Task 1: Add direct cognition configuration and readiness blockers

**Files:**

- Modify: `groupmate/settings.py`
- Modify: `_conf_schema.json`
- Modify: `main.py`
- Modify: `tests/shared/test_plugin_skeleton.py`

### Step 1: Write failing configuration tests

Add assertions for three new settings:

```python
settings = SocialRuntimeSettings.from_mapping({
    "cognition_api_key": "  sk-test  ",
    "cognition_api_base": "https://api.deepseek.com/",
    "cognition_model": "deepseek-v4-flash",
})

assert settings.cognition_api_key == "sk-test"
assert settings.cognition_api_base == "https://api.deepseek.com"
assert settings.cognition_model == "deepseek-v4-flash"
assert "cognition_api_key" not in repr(settings)
```

Also update the visible schema assertion to include `cognition_api_key`, `cognition_api_base`, and `cognition_model`; assert the key field is password/secret-style, the base defaults to `https://api.deepseek.com`, and the model defaults to `deepseek-v4-flash`.

Add a composition-root test that constructs the control plane with a missing key and verifies the runtime blocker is `未配置认知模型 API Key`, while `generation_provider` remains separately required for final replies.

### Step 2: Run the focused test and verify it fails

Run:

```bash
pytest tests/shared/test_plugin_skeleton.py -q
```

Expected: FAIL because the settings and schema do not yet expose direct cognition configuration.

### Step 3: Implement normalized secret settings

Extend `SocialRuntimeSettings` with:

```python
cognition_api_key: str = field(default="", repr=False)
cognition_api_base: str = "https://api.deepseek.com"
cognition_model: str = "deepseek-v4-flash"
```

Normalize whitespace and remove trailing `/` from the base. Reject non-HTTP(S) bases, query strings, fragments, missing hosts, or empty model names. Preserve the current 3–15 second setting and the 20→8 migration.

In `_conf_schema.json`, make the purpose explicit:

- `generation_provider`: final reply model managed by AstrBot.
- `cognition_api_key`: DeepSeek secret used only for group-chat understanding.
- `cognition_api_base`: normally leave at the official endpoint.
- `cognition_model`: default `deepseek-v4-flash`; allow a compatible model ID such as Pro.

In `main.py`, readiness requires runtime mode, final reply provider, direct cognition key, base, and model. Keep all blocker text user-facing and specific.

### Step 4: Re-run the focused test

Run:

```bash
pytest tests/shared/test_plugin_skeleton.py -q
```

Expected: PASS.

### Step 5: Commit

```bash
git add groupmate/settings.py _conf_schema.json main.py tests/shared/test_plugin_skeleton.py
git commit -m "feat: configure direct deepseek cognition"
```

## Task 2: Build the safe reusable DeepSeek HTTP client

**Files:**

- Create: `groupmate/adapters/deepseek_cognition.py`
- Create: `tests/contracts/test_deepseek_cognition.py`
- Modify: `requirements.txt`

### Step 1: Write failing transport-contract tests

Use an injected fake transport, not the network. Cover:

1. URL is exactly `<normalized-base>/chat/completions`.
2. Authorization is `Bearer <key>` and never appears in `repr(client)` or an error string.
3. Request has the exact control parameters:

```python
assert body["model"] == "deepseek-v4-flash"
assert body["stream"] is False
assert body["thinking"] == {"type": "disabled"}
assert body["response_format"] == {"type": "json_object"}
assert body["max_tokens"] == 192
assert body["temperature"] == 0.1
```

4. The client extracts `choices[0].message.content` and parses one JSON object.
5. 401/403, 429, 5xx, network errors, six-second timeouts, empty choices, and malformed JSON map only to safe stable categories:

```text
direct_auth_failed
direct_rate_limited
direct_upstream_failed
direct_network_failed
direct_timeout
direct_invalid_output
```

6. The transport is reused across calls and closed exactly once.

### Step 2: Run the new test and verify it fails

Run:

```bash
pytest tests/contracts/test_deepseek_cognition.py -q
```

Expected: FAIL because the direct client does not exist.

### Step 3: Implement an injectable client boundary

Define small internal contracts:

```python
@dataclass(frozen=True)
class JsonHttpResponse:
    status: int
    body: object

class JsonHttpTransport(Protocol):
    async def post_json(self, *, url, headers, payload, timeout_seconds): ...
    async def close(self) -> None: ...

@dataclass(frozen=True)
class DirectCognitionResponse:
    verdict: Mapping[str, object]
    latency_ms: int
    request_bytes: int
    backend: str
    model: str
```

`AioHttpJsonTransport` lazily creates and reuses one `aiohttp.ClientSession`. It reads successful JSON responses with a bounded body and raises only sanitized internal failures. `DeepSeekCognitionClient.classify(facts)` composes the system/user messages, records request byte size and monotonic latency, and returns `DirectCognitionResponse`.

The system message requires only this object and forbids prose:

```json
{
  "decision": "speak|silence",
  "signal": "help_request|care_signal|humor_signal|greeting|boundary_signal|none",
  "target_id": "member id or null",
  "evidence_event_ids": ["known event id"],
  "confidence": 0.0,
  "disruption": 0.0,
  "novelty": 0.0,
  "reason": "short reason"
}
```

Add `aiohttp>=3.9,<4` to `requirements.txt` so a standalone AstrBot installation declares the dependency instead of relying on an incidental transitive package.

### Step 4: Re-run focused tests

Run:

```bash
pytest tests/contracts/test_deepseek_cognition.py -q
```

Expected: PASS.

### Step 5: Commit

```bash
git add groupmate/adapters/deepseek_cognition.py tests/contracts/test_deepseek_cognition.py requirements.txt
git commit -m "feat: add safe direct deepseek client"
```

## Task 3: Add the compact AMBIENT worker and local verdict normalization

**Files:**

- Create: `groupmate/social_runtime/cognition/ambient_worker.py`
- Create: `tests/contracts/test_direct_ambient_worker.py`
- Modify: `groupmate/social_runtime/cognition/contracts.py`
- Modify: `groupmate/social_runtime/cognition/service.py`
- Modify: `tests/contracts/test_cognitive_worker.py`

### Step 1: Write failing fact-envelope tests

Build a frame/context containing more data than the limits and assert the outgoing facts contain only:

- at most the last 12 focus events;
- at most 4 active topics;
- at most 8 candidate/topic participants;
- event ID, actor ID, bounded text, reply target, mention IDs, and non-text part kinds;
- bounded activity, lease state, and only `participation`/`presence` persona guidance.

Assert it excludes API credentials, media URLs, database paths, generic JSON Schema, scene/config/persona version hashes, and unrelated persona sections. Add a byte-size guard for the representative fixture so the new request is materially smaller than the current 5 KB trace.

### Step 2: Write failing verdict-validation tests

Using a fake client, cover:

- valid `speak` + signal becomes one signal observation followed by exactly one `participation_assessment`;
- valid `silence` produces only the assessment;
- `target_id` outside `frame.candidate_audiences` is rejected;
- evidence IDs outside `frame.focus_event_ids` are rejected;
- unsupported signal/decision, empty evidence, booleans masquerading as numbers, NaN/Infinity, or values outside 0..1 return `direct_invalid_output`;
- local fields are authoritative: `scene_version=frame.scene_version`, `expires_at=context.now + 30`, and topic comes only from `frame.focus_topic_ids`;
- transport categories and timing/bytes/backend/model are carried into `CognitiveWorkerResult` without raw response content.

### Step 3: Run focused tests and verify failure

Run:

```bash
pytest tests/contracts/test_direct_ambient_worker.py tests/contracts/test_cognitive_worker.py -q
```

Expected: FAIL because the direct worker and diagnostic metadata do not exist.

### Step 4: Implement the direct worker

Create `DirectAmbientWorker` with fixed name `ambient_social_assessor`. Its `input_bytes()` must calculate the same serialized request size the client will send. Its `observe_with_result()` calls `classify()`, validates the compact verdict, and emits existing immutable observations.

Normalize a valid assessment to:

```python
{
    "should_participate": decision == "speak",
    "decision": decision,
    "target_confidence": confidence if target_id else 0.0,
    "topic_confidence": confidence if frame.focus_topic_ids else 0.0,
    "disruption_cost": disruption,
    "novelty": novelty,
    "repetition_cost": 0.0,
    "reason": bounded_reason,
    "subject_id": target_id,
    "topic_id": first_known_topic,
}
```

Extend `CognitiveWorkerResult` and `CognitiveWorkerDiagnostic` with optional `backend` and `model` strings, defaulting to empty for old/local workers. Teach `CognitionService` to preserve them and classify direct categories:

- `direct_timeout` → `TIMED_OUT`
- `direct_invalid_output` → `INVALID_OUTPUT`
- all other `direct_*` failures → `MODEL_FAILED`

Keep the service's hard absolute deadline as a second safety boundary.

### Step 5: Re-run focused tests

Run:

```bash
pytest tests/contracts/test_direct_ambient_worker.py tests/contracts/test_cognitive_worker.py -q
```

Expected: PASS.

### Step 6: Commit

```bash
git add groupmate/social_runtime/cognition/ambient_worker.py groupmate/social_runtime/cognition/contracts.py groupmate/social_runtime/cognition/service.py tests/contracts/test_direct_ambient_worker.py tests/contracts/test_cognitive_worker.py
git commit -m "feat: add compact ambient cognition worker"
```

## Task 4: Wire only AMBIENT cognition to the plugin client

**Files:**

- Modify: `groupmate/adapters/astrbot_bridge.py`
- Modify: `tests/shared/test_plugin_skeleton.py`
- Modify: `tests/scenarios/test_social_runtime_shadow.py`

### Step 1: Write failing bridge lifecycle tests

Inject a fake direct client factory into the bridge and assert:

- the registered model worker set is exactly `ambient_social_assessor`;
- it is a `DirectAmbientWorker`, not `AstrBotStructuredWorker`;
- `AstrBotModelPort` still backs `ReplyExecutor`;
- bridge shutdown closes the direct client once, even when startup or manager shutdown fails;
- OFF mode does not create or close a direct client;
- a SHADOW AMBIENT cycle consumes a valid fake verdict and reaches the existing participation/governor path without sending a message.

### Step 2: Run bridge/scenario tests and verify failure

Run:

```bash
pytest tests/shared/test_plugin_skeleton.py tests/scenarios/test_social_runtime_shadow.py -q
```

Expected: FAIL because the bridge still constructs `AstrBotStructuredWorker` for cognition.

### Step 3: Implement composition wiring

In `AstrBotSocialRuntimeBridge.start()`:

1. construct `AstrBotModelPort` only for `ReplyExecutor`;
2. construct one `DeepSeekCognitionClient` from the three direct settings;
3. register only `DirectAmbientWorker(client)` under `ambient_social_assessor`;
4. retain the existing worker concurrency and hard timeout controls.

Store the direct client on the bridge. In `close()`, detach it first, then close manager/client safely so repeated close calls are idempotent and the session cannot leak.

Do not re-register `direct_interaction`: FAST and CONTINUATION remain deterministic and do not request that worker in the current mainline.

### Step 4: Re-run focused tests

Run:

```bash
pytest tests/shared/test_plugin_skeleton.py tests/scenarios/test_social_runtime_shadow.py -q
```

Expected: PASS.

### Step 5: Commit

```bash
git add groupmate/adapters/astrbot_bridge.py tests/shared/test_plugin_skeleton.py tests/scenarios/test_social_runtime_shadow.py
git commit -m "feat: route ambient cognition directly to deepseek"
```

## Task 5: Make the runtime page explain the new route and failures

**Files:**

- Modify: `groupmate/social_runtime/control/message_traces.py`
- Modify: `pages/settings/components/presenters.js`
- Modify: `pages/settings/components/inspector.js`
- Modify: `tests/contracts/test_message_traces.py`
- Modify: `tests/page/test_product_ui.py`
- Modify: `tests/page/test_shadow_console.py`
- Modify: `tests/page/fixtures/fake_bridge.js`

### Step 1: Write failing trace and presenter tests

Assert persisted diagnostics add only safe fields:

```json
{
  "backend": "direct_deepseek",
  "model": "deepseek-v4-flash",
  "diagnostic_code": "direct_timeout"
}
```

Add presenter cases for:

- success: `直连 DeepSeek · deepseek-v4-flash`;
- `direct_timeout`: `直连模型在 6 秒内未返回，本次已转为保守观察。`;
- auth: ask the administrator to check the cognition API key;
- rate limit: explain rate limiting without dumping upstream content;
- upstream/network: distinguish service/network failure;
- invalid output: explain local schema validation rejection.

Assert legacy records without backend/model still render with current Provider wording.

### Step 2: Run focused tests and verify failure

Run:

```bash
pytest tests/contracts/test_message_traces.py tests/page/test_product_ui.py tests/page/test_shadow_console.py -q
```

Expected: FAIL because the trace and UI do not expose the direct backend.

### Step 3: Implement safe diagnostics and UI labels

Persist `backend` and `model` from `CognitiveWorkerDiagnostic`. In the inspector, place the route label near the worker heading and rename `Provider 等待` to `模型请求` when `backend == "direct_deepseek"`. Keep queue wait, input size, and cutoff rows.

Map only stable categories in `cognitionDiagnosticExplanation()`. Never display arbitrary text after a colon for direct failures. Keep old `model_call_failed:*` records compatible.

Update fake data with one successful direct call and one sanitized timeout so the page can be checked without a live API.

### Step 4: Re-run focused tests

Run:

```bash
pytest tests/contracts/test_message_traces.py tests/page/test_product_ui.py tests/page/test_shadow_console.py -q
```

Expected: PASS.

### Step 5: Commit

```bash
git add groupmate/social_runtime/control/message_traces.py pages/settings/components/presenters.js pages/settings/components/inspector.js tests/contracts/test_message_traces.py tests/page/test_product_ui.py tests/page/test_shadow_console.py tests/page/fixtures/fake_bridge.js
git commit -m "feat: explain direct cognition diagnostics"
```

## Task 6: Minimal regression verification and rc.17 package

**Files:**

- Modify: `metadata.yaml`
- Create: `dist/astrbot_plugin_groupmate-1.0.0-rc.17.zip`

### Step 1: Run only the relevant regression set

Run:

```bash
pytest \
  tests/contracts/test_deepseek_cognition.py \
  tests/contracts/test_direct_ambient_worker.py \
  tests/contracts/test_cognitive_worker.py \
  tests/contracts/test_message_traces.py \
  tests/shared/test_plugin_skeleton.py \
  tests/scenarios/test_social_runtime_shadow.py \
  tests/page/test_product_ui.py \
  tests/page/test_shadow_console.py -q
git diff --check
```

Expected: selected tests PASS and `git diff --check` has no output. Do not run live API tests or the entire repository suite unless these focused tests expose cross-module risk.

### Step 2: Bump and package

Set `metadata.yaml` to `1.0.0-rc.17`. Build the install ZIP from tracked plugin runtime assets while excluding `.git`, `analysis`, tests, caches, docs, and previous `dist` artifacts. The archive root must contain `main.py`, `metadata.yaml`, `_conf_schema.json`, `requirements.txt`, `groupmate/`, and `pages/` rather than an extra parent folder.

### Step 3: Verify the archive

Run:

```bash
unzip -t dist/astrbot_plugin_groupmate-1.0.0-rc.17.zip
shasum -a 256 dist/astrbot_plugin_groupmate-1.0.0-rc.17.zip
```

Expected: archive integrity succeeds and one SHA-256 is printed.

### Step 4: Commit release metadata

```bash
git add metadata.yaml
git commit -m "chore: prepare groupmate 1.0.0-rc.17 package"
```

Do not commit the ZIP if `dist/` remains intentionally ignored; provide its absolute path and hash to the user.

## Manual SHADOW acceptance after installation

1. Configure final reply Provider as before.
2. Fill the direct cognition API key, keep `https://api.deepseek.com`, and select `deepseek-v4-flash` (or the user's compatible Pro model ID).
3. Restart the plugin and keep mode at SHADOW.
4. Send an ordinary multi-member group-chat sequence that should remain silent and one clear help/humor opportunity that may justify participation.
5. Open each message's complete processing route. Confirm the cognition card shows `直连 DeepSeek`, a model ID, request time below the eight-second cutoff, and either a completed verdict or a specific safe failure category.
6. Confirm SHADOW sends no group message. Only after representative samples have usable direct verdicts should formal sending be considered.

