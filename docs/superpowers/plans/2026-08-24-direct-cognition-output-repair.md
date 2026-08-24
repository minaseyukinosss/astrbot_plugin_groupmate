# Direct Cognition Output Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make valid DeepSeek AMBIENT judgments reliably usable, preserve strict reply safety, and expose the exact safe failure category when a response cannot be accepted.

**Architecture:** Keep the plugin-owned `/chat/completions` route and JSON mode. Strengthen the request contract with a complete example, split transport parsing from local verdict validation, normalize only safe `silence` responses with empty evidence to the newest frozen event, and map every rejected result to a non-sensitive diagnostic code shown in the runtime page.

**Tech Stack:** Python 3.10+ async runtime, DeepSeek-compatible JSON HTTP API, pytest contract tests, vanilla JavaScript presenter tests.

---

### Task 1: Make the DeepSeek response contract explicit and diagnosable

**Files:**
- Modify: `tests/contracts/test_deepseek_cognition.py`
- Modify: `groupmate/adapters/deepseek_cognition.py`

- [ ] **Step 1: Write failing request-contract and parse-category tests**

Extend `test_direct_client_sends_bounded_non_thinking_json_request` so the system message must contain a complete JSON example and the rule that `silence` may use an empty evidence list while `speak` may not. Replace the collapsed invalid-output cases with these exact expectations:

```python
(
    JsonHttpResponse(200, {"choices": []}),
    None,
    "direct_response_shape_invalid",
),
(
    JsonHttpResponse(200, {"choices": [{"message": {"content": ""}}]}),
    None,
    "direct_response_empty",
),
(
    JsonHttpResponse(200, {"choices": [{"message": {"content": "not json"}}]}),
    None,
    "direct_response_json_invalid",
),
(
    JsonHttpResponse(200, {"choices": [{"message": {"content": "[]"}}]}),
    None,
    "direct_response_shape_invalid",
),
```

- [ ] **Step 2: Run the focused client test and confirm RED**

Run:

```bash
PYTHONPATH=/Library/Frameworks/Python.framework/Versions/3.7/lib/python3.7/site-packages /opt/homebrew/bin/python3.13 -m pytest tests/contracts/test_deepseek_cognition.py -q --disable-warnings
```

Expected: failures still report `direct_invalid_output`, and the prompt-contract assertions fail.

- [ ] **Step 3: Implement the explicit prompt and parse boundaries**

Update `_SYSTEM_MESSAGE` with a literal complete object containing all required fields:

```json
{"decision":"silence","signal":"none","target_id":null,"evidence_event_ids":[],"confidence":0.74,"disruption":0.62,"novelty":0.18,"reason":"成员正在自然交流，插话会打断"}
```

State that IDs must be copied from the input, all three scores are JSON numbers in `[0,1]`, `speak` requires a non-`none` signal and at least one evidence ID, and no Markdown/reply/reasoning may be emitted. In `classify`, classify failures without retaining remote content:

```python
try:
    choices = response.body["choices"]
    content = choices[0]["message"]["content"]
except (KeyError, IndexError, TypeError):
    raise self._error("direct_response_shape_invalid", started, request_bytes) from None
if not isinstance(content, str):
    raise self._error("direct_response_shape_invalid", started, request_bytes) from None
if not content.strip():
    raise self._error("direct_response_empty", started, request_bytes) from None
try:
    verdict = json.loads(content)
except json.JSONDecodeError:
    raise self._error("direct_response_json_invalid", started, request_bytes) from None
if not isinstance(verdict, dict):
    raise self._error("direct_response_shape_invalid", started, request_bytes) from None
```

- [ ] **Step 4: Run the focused client test and confirm GREEN**

Run the command from Step 2. Expected: all tests in `test_deepseek_cognition.py` pass.

- [ ] **Step 5: Commit the client contract change**

```bash
git add tests/contracts/test_deepseek_cognition.py groupmate/adapters/deepseek_cognition.py
git commit -m "fix: harden direct cognition response contract"
```

### Task 2: Normalize safe silence and retain strict local validation

**Files:**
- Modify: `tests/contracts/test_direct_ambient_worker.py`
- Modify: `groupmate/social_runtime/cognition/ambient_worker.py`

- [ ] **Step 1: Write failing normalization and diagnostic tests**

Add a test proving `decision="silence"`, `signal="none"`, and `evidence_event_ids=[]` succeeds with `("qq:13",)` as evidence. Add a separate test proving `speak` with empty evidence remains rejected. Replace the collapsed parametrization with `(overrides, expected_code)` cases for every category:

```python
({"decision": "maybe"}, "direct_invalid_decision"),
({"signal": "unknown"}, "direct_invalid_signal"),
({"decision": "speak", "signal": "none"}, "direct_speak_without_signal"),
({"target_id": "outside-frame"}, "direct_unknown_target"),
({"evidence_event_ids": []}, "direct_empty_speak_evidence"),
({"evidence_event_ids": ["qq:outside"]}, "direct_unknown_evidence"),
({"confidence": True}, "direct_invalid_score"),
({"disruption": 2.0}, "direct_invalid_score"),
```

Also remove one required key from a verdict and expect `direct_missing_field`.

- [ ] **Step 2: Run the focused worker test and confirm RED**

Run:

```bash
PYTHONPATH=/Library/Frameworks/Python.framework/Versions/3.7/lib/python3.7/site-packages /opt/homebrew/bin/python3.13 -m pytest tests/contracts/test_direct_ambient_worker.py -q --disable-warnings
```

Expected: all new cases still collapse to `direct_invalid_output`, and empty-evidence silence is rejected.

- [ ] **Step 3: Implement typed verdict rejection and bounded normalization**

Add a private exception carrying only a safe code:

```python
class _VerdictRejected(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)
```

Require exactly the presence of `decision`, `signal`, `target_id`, `evidence_event_ids`, `confidence`, `disruption`, `novelty`, and `reason`. Map invalid values to the codes listed in Step 1. If evidence is empty and the decision is `silence`, replace it with `(frame.focus_event_ids[-1],)`; if no frozen event exists, reject it as `direct_unknown_evidence`. Never apply this normalization to `speak`. Catch `_VerdictRejected` in `observe_with_result` and return its code without raw model content.

- [ ] **Step 4: Run the focused worker test and confirm GREEN**

Run the command from Step 2. Expected: all tests pass and valid observations still preserve the original local evidence constraints.

- [ ] **Step 5: Commit the worker validation change**

```bash
git add tests/contracts/test_direct_ambient_worker.py groupmate/social_runtime/cognition/ambient_worker.py
git commit -m "fix: normalize safe direct cognition verdicts"
```

### Task 3: Preserve INVALID_OUTPUT semantics and explain each failure in the UI

**Files:**
- Modify: `tests/contracts/test_direct_ambient_worker.py`
- Modify: `tests/page/test_product_ui.py`
- Modify: `groupmate/social_runtime/cognition/service.py`
- Modify: `pages/settings/components/presenters.js`

- [ ] **Step 1: Write failing service-status and presenter tests**

Add a service test that feeds `DirectCognitionError("direct_response_json_invalid")` through `CognitionService` and expects status `INVALID_OUTPUT`. Extend the presenter test to assert concise Chinese messages for:

```text
direct_response_empty
direct_response_json_invalid
direct_response_shape_invalid
direct_missing_field
direct_invalid_decision
direct_invalid_signal
direct_speak_without_signal
direct_unknown_target
direct_empty_speak_evidence
direct_unknown_evidence
direct_invalid_score
```

Keep `direct_invalid_output` as a readable legacy case.

- [ ] **Step 2: Run the focused service and page tests and confirm RED**

Run:

```bash
PYTHONPATH=/Library/Frameworks/Python.framework/Versions/3.7/lib/python3.7/site-packages /opt/homebrew/bin/python3.13 -m pytest tests/contracts/test_direct_ambient_worker.py tests/page/test_product_ui.py -q --disable-warnings
```

Expected: the new direct response code is classified as `MODEL_FAILED`, and new presenter explanations fall back to generic wording.

- [ ] **Step 3: Implement status grouping and safe user-facing explanations**

In `CognitionService`, classify parse/shape and verdict-validation codes as `INVALID_OUTPUT`, while keeping timeout, auth, rate limit, network, and upstream errors in their existing operational categories. In the presenter, map each new code to a specific non-sensitive explanation, for example `direct_response_empty` to `认知模型返回了空内容，本次未采用。` and `direct_unknown_evidence` to `认知模型引用了当前上下文之外的消息，本次未采用。` Do not show raw model output, IDs, prompts, or exception text.

- [ ] **Step 4: Run the focused service and page tests and confirm GREEN**

Run the command from Step 2. Expected: all selected tests pass.

- [ ] **Step 5: Commit diagnostics and UI explanations**

```bash
git add tests/contracts/test_direct_ambient_worker.py tests/page/test_product_ui.py groupmate/social_runtime/cognition/service.py pages/settings/components/presenters.js
git commit -m "fix: explain direct cognition output failures"
```

### Task 4: Version, focused verification, and installation package

**Files:**
- Modify: `metadata.yaml`
- Create: `dist/astrbot_plugin_groupmate-1.0.0-rc.18.zip`

- [ ] **Step 1: Bump the plugin release candidate**

Change `metadata.yaml` from `version: 1.0.0-rc.17` to `version: 1.0.0-rc.18`.

- [ ] **Step 2: Run only the relevant verification set**

Run:

```bash
PYTHONPATH=/Library/Frameworks/Python.framework/Versions/3.7/lib/python3.7/site-packages /opt/homebrew/bin/python3.13 -m pytest tests/contracts/test_deepseek_cognition.py tests/contracts/test_direct_ambient_worker.py tests/page/test_product_ui.py -q --disable-warnings
git diff --check
```

Expected: all selected tests pass and `git diff --check` emits no output.

- [ ] **Step 3: Commit the version bump**

```bash
git add metadata.yaml
git commit -m "chore: prepare groupmate 1.0.0-rc.18 package"
```

- [ ] **Step 4: Build and validate the archive**

Run:

```bash
git archive --format=zip --prefix=astrbot_plugin_groupmate/ -o dist/astrbot_plugin_groupmate-1.0.0-rc.18.zip HEAD README.md __init__.py _conf_schema.json eval groupmate logo.png main.py metadata.yaml pages requirements.txt
unzip -t dist/astrbot_plugin_groupmate-1.0.0-rc.18.zip
shasum -a 256 dist/astrbot_plugin_groupmate-1.0.0-rc.18.zip
```

Expected: archive validation succeeds and a SHA-256 checksum is printed.

---

## Self-Review

- Spec coverage: the request protocol, safe silence normalization, strict speak validation, parse/validation subcodes, service status, UI explanations, legacy compatibility, focused verification, and rc.18 package each have a concrete task.
- Placeholder scan: no deferred steps or unspecified error handling remain.
- Type consistency: all diagnostic names match across the client, worker, service, presenter, and tests; `evidence_event_ids` remains a tuple in local observations and an array in model JSON.
