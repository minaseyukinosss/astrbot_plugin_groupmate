# AMBIENT Cognition Latency Root Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make ordinary-group cognition use bounded context, one model call, and a deadline that cannot outlive the eight-second AMBIENT decision window.

**Architecture:** Keep deterministic routing and local participation governance unchanged. Replace the two AMBIENT workers with one structured worker that emits both social signals and a participation assessment, cap rolling attention inputs at their source, and extend diagnostics with safe numeric timings and input size.

**Tech Stack:** Python 3.13, asyncio, dataclasses, SQLite JSON persistence, vanilla JavaScript presenter tests, pytest.

---

### Task 1: Bound the rolling attention window

**Files:**
- Modify: `groupmate/social_runtime/attention.py`
- Test: `tests/scenarios/test_attention_windows.py`

- [x] Add a failing test that ingests more than 12 messages, 4 topics, and 8 actors and asserts the pending window keeps only the newest allowed values.
- [x] Run `.venv/bin/pytest -q tests/scenarios/test_attention_windows.py` and confirm the new assertion fails.
- [x] Add a `_append_recent_unique(values, value, limit)` helper and apply limits 12/4/8 while building the AMBIENT window.
- [x] Change AMBIENT `requested_workers` to `("ambient_social_assessor",)`.
- [x] Re-run the focused test and confirm it passes.

### Task 2: Produce both observation classes in one worker call

**Files:**
- Modify: `groupmate/adapters/astrbot_bridge.py`
- Modify: `groupmate/social_runtime/cognition/astrbot_workers.py`
- Test: `tests/contracts/test_cognitive_worker.py`
- Test: `tests/shared/test_plugin_skeleton.py`

- [x] Add failing assertions that the bridge registers `ambient_social_assessor` and the worker instruction requires social signals plus exactly one `participation_assessment` without reply text.
- [x] Run the two focused test files and confirm failure.
- [x] Register the combined worker, retain historical label mappings, and update the instruction contract.
- [x] Re-run the focused tests and confirm success.

### Task 3: Enforce the real AMBIENT budget and safe diagnostics

**Files:**
- Modify: `groupmate/settings.py`
- Modify: `_conf_schema.json`
- Modify: `groupmate/social_runtime/cognition/contracts.py`
- Modify: `groupmate/social_runtime/cognition/service.py`
- Modify: `groupmate/social_runtime/manager.py`
- Test: `tests/contracts/test_cognitive_worker.py`
- Test: `tests/shared/test_plugin_skeleton.py`

- [x] Add failing tests for default 8 seconds, bounds 3–15, AMBIENT effective timeout no greater than remaining deadline, and numeric `queue_wait_ms/provider_latency_ms/input_bytes/timeout_ms` fields.
- [x] Run only those tests and confirm failure.
- [x] Measure serialized input without retaining prompt text; have the concurrency gate report queue wait; compute the effective timeout from the eight-second AMBIENT deadline and configured safety cap.
- [x] Persist only numeric diagnostics and exception class names.
- [x] Re-run the focused tests and confirm success.

### Task 4: Present the new diagnostics and verify compatibility

**Files:**
- Modify: `pages/settings/components/presenters.js`
- Modify: `pages/settings/components/inspector.js`
- Modify: `tests/page/test_product_ui.py`
- Modify: `tests/page/test_shadow_console.py`

- [x] Add failing presenter tests for Provider time, queue time, input size, actual deadline, and old records with only `latency_ms`.
- [x] Run the two page test files and confirm failure.
- [x] Render a concise diagnosis in the main card and keep numeric details under the collapsed technical section.
- [x] Run the targeted backend and page tests, JavaScript syntax checks, and `git diff --check`.
