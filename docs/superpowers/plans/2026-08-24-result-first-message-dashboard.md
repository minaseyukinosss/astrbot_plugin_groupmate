# Result-First Message Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing runtime message console show whether Groupmate would reply, why, and which message supports that conclusion before any technical diagnostics.

**Architecture:** Extend the privacy-trimmed message trace projection with one bounded `judgement` object derived only from validated cognitive observations and existing public message traces. Keep the current runtime table and inspector shell, add result-first presenters, merge repeated detail sections, and move diagnostics and stage timing into progressive disclosure.

**Tech Stack:** Python 3.10+ dataclasses and SQLite projections, vanilla JavaScript DOM renderers, CSS design tokens, pytest contract and page-source tests.

---

### Task 1: Project the validated model reason and one public evidence message

**Files:**
- Modify: `tests/contracts/test_message_traces.py`
- Modify: `tests/scenarios/test_social_runtime_shadow.py`
- Modify: `groupmate/social_runtime/scene_actor.py`
- Modify: `groupmate/social_runtime/control/message_traces.py`

- [ ] **Step 1: Write failing projection and privacy tests**

Extend the test evaluation helper with `context_events` and `cognitive_observations`. Add an AMBIENT observation whose proposition is:

```python
{
    "should_participate": False,
    "decision": "silence",
    "disruption_cost": 0.62,
    "novelty": 0.18,
    "reason": "成员正在自然交流，现在插话会打断对话。",
}
```

Use `evidence_event_ids=(evidence_event.event_id,)`, record the evidence message before the evaluated message, and assert the trace contains:

```python
{
    "source": "model",
    "status": "accepted",
    "decision": "silence",
    "would_reply": False,
    "label": "继续观察",
    "reason": "成员正在自然交流，现在插话会打断对话。",
    "evidence": {
        "actor": {"display_name": "夏夏"},
        "message": {"summary": "今晚一起打游戏吗？"},
    },
}
```

Add cases for a failed AMBIENT diagnostic (`status="unavailable"`, no fake reason/evidence) and a DIRECT_FAST evaluation (`source="policy"`, `status="not_required"`). Extend the shadow serialization test so `reason` is capped at 80 characters while `chain_of_thought`, raw response, prompt, API key, and internal evidence IDs remain absent from the public trace.

- [ ] **Step 2: Run the focused projection tests and confirm RED**

Run:

```bash
PYTHONPATH=/Library/Frameworks/Python.framework/Versions/3.7/lib/python3.7/site-packages /opt/homebrew/bin/python3.13 -m pytest tests/contracts/test_message_traces.py tests/scenarios/test_social_runtime_shadow.py -q --disable-warnings
```

Expected: the new `judgement` assertions fail because message traces currently expose only generic governor reasons.

- [ ] **Step 3: Implement bounded safe projection**

In `safe_cognitive_observation`, add `reason` to the allowed proposition keys but normalize it before persistence:

```python
if key == "reason":
    value = " ".join(str(value or "").split())[:80]
```

In `MessageTraceRepository`, select the newest `participation_assessment` from `evaluation.cognitive_observations`. Build `summary["judgement"]` with only `source`, `status`, `decision`, `would_reply`, `label`, `reason`, and optional `evidence`. Resolve evidence by querying `message_traces` with the newest valid evidence event ID under the same persona/group scope and copy only its existing `actor` and `message` public summaries. Never expose the event ID itself.

For a failed AMBIENT diagnostic, set `source="model"` and `status="unavailable"`. For DIRECT_FAST/CONTINUATION, set `source="policy"` and `status="not_required"`. External handoff continues to use its route/delivery state and does not invent a Groupmate judgement.

- [ ] **Step 4: Run the focused projection tests and confirm GREEN**

Run the command from Step 2. Expected: all selected tests pass.

- [ ] **Step 5: Commit the projection change**

```bash
git add tests/contracts/test_message_traces.py tests/scenarios/test_social_runtime_shadow.py groupmate/social_runtime/scene_actor.py groupmate/social_runtime/control/message_traces.py
git commit -m "feat: project safe message judgement summaries"
```

### Task 2: Add result-first presentation helpers

**Files:**
- Modify: `tests/page/test_product_ui.py`
- Modify: `pages/settings/components/presenters.js`

- [ ] **Step 1: Write failing presenter tests**

Add assertions for three pure helpers:

```javascript
traceResultHeadline(summary) // “正式运行不会回复” / “正式运行会回复”
traceResultState(summary)    // “继续观察” / “准备回复”
traceResultReason(summary)   // validated model reason or safe fallback
```

Cover accepted model silence, accepted model speak, unavailable model output, policy decision, pending Groupmate processing, and external plugin handoff. The unavailable case must use `cognitionDiagnosticExplanation` rather than the generic `forced_observe` label.

- [ ] **Step 2: Run the presenter tests and confirm RED**

Run:

```bash
PYTHONPATH=/Library/Frameworks/Python.framework/Versions/3.7/lib/python3.7/site-packages /opt/homebrew/bin/python3.13 -m pytest tests/page/test_product_ui.py -q --disable-warnings
```

Expected: imports or calls fail because the result-first helpers do not exist.

- [ ] **Step 3: Implement minimal deterministic presenters**

Implement the helpers with this precedence:

1. External owner → “由外部能力处理”.
2. Pending outcome → “等待完成判断”.
3. `decision.would_reply === true` → “正式运行会回复”.
4. Completed non-reply → “正式运行不会回复”.

For reasons, prefer accepted `judgement.reason`; then an unavailable diagnostic explanation; then `decision.reasons`; finally `route.reason`. Return plain text only.

- [ ] **Step 4: Run the presenter tests and confirm GREEN**

Run the command from Step 2. Expected: all page presenter tests pass.

- [ ] **Step 5: Commit the presenter change**

```bash
git add tests/page/test_product_ui.py pages/settings/components/presenters.js
git commit -m "feat: present message outcomes before diagnostics"
```

### Task 3: Reorder the existing runtime list and inspector

**Files:**
- Modify: `tests/page/test_shadow_console.py`
- Modify: `pages/settings/workspaces/runtime.js`
- Modify: `pages/settings/components/inspector.js`
- Modify: `pages/settings/styles/components.css`

- [ ] **Step 1: Write failing information-hierarchy tests**

Update the runtime console contract to require the existing table and drawer plus these labels/classes:

```text
处理结果
正式运行会回复
正式运行不会回复
判断原因
关键证据
result-summary
result-evidence
technical-details
```

Assert `认知后端`, `模型请求`, `输入大小`, `本次截止`, stage timeline, and raw diagnostic code are rendered only inside `technical-details`. Assert the old first-level fields `参与方案`, `SHADOW 前判断`, and `生成说明` no longer appear in the default inspector sections.

- [ ] **Step 2: Run the console contract tests and confirm RED**

Run:

```bash
PYTHONPATH=/Library/Frameworks/Python.framework/Versions/3.7/lib/python3.7/site-packages /opt/homebrew/bin/python3.13 -m pytest tests/page/test_shadow_console.py -q --disable-warnings
```

Expected: result-first labels/classes are missing and technical metrics remain visible in the default cognition section.

- [ ] **Step 3: Implement the list hierarchy**

In `runtime.js`, keep the existing six-column table. Change the decision cell to render `traceResultHeadline(summary)` as its strong line, `traceResultState(summary)` as its status line, and `traceResultReason(summary)` as the bounded two-line explanation. Do not change filters, refresh, row selection, message/media rendering, or delivery tone.

- [ ] **Step 4: Implement the inspector hierarchy**

In `inspector.js`, add a `renderResultSummary(summary)` unit directly after the incoming message heading. It renders the headline and state badge, one reason paragraph, one optional evidence row using the existing avatar and `renderMessageContent` functions, and an optional candidate reply only when one exists.

Keep “收到的消息” and “处理路径”. Replace the separate default understanding/strategy/delivery sections with the result summary. Move cognition diagnostics, stage timeline, backend/model metrics, strategy channel, original outcome/source, diagnostic code, timings, and delivery details into the existing `technical-details` element.

- [ ] **Step 5: Add restrained styles using existing tokens**

Add `.result-summary`, `.result-summary-heading`, `.result-state`, `.result-reason`, and `.result-evidence` styles. Use existing ink, muted, primary-soft, warning-soft, border, spacing, and radius tokens. Use separators and spacing instead of nested cards or shadows. On narrow screens, use a single-column flow and allow message text to wrap without shrinking the avatar.

- [ ] **Step 6: Run the console and presenter tests and confirm GREEN**

Run:

```bash
PYTHONPATH=/Library/Frameworks/Python.framework/Versions/3.7/lib/python3.7/site-packages /opt/homebrew/bin/python3.13 -m pytest tests/page/test_shadow_console.py tests/page/test_product_ui.py -q --disable-warnings
```

Expected: all selected page tests pass.

- [ ] **Step 7: Commit the UI change**

```bash
git add tests/page/test_shadow_console.py pages/settings/workspaces/runtime.js pages/settings/components/inspector.js pages/settings/styles/components.css
git commit -m "feat: make runtime message results immediately clear"
```

### Task 4: Refresh the local fixture and package rc.19

**Files:**
- Modify: `tests/page/fixtures/fake_bridge.js`
- Modify: `metadata.yaml`
- Create: `dist/astrbot_plugin_groupmate-1.0.0-rc.19.zip`

- [ ] **Step 1: Update the fake runtime projection**

Give at least one AMBIENT trace an accepted model `judgement` with a reason and one public evidence message, and give the timeout trace `status="unavailable"`. Keep DIRECT_FAST and external-plugin examples so every fallback remains locally inspectable.

- [ ] **Step 2: Bump the plugin version**

Change `metadata.yaml` from `version: 1.0.0-rc.18` to `version: 1.0.0-rc.19`.

- [ ] **Step 3: Run focused verification**

Run:

```bash
PYTHONPATH=/Library/Frameworks/Python.framework/Versions/3.7/lib/python3.7/site-packages /opt/homebrew/bin/python3.13 -m pytest tests/contracts/test_message_traces.py tests/scenarios/test_social_runtime_shadow.py tests/page/test_product_ui.py tests/page/test_shadow_console.py -q --disable-warnings
git diff --check
```

Expected: all selected tests pass and `git diff --check` prints no errors.

- [ ] **Step 4: Commit the release metadata and fixture**

```bash
git add tests/page/fixtures/fake_bridge.js metadata.yaml
git commit -m "chore: prepare groupmate 1.0.0-rc.19 package"
```

- [ ] **Step 5: Build and validate the plugin archive**

Run:

```bash
git archive --format=zip --prefix=astrbot_plugin_groupmate/ -o dist/astrbot_plugin_groupmate-1.0.0-rc.19.zip HEAD README.md __init__.py _conf_schema.json eval groupmate logo.png main.py metadata.yaml pages requirements.txt
unzip -t dist/astrbot_plugin_groupmate-1.0.0-rc.19.zip
shasum -a 256 dist/astrbot_plugin_groupmate-1.0.0-rc.19.zip
```

Expected: archive validation succeeds and a SHA-256 checksum is printed.

---

## Self-Review

- Spec coverage: result-first list and detail, bounded reason, one evidence message, model failure, policy/external fallbacks, technical progressive disclosure, responsive layout, privacy, and packaging each have an explicit task.
- Placeholder scan: no deferred behavior or unspecified error handling remains.
- Type consistency: the `judgement` object and the three presenter helper names are identical across projection, tests, list, and inspector; evidence reuses the existing public `actor` and `message` shapes.
