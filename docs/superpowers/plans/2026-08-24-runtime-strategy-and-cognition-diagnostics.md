# Runtime Strategy And Cognition Diagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make strategy decisions and cognition failures understandable in the runtime page, and replace the fixed 10-second cognition cutoff with a configurable safe timeout.

**Architecture:** Keep the existing trace JSON contract and add a small frontend presentation layer for human labels. Thread one bounded timeout setting from AstrBot configuration into `CognitionBudget`; preserve concurrent workers and fail-closed ambient participation.

**Tech Stack:** Python 3, asyncio, SQLite JSON traces, vanilla JavaScript, CSS, pytest, Node.js.

---

### Task 1: Lock the runtime-page contract with failing tests

**Files:**
- Modify: `tests/page/test_shadow_console.py`
- Modify: `tests/page/test_product_ui.py`

- [ ] Add assertions for the three strategy lanes, formal-mode reply verdict, candidate source/count, Chinese worker/status/diagnostic labels, cognition error count, and cognition-aware filtering.
- [ ] Run only the two page test files and confirm the new assertions fail because the presenter and UI mappings do not exist.

### Task 2: Implement the trace presentation layer

**Files:**
- Modify: `pages/settings/components/presenters.js`
- Modify: `pages/settings/components/inspector.js`
- Modify: `pages/settings/workspaces/runtime.js`
- Modify: `pages/settings/styles/components.css`

- [ ] Add pure label/summary functions for participation lanes, cognition states, workers, candidate sources, and diagnostics.
- [ ] Render the strategy verdict in list rows and the inspector without exposing internal reasoning.
- [ ] Count degraded traces and make the “异常” filter include cognition failures.
- [ ] Add compact warning and strategy styles using the existing neutral/green/warning token system.
- [ ] Re-run the two page test files and confirm they pass.

### Task 3: Reproduce and correct the cognition timeout policy

**Files:**
- Modify: `tests/shared/test_plugin_skeleton.py`
- Modify: `tests/contracts/test_message_trace_bridge.py`
- Modify: `groupmate/settings.py`
- Modify: `groupmate/adapters/astrbot_bridge.py`
- Modify: `_conf_schema.json`

- [ ] Add failing settings and bridge tests proving a configured cognition timeout reaches `CognitionBudget` and defaults to 20 seconds.
- [ ] Run those focused tests and confirm failure against the hard-coded 10-second budget.
- [ ] Add the bounded setting and pass it through the composition boundary.
- [ ] Record safe Provider exception categories while keeping prompts, responses and secrets out of public traces.
- [ ] Re-run the focused settings, bridge and cognitive-worker tests.

### Task 4: Focused verification

**Files:**
- Verify only; no new files expected.

- [ ] Run the page tests, trace contract test, plugin settings test, bridge contract test and cognitive worker test.
- [ ] Run the existing frontend syntax/build check used by the repository.
- [ ] Inspect `git diff --check` and the final diff; preserve the unrelated untracked `analysis/` directory.
