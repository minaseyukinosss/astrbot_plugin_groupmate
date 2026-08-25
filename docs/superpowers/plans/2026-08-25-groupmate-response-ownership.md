# Groupmate Single Response Ownership Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure a direct group-chat message can be answered by Groupmate or AstrBot's default LLM chain, but never both.

**Architecture:** Keep AstrBot's existing high-priority fact observer and low-priority Groupmate handler. At the start of `AstrBotSocialRuntimeBridge.handle_event`, resolve the message once and synchronously claim the host event only when the effective group mode is `SOCIAL_RUNTIME`, the message is directly addressed to the persona, and no external plugin owns it; claiming calls AstrBot's `stop_event()` before ingestion, cognition, generation, or delivery.

**Tech Stack:** Python 3, AstrBot plugin event API, pytest

---

## File Structure

- Modify `groupmate/adapters/astrbot_bridge.py`: add the deterministic ownership predicate and stop the host event before Social Runtime work.
- Modify `tests/contracts/test_message_trace_bridge.py`: cover production direct-address claiming and the non-claiming SHADOW/external/ambient boundaries.

### Task 1: Claim only Groupmate-owned direct responses

**Files:**
- Modify: `tests/contracts/test_message_trace_bridge.py`
- Modify: `groupmate/adapters/astrbot_bridge.py:162-186`

- [x] **Step 1: Write the failing boundary test**

Extend the fake event with a `stop_event()` counter and give the fake manager an effective `group_mode()`. Add one parameterized test whose cases assert:

```python
@pytest.mark.parametrize(
    ("mode", "message", "segments", "expected_stops"),
    (
        (RuntimeMode.SOCIAL_RUNTIME, "你好", _at_bot("你好"), 1),
        (RuntimeMode.SOCIAL_RUNTIME, "小爱在吗", _text("小爱在吗"), 1),
        (RuntimeMode.SOCIAL_RUNTIME, "然后呢", _reply_to_bot("然后呢"), 1),
        (RuntimeMode.SHADOW, "你好", _at_bot("你好"), 0),
        (RuntimeMode.OFF, "你好", _at_bot("你好"), 0),
        (RuntimeMode.SOCIAL_RUNTIME, "bq 开心", _at_bot("bq 开心"), 0),
        (RuntimeMode.SOCIAL_RUNTIME, "大家好", _text("大家好"), 0),
    ),
)
def test_bridge_claims_only_production_direct_groupmate_messages(
    tmp_path, mode, message, segments, expected_stops
):
    bridge = _bridge_for(tmp_path, mode=mode)
    event = _FakeAstrEvent("claim", message, segments=segments)

    asyncio.run(bridge.handle_event(event))

    assert event.stop_calls == expected_stops
```

Configure the bridge with `external_command_prefixes=("bq=astrbot.meme",)` so the external case exercises the real ownership policy.

- [x] **Step 2: Run the test and verify RED**

Run:

```bash
pytest -q tests/contracts/test_message_trace_bridge.py::test_bridge_claims_only_production_direct_groupmate_messages
```

Expected: FAIL because `stop_calls` remains `0` for the `SOCIAL_RUNTIME + @bot` case.

- [x] **Step 3: Implement the minimal ownership predicate**

In `AstrBotSocialRuntimeBridge`, add a pure predicate over the already resolved envelope:

```python
def _owns_host_response(self, event: SocialEventEnvelope) -> bool:
    if self._manager is None:
        return False
    payload = event.payload
    return bool(
        self._manager.group_mode(str(event.group_id or ""))
        is RuntimeMode.SOCIAL_RUNTIME
        and payload.get("direct_address")
        and payload.get("social_eligible") is not False
        and payload.get("interaction_owner") != "EXTERNAL_PLUGIN"
    )
```

Immediately after translation and persona-address resolution in `handle_event`, stop the AstrBot event before recording or ingesting it:

```python
translated = self._resolve_interaction(self.translator.translate(event))
if self._owns_host_response(translated):
    stop_event = getattr(event, "stop_event", None)
    if callable(stop_event):
        stop_event()
```

Do not stop SHADOW, OFF, non-target, ambient, or externally owned messages.

- [x] **Step 4: Run the focused test and verify GREEN**

Run:

```bash
pytest -q tests/contracts/test_message_trace_bridge.py::test_bridge_claims_only_production_direct_groupmate_messages
```

Expected: all parameterized cases PASS.

- [x] **Step 5: Run the small bridge regression set**

Run:

```bash
pytest -q tests/contracts/test_message_trace_bridge.py tests/shared/test_plugin_skeleton.py tests/scenarios/test_chat_mainline.py
```

Expected: PASS with no warnings or errors.

- [x] **Step 6: Check the patch and commit**

Run:

```bash
git diff --check
git status --short
git add groupmate/adapters/astrbot_bridge.py tests/contracts/test_message_trace_bridge.py docs/superpowers/plans/2026-08-25-groupmate-response-ownership.md
git commit -m "fix: enforce single group reply owner"
```

Expected: only the response-ownership implementation, focused tests, and this plan are committed; the existing untracked `analysis/` directory remains untouched.
