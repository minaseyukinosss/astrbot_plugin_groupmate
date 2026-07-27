# Scene-Driven Groupmate Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add explicit scene classification, lossless hard-turn scheduling, sender-scoped continuations, real quote delivery, and scene-conditional replay metrics.

**Architecture:** Preserve the current GroupActor and cognitive workflow. Add a deterministic scene projection to the domain, use it to create anchored turn requests, serialize hard requests through a FIFO while soft traffic remains coalesced, and carry quote semantics through a structured delivery port.

**Tech Stack:** Python 3.7-compatible standard library, asyncio, AstrBot MessageChain, pytest.

---

### Task 1: Interaction Scene Projection

**Files:**
- Create: `groupmate/core/scenes.py`
- Modify: `groupmate/models.py`
- Modify: `groupmate/engine/workflow.py`
- Test: `tests/test_scenes.py`

- [ ] Write failing tests proving native direct, reply-to-bot, continuation, and ordinary candidates map to different scenes.
- [ ] Run `pytest tests/test_scenes.py -q` and confirm missing scene symbols fail.
- [ ] Implement `InteractionScene`, `ScenePolicy`, and deterministic `classify_scene()`.
- [ ] Run `pytest tests/test_scenes.py -q` and confirm all scene tests pass.

### Task 2: Lossless Hard Scheduling

**Files:**
- Modify: `groupmate/engine/runtime.py`
- Modify: `groupmate/core/projections.py`
- Test: `tests/test_runtime.py`
- Test: `tests/test_phase2_projections.py`

- [ ] Write failing tests for two direct requests arriving during an active hard evaluation and for two simultaneous sender continuations.
- [ ] Run the focused tests and confirm the second request is currently cancelled or overwritten.
- [ ] Add an anchored hard FIFO and sender-indexed continuation grants; keep the soft debounce as a coalescing slot.
- [ ] Run the focused runtime and projection tests and confirm both requests are evaluated in order.

### Task 3: Real Quote Delivery

**Files:**
- Modify: `groupmate/host/llm.py`
- Test: `tests/test_platform_port.py`

- [ ] Write a failing test asserting the first outgoing chain contains a reply component for `quote_message_id` and later segments do not repeat it.
- [ ] Run `pytest tests/test_platform_port.py -q` and confirm the reply component is missing.
- [ ] Build a MessageChain with AstrBot's reply component when available, with a compatible OneBot segment fallback.
- [ ] Run the platform and delivery tests and confirm quote metadata reaches the first segment only.

### Task 4: Scene-Conditional Replay Metrics

**Files:**
- Create: `eval/scene_metrics.py`
- Create: `tests/test_scene_metrics.py`
- Modify: `eval/README.md`

- [ ] Write failing tests for per-scene counts, quote rates, media rates, length quantiles, and latency quantiles.
- [ ] Run `pytest tests/test_scene_metrics.py -q` and confirm the module is missing.
- [ ] Implement deterministic aggregation from labeled replay observations without random targets.
- [ ] Run scene metric tests and document aggregate ratios as validation checks only.

### Task 5: Regression Verification

**Files:**
- Modify only files required by failures introduced by Tasks 1-4.

- [ ] Run `pytest -q`.
- [ ] Run `python -m eval.runner --provider scripted --repetitions 1` using the repository's documented arguments.
- [ ] Review `git diff --check`, `git status --short`, and the final diff.

