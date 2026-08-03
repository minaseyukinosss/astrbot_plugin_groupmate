# HostEventAdapter Phase B Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a static host-event adapter boundary and a default-off AIOCQHTTP poke adapter so a poke targeting the bot can enter Groupmate as an explicit synthetic interaction without affecting AstrBot commands, long-term memory, or the unified reply pipeline.

**Architecture:** Keep `HostEventGate` as the first ownership boundary, then run a static `HostEventAdapterRuntime` before ordinary message translation. An admitted poke becomes a whitelisted `SYSTEM_SYNTHETIC` `ChatMessage`, enters the existing per-group Actor, and is handled through explicit `HOST_INTERACTION` / `DIRECT_INTERACTION` semantics. Persona, OutputFirewall, Composer, DeliveryService, and Outbox remain the only reply path.

**Tech Stack:** Python 3.7-compatible dataclasses and ABCs, AstrBot AIOCQHTTP message components, existing Groupmate Actor/workflow/memory architecture, pytest, deterministic evaluation runner.

**Design Spec:** `docs/superpowers/specs/2026-08-03-host-event-adapter-phase-b-design.md`

---

## Scope

In scope:

- immutable host-event adapter manifest and result contracts;
- static adapter runtime with duplicate ownership rejection and fail-closed dispatch;
- default-off AIOCQHTTP `PokeEventAdapter`;
- gate-first ingress and explicit adapted-message Bridge entry;
- `HOST_INTERACTION` trigger and `DIRECT_INTERACTION` scene;
- direct, short, persona-governed poke participation;
- synthetic origin preservation, safe prompt rendering, and memory/session exclusions;
- configuration, status, README, focused tests, full tests, and deterministic evaluation.

Out of scope:

- third-party plugin integration;
- dynamic discovery;
- other production host interactions;
- schema migration;
- Tool Gateway, MCP, or action execution;
- automatic detection or shutdown of another poke-reply plugin.

## File Map

- Create `groupmate/host/event_adapters/base.py`: manifest, status, result, and adapter ABC.
- Create `groupmate/host/event_adapters/runtime.py`: static validation and fail-closed dispatch.
- Create `groupmate/host/event_adapters/poke.py`: AIOCQHTTP poke recognition and translation.
- Create `groupmate/host/event_adapters/__init__.py`: public adapter exports.
- Modify `groupmate/host/__init__.py`: host-layer public exports.
- Modify `groupmate/host/config.py`: `interaction_group.poke_enabled` parsing.
- Modify `_conf_schema.json`: default-off poke switch.
- Modify `main.py`: static adapter assembly.
- Modify `groupmate/host/event_gate.py`: explicit interaction dispositions.
- Modify `groupmate/host/ingress.py`: gate-first adapter dispatch.
- Modify `groupmate/host/bridge.py`: adapted-message entry and owner marking.
- Modify `groupmate/models.py`: explicit interaction trigger and scene.
- Modify `groupmate/engine/triggers.py`: strict synthetic interaction classification.
- Modify `groupmate/core/scenes.py`: direct interaction scene policy.
- Modify `groupmate/core/addressee.py`: sender reply target with blocked memory subject.
- Modify `groupmate/core/response_act.py`: deterministic poke response act.
- Modify `groupmate/engine/direct_pressure.py`: poke pressure accounting.
- Modify `groupmate/engine/participation.py`: direct interaction participation.
- Modify `groupmate/engine/runtime.py`: origin preservation and hard-turn behavior.
- Modify `groupmate/engine/workflow.py`: interaction decision text and live session exclusion.
- Modify `groupmate/core/history_format.py`: fixed prompt-safe interaction label.
- Modify `groupmate/memory/memory_writer.py`: no synthetic-triggered candidates.
- Modify tests named in each task; create focused adapter and flow test modules.
- Modify `README.md` and the design/plan status at closure.

---

### Task 1: Host Event Adapter Contract And Static Runtime

**Files:**
- Create: `groupmate/host/event_adapters/base.py`
- Create: `groupmate/host/event_adapters/runtime.py`
- Create: `groupmate/host/event_adapters/__init__.py`
- Modify: `groupmate/host/__init__.py`
- Create: `tests/test_host_event_adapter_runtime.py`

- [ ] **Step 1: Write failing contract and runtime tests**

Create `tests/test_host_event_adapter_runtime.py` with tests for immutable values, result invariants, duplicate ownership, ordered non-match dispatch, and exception isolation:

```python
from dataclasses import FrozenInstanceError, replace

import pytest

from groupmate.host.event_adapters import (
    HostEventAdapter,
    HostEventAdapterManifest,
    HostEventAdapterResult,
    HostEventAdapterRuntime,
    HostEventAdapterStatus,
)
from groupmate.models import ChatMessage, MessageOrigin


def synthetic_message():
    return ChatMessage(
        message_id="poke-1",
        group_id="g1",
        sender_id="u1",
        sender_name="Alice",
        text="",
        timestamp=100,
        segment_types=("poke",),
        origin=MessageOrigin.SYSTEM_SYNTHETIC,
        metadata={"interaction_kind": "poke"},
    )


class StaticAdapter(HostEventAdapter):
    def __init__(self, name, event_kind, result, calls):
        self.manifest = HostEventAdapterManifest(name, (event_kind,))
        self.result = result
        self.calls = calls
        super().__init__()

    def adapt(self, event):
        self.calls.append(event)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_contract_values_are_immutable_and_validate_result_shape():
    manifest = HostEventAdapterManifest("poke", ("poke",))
    admitted = HostEventAdapterResult.admitted(synthetic_message())
    assert admitted.status is HostEventAdapterStatus.ADMITTED
    with pytest.raises(FrozenInstanceError):
        manifest.name = "changed"
    with pytest.raises(ValueError, match="message"):
        HostEventAdapterResult(HostEventAdapterStatus.ADMITTED, "bad")
    with pytest.raises(ValueError, match="SYSTEM_SYNTHETIC"):
        HostEventAdapterResult.admitted(replace(
            synthetic_message(),
            origin=MessageOrigin.PLATFORM_REALTIME,
        ))
    with pytest.raises(ValueError, match="metadata"):
        HostEventAdapterResult.admitted(replace(
            synthetic_message(),
            metadata={"interaction_kind": "poke", "raw": object()},
        ))


def test_runtime_rejects_duplicate_name_and_event_kind():
    result = HostEventAdapterResult.not_matched()
    with pytest.raises(ValueError, match="duplicate adapter name"):
        HostEventAdapterRuntime((
            StaticAdapter("same", "poke", result, []),
            StaticAdapter("same", "reaction", result, []),
        ))
    with pytest.raises(ValueError, match="duplicate event kind"):
        HostEventAdapterRuntime((
            StaticAdapter("one", "poke", result, []),
            StaticAdapter("two", "poke", result, []),
        ))


def test_runtime_continues_after_not_matched_and_stops_after_claim():
    calls = []
    runtime = HostEventAdapterRuntime((
        StaticAdapter("one", "one", HostEventAdapterResult.not_matched(), calls),
        StaticAdapter("two", "two", HostEventAdapterResult.bypassed("disabled"), calls),
    ))
    event = object()
    result = runtime.adapt(event)
    assert result.status is HostEventAdapterStatus.BYPASSED
    assert calls == [event, event]


@pytest.mark.parametrize("value", [RuntimeError("boom"), object()])
def test_runtime_invalid_adapter_behavior_fails_closed(value):
    runtime = HostEventAdapterRuntime((StaticAdapter("bad", "poke", value, []),))
    result = runtime.adapt(object())
    assert result == HostEventAdapterResult.bypassed("adapter_error")
```

Use `dataclasses.replace()` rather than the illustrative `__dict__` reconstruction if the frozen message implementation makes the last assertion clearer.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
python3 -m pytest tests/test_host_event_adapter_runtime.py -q
```

Expected: collection fails because `groupmate.host.event_adapters` does not exist.

- [ ] **Step 3: Implement the minimal contracts**

Create `base.py` with Python 3.7-compatible validation:

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional, Tuple

from ...models import ChatMessage, MessageOrigin, StringEnum


@dataclass(frozen=True)
class HostEventAdapterManifest:
    name: str
    event_kinds: Tuple[str, ...]

    def __post_init__(self):
        name = str(self.name or "").strip()
        kinds = tuple(dict.fromkeys(
            str(item or "").strip().lower()
            for item in (self.event_kinds or ())
            if str(item or "").strip()
        ))
        if not name:
            raise ValueError("adapter name is required")
        if not kinds:
            raise ValueError("adapter event_kinds are required")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "event_kinds", kinds)


class HostEventAdapterStatus(StringEnum):
    NOT_MATCHED = "not_matched"
    BYPASSED = "bypassed"
    ADMITTED = "admitted"


@dataclass(frozen=True)
class HostEventAdapterResult:
    status: HostEventAdapterStatus
    reason_code: str
    message: Optional[ChatMessage] = None

    def __post_init__(self):
        status = self.status
        if not isinstance(status, HostEventAdapterStatus):
            status = HostEventAdapterStatus(str(status))
        reason = str(self.reason_code or "").strip()
        if not reason:
            raise ValueError("reason_code is required")
        if status is HostEventAdapterStatus.ADMITTED:
            if not isinstance(self.message, ChatMessage):
                raise ValueError("admitted result requires message")
            if self.message.origin is not MessageOrigin.SYSTEM_SYNTHETIC:
                raise ValueError("admitted message must be SYSTEM_SYNTHETIC")
            allowed = {"interaction_kind", "target_id", "source_adapter"}
            metadata = self.message.metadata
            if set(metadata) - allowed or any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in metadata.items()
            ):
                raise ValueError("admitted message metadata is not whitelisted")
        elif self.message is not None:
            raise ValueError("non-admitted result cannot contain message")
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "reason_code", reason)

    @classmethod
    def not_matched(cls):
        return cls(HostEventAdapterStatus.NOT_MATCHED, "not_matched")

    @classmethod
    def bypassed(cls, reason_code):
        return cls(HostEventAdapterStatus.BYPASSED, reason_code)

    @classmethod
    def admitted(cls, message):
        return cls(HostEventAdapterStatus.ADMITTED, "admitted", message)


class HostEventAdapter(ABC):
    manifest = None

    def __init__(self):
        if not isinstance(self.manifest, HostEventAdapterManifest):
            raise TypeError("adapter manifest is required")

    @abstractmethod
    def adapt(self, event: Any) -> HostEventAdapterResult:
        raise NotImplementedError
```

Create `runtime.py`:

```python
from typing import Iterable, Tuple

from .base import HostEventAdapter, HostEventAdapterResult, HostEventAdapterStatus


class HostEventAdapterRuntime:
    def __init__(self, adapters: Iterable[HostEventAdapter] = ()):
        self.adapters: Tuple[HostEventAdapter, ...] = tuple(adapters or ())
        names = set()
        kinds = set()
        for adapter in self.adapters:
            if not isinstance(adapter, HostEventAdapter):
                raise TypeError("adapters must contain HostEventAdapter values")
            if adapter.manifest.name in names:
                raise ValueError("duplicate adapter name: {}".format(adapter.manifest.name))
            names.add(adapter.manifest.name)
            for kind in adapter.manifest.event_kinds:
                if kind in kinds:
                    raise ValueError("duplicate event kind: {}".format(kind))
                kinds.add(kind)

    def adapt(self, event):
        for adapter in self.adapters:
            try:
                result = adapter.adapt(event)
            except Exception:
                return HostEventAdapterResult.bypassed("adapter_error")
            if not isinstance(result, HostEventAdapterResult):
                return HostEventAdapterResult.bypassed("adapter_error")
            if result.status is not HostEventAdapterStatus.NOT_MATCHED:
                return result
        return HostEventAdapterResult.not_matched()
```

Export these values from `event_adapters/__init__.py` and `groupmate/host/__init__.py`.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```bash
python3 -m pytest tests/test_host_event_adapter_runtime.py tests/test_host_event_gate.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```bash
git add groupmate/host/event_adapters groupmate/host/__init__.py tests/test_host_event_adapter_runtime.py
git commit -m "feat: add host event adapter contract"
```

---

### Task 2: Default-Off AIOCQHTTP Poke Adapter

**Files:**
- Create: `groupmate/host/event_adapters/poke.py`
- Modify: `groupmate/host/event_adapters/__init__.py`
- Create: `tests/test_poke_event_adapter.py`

- [ ] **Step 1: Write failing poke translation tests**

Create a local fake event exposing `get_group_id()`, `get_sender_id()`, `get_sender_name()`, `get_self_id()`, `message_obj`, and AIOCQHTTP raw data. Cover component and raw segment recognition:

```python
from types import SimpleNamespace

import pytest

from groupmate.host.event_adapters import (
    HostEventAdapterStatus,
    PokeEventAdapter,
)
from groupmate.models import MessageOrigin


class Poke:
    type = "poke"

    def __init__(self, qq):
        self.qq = qq


class Event:
    def __init__(self, target="bot", *, component=True, raw_segment=False):
        message = [Poke(target)] if component else []
        raw_message = {
            "message_id": "notice-1",
            "group_id": "g1",
            "user_id": "u1",
            "target_id": target,
            "time": 100,
            "sender": {"nickname": "Alice"},
            "message": (
                [{"type": "poke", "data": {"qq": target}}]
                if raw_segment else []
            ),
        }
        self.message_obj = SimpleNamespace(
            message_id="notice-1",
            timestamp=100,
            message=message,
            raw_message=raw_message,
        )

    def get_group_id(self): return "g1"
    def get_sender_id(self): return "u1"
    def get_sender_name(self): return "Alice"
    def get_self_id(self): return "bot"


def test_poke_is_bypassed_when_disabled():
    result = PokeEventAdapter(enabled=False).adapt(Event())
    assert result.status is HostEventAdapterStatus.BYPASSED
    assert result.reason_code == "disabled"


def test_poke_targeting_another_user_is_bypassed():
    result = PokeEventAdapter(enabled=True).adapt(Event("u2"))
    assert result.reason_code == "target_not_bot"


@pytest.mark.parametrize("event", [Event(component=True), Event(component=False, raw_segment=True)])
def test_poke_targeting_bot_becomes_whitelisted_synthetic_message(event):
    result = PokeEventAdapter(enabled=True).adapt(event)
    message = result.message
    assert result.status is HostEventAdapterStatus.ADMITTED
    assert message.origin is MessageOrigin.SYSTEM_SYNTHETIC
    assert message.text == ""
    assert message.segment_types == ("poke",)
    assert message.metadata == {
        "interaction_kind": "poke",
        "target_id": "bot",
        "source_adapter": "aiocqhttp_poke",
    }
    assert "raw" not in repr(message.metadata).lower()


def test_non_poke_is_not_matched_and_missing_identity_is_invalid():
    ordinary = Event(component=False)
    ordinary.message_obj.raw_message["message"] = [
        {"type": "text", "data": {"text": "hello"}}
    ]
    assert PokeEventAdapter(True).adapt(ordinary).status is HostEventAdapterStatus.NOT_MATCHED
    broken = Event()
    broken.get_group_id = lambda: ""
    assert PokeEventAdapter(True).adapt(broken).reason_code == "invalid_event"


def test_fallback_event_id_is_deterministic():
    event = Event()
    event.message_obj.message_id = ""
    event.message_obj.raw_message.pop("message_id")
    first = PokeEventAdapter(True).adapt(event).message.message_id
    second = PokeEventAdapter(True).adapt(event).message.message_id
    assert first == second
    assert first.startswith("poke-")
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
python3 -m pytest tests/test_poke_event_adapter.py -q
```

Expected: import fails because `PokeEventAdapter` does not exist.

- [ ] **Step 3: Implement minimal poke recognition and translation**

Create `poke.py`. Keep all extraction helpers private and whitelist every output field:

```python
from hashlib import sha256

from ...models import ChatMessage, MessageOrigin
from .base import HostEventAdapter, HostEventAdapterManifest, HostEventAdapterResult


class PokeEventAdapter(HostEventAdapter):
    manifest = HostEventAdapterManifest("aiocqhttp_poke", ("poke",))

    def __init__(self, enabled=False):
        self.enabled = bool(enabled)
        super().__init__()

    def adapt(self, event):
        matched, target_id = self._poke_target(event)
        if not matched:
            return HostEventAdapterResult.not_matched()
        if not self.enabled:
            return HostEventAdapterResult.bypassed("disabled")
        group_id = self._identifier(event, "get_group_id")
        sender_id = self._identifier(event, "get_sender_id")
        sender_name = self._identifier(event, "get_sender_name") or sender_id
        bot_id = self._identifier(event, "get_self_id")
        timestamp = self._timestamp(event)
        if not all((group_id, sender_id, bot_id, target_id, timestamp > 0)):
            return HostEventAdapterResult.bypassed("invalid_event")
        if target_id != bot_id:
            return HostEventAdapterResult.bypassed("target_not_bot")
        message_id = self._event_id(event, group_id, sender_id, target_id, timestamp)
        return HostEventAdapterResult.admitted(ChatMessage(
            message_id=message_id,
            group_id=group_id,
            sender_id=sender_id,
            sender_name=sender_name,
            text="",
            timestamp=timestamp,
            segment_types=("poke",),
            origin=MessageOrigin.SYSTEM_SYNTHETIC,
            platform="aiocqhttp",
            bot_id=bot_id,
            metadata={
                "interaction_kind": "poke",
                "target_id": bot_id,
                "source_adapter": "aiocqhttp_poke",
            },
        ))

    @classmethod
    def _poke_target(cls, event):
        message_obj = getattr(event, "message_obj", None)
        for component in getattr(message_obj, "message", ()) or ():
            component_type = str(getattr(component, "type", "") or "").lower()
            class_name = component.__class__.__name__.lower()
            if component_type != "poke" and class_name != "poke":
                continue
            target = getattr(component, "target_id", None)
            if target is None:
                target = getattr(component, "qq", None)
            return True, str(target or "").strip()
        raw = getattr(message_obj, "raw_message", None)
        if not isinstance(raw, dict):
            return False, ""
        for segment in raw.get("message", ()) or ():
            if not isinstance(segment, dict):
                continue
            if str(segment.get("type", "") or "").lower() != "poke":
                continue
            data = segment.get("data") or {}
            target = data.get("target_id", data.get("qq", ""))
            return True, str(target or "").strip()
        if str(raw.get("sub_type", "") or "").lower() == "poke":
            return True, str(raw.get("target_id", "") or "").strip()
        return False, ""

    @staticmethod
    def _identifier(event, method_name):
        method = getattr(event, method_name, None)
        if not callable(method):
            return ""
        try:
            return str(method() or "").strip()
        except Exception:
            return ""

    @staticmethod
    def _timestamp(event):
        message_obj = getattr(event, "message_obj", None)
        raw = getattr(message_obj, "raw_message", None)
        value = getattr(message_obj, "timestamp", 0)
        if not value and isinstance(raw, dict):
            value = raw.get("time", raw.get("timestamp", 0))
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _event_id(event, group_id, sender_id, target_id, timestamp):
        message_obj = getattr(event, "message_obj", None)
        raw = getattr(message_obj, "raw_message", None)
        value = getattr(message_obj, "message_id", "")
        if not value and isinstance(raw, dict):
            value = raw.get("message_id", raw.get("id", ""))
        if str(value or "").strip():
            return "poke-{}".format(str(value).strip())
        canonical = "|".join((
            "aiocqhttp", str(group_id), str(sender_id), str(target_id),
            str(int(timestamp)), "poke",
        ))
        return "poke-{}".format(
            sha256(canonical.encode("utf-8")).hexdigest()[:24]
        )
```

Do not copy raw data into the result.

Export `PokeEventAdapter`.

- [ ] **Step 4: Run adapter tests and verify GREEN**

Run:

```bash
python3 -m pytest tests/test_poke_event_adapter.py tests/test_host_event_adapter_runtime.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```bash
git add groupmate/host/event_adapters/poke.py groupmate/host/event_adapters/__init__.py tests/test_poke_event_adapter.py
git commit -m "feat: translate aiocqhttp poke events"
```

---

### Task 3: Configuration And Static Plugin Assembly

**Files:**
- Modify: `groupmate/host/config.py`
- Modify: `_conf_schema.json`
- Modify: `main.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_plugin_loading.py`

- [ ] **Step 1: Write failing configuration and assembly tests**

Update the public settings contract from six to seven values and assert safe defaults:

```python
def test_deployment_settings_contain_only_seven_public_values():
    settings = AstrBotConfigParser().parse({})
    assert {item.name for item in fields(DeploymentSettings)} == {
        "enabled_groups", "persona_aliases", "relationships",
        "generation_provider", "vision_enabled", "vision_provider",
        "poke_enabled", "diagnostics",
    }
    assert settings.poke_enabled is False


def test_poke_enabled_is_nested_and_explicit():
    settings = AstrBotConfigParser().parse({
        "interaction_group": {"poke_enabled": True}
    })
    assert settings.poke_enabled is True
    assert settings.diagnostics.unknown_keys == ()


def test_schema_exposes_exactly_seven_settings():
    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    items = {name for group in schema.values() for name in group.get("items", {})}
    assert items == {
        "enabled_groups", "persona_aliases", "relationships",
        "generation_provider", "vision_enabled", "vision_provider",
        "poke_enabled",
    }
    assert schema["interaction_group"]["items"]["poke_enabled"]["default"] is False
```

Extend the plugin loading subprocess fake to instantiate `GroupmatePlugin` with fake `AstrBotBridge`, `GroupmateWebAPI`, and config, then assert `plugin.ingress.event_adapters.adapters` contains exactly one `PokeEventAdapter` whose enabled state matches parsed configuration.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
python3 -m pytest tests/test_config.py tests/test_plugin_loading.py -q
```

Expected: failures because `poke_enabled` and `interaction_group` do not exist.

- [ ] **Step 3: Implement config parsing and assembly**

In `config.py`:

```python
_KNOWN_GROUPS = (
    "scope_group", "persona_group", "provider_group", "interaction_group"
)

@dataclass(frozen=True)
class DeploymentSettings:
    enabled_groups: Tuple[str, ...]
    persona_aliases: Tuple[Tuple[str, Tuple[str, ...]], ...]
    relationships: Tuple[Tuple[str, Tuple[RelationshipEntry, ...]], ...]
    generation_provider: str
    vision_enabled: bool
    vision_provider: str
    poke_enabled: bool
    diagnostics: ConfigDiagnostics

interaction_group = _as_mapping(source.get("interaction_group"))

return DeploymentSettings(
    enabled_groups=enabled_groups,
    persona_aliases=persona_aliases,
    relationships=relationships,
    generation_provider=str(
        provider_group.get("generation_provider", "") or ""
    ).strip(),
    vision_enabled=_boolean(provider_group.get("vision_enabled", True), True),
    vision_provider=str(
        provider_group.get("vision_provider", "") or ""
    ).strip(),
    poke_enabled=_boolean(
        interaction_group.get("poke_enabled", False), False
    ),
    diagnostics=ConfigDiagnostics(
        ignored_legacy_keys=diagnostics.ignored_legacy_keys,
        unknown_keys=diagnostics.unknown_keys,
        warnings=warnings,
    ),
)
```

Add an `_conf_schema.json` object group with a single bool setting and a hint that enabling it makes Groupmate the expected final poke reply owner.

In `main.py`, assemble explicitly:

```python
self.event_adapters = HostEventAdapterRuntime((
    PokeEventAdapter(enabled=self.config.poke_enabled),
))
self.ingress = AstrBotEventIngress(
    self.event_gate,
    self.bridge,
    event_adapters=self.event_adapters,
)
```

Do not scan packages or accept configured class paths.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```bash
python3 -m pytest tests/test_config.py tests/test_plugin_loading.py tests/test_plugin_page_assets.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```bash
git add groupmate/host/config.py _conf_schema.json main.py tests/test_config.py tests/test_plugin_loading.py
git commit -m "feat: configure static poke adapter"
```

---

### Task 4: Gate-First Ingress And Adapted Bridge Entry

**Files:**
- Modify: `groupmate/host/event_gate.py`
- Modify: `groupmate/host/ingress.py`
- Modify: `groupmate/host/bridge.py`
- Modify: `tests/test_host_event_ingress.py`
- Modify: `tests/test_native_wake_suppress.py`

- [ ] **Step 1: Write failing ownership and routing tests**

Extend `tests/test_host_event_ingress.py` with a recording adapter runtime and adapted Bridge method:

```python
class StaticAdapters:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def adapt(self, event):
        self.calls.append(event)
        return self.result


def test_command_gate_runs_before_adapters():
    event = Event()
    adapters = StaticAdapters(HostEventAdapterResult.admitted(synthetic_message()))
    bridge = RecordingBridge()
    ingress = AstrBotEventIngress(
        StaticGate(HostEventDisposition.HOST_COMMAND),
        bridge,
        event_adapters=adapters,
    )
    disposition = asyncio.run(ingress.handle_group_message(event))
    assert disposition is HostEventDisposition.HOST_COMMAND
    assert adapters.calls == []
    assert bridge.calls == []
    assert event.stop_calls == 0


def test_bypassed_interaction_never_reaches_bridge():
    adapters = StaticAdapters(HostEventAdapterResult.bypassed("disabled"))
    bridge = RecordingBridge()
    disposition = asyncio.run(AstrBotEventIngress(
        StaticGate(HostEventDisposition.GROUPMATE_MESSAGE),
        bridge,
        event_adapters=adapters,
    ).handle_group_message(Event()))
    assert disposition is HostEventDisposition.HOST_INTERACTION_BYPASS
    assert bridge.calls == []


def test_admitted_interaction_uses_adapted_bridge_path_only():
    event = Event()
    message = synthetic_message()
    bridge = RecordingBridge()
    adapters = StaticAdapters(HostEventAdapterResult.admitted(message))
    disposition = asyncio.run(AstrBotEventIngress(
        StaticGate(HostEventDisposition.GROUPMATE_MESSAGE),
        bridge,
        event_adapters=adapters,
    ).handle_group_message(event))
    assert disposition is HostEventDisposition.GROUPMATE_INTERACTION
    assert bridge.calls == [("adapted", event, message)]
    assert event.stop_calls == 0


def test_not_matched_preserves_existing_owner_path():
    event = Event()
    bridge = RecordingBridge(TurnOwner.GROUPMATE)
    adapters = StaticAdapters(HostEventAdapterResult.not_matched())
    ingress = AstrBotEventIngress(
        StaticGate(HostEventDisposition.GROUPMATE_MESSAGE),
        bridge,
        event_adapters=adapters,
    )
    disposition = asyncio.run(ingress.handle_group_message(event))
    assert disposition is HostEventDisposition.GROUPMATE_MESSAGE
    assert adapters.calls == [event]
    assert bridge.calls == [("owner", event), ("handle", event)]
```

In `tests/test_native_wake_suppress.py`, test `AstrBotBridge.handle_adapted_event()` with a fake actor: accepted interaction sets `call_llm=True`, submits the exact provided synthetic message, and uses `schedule=False` while paused.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
python3 -m pytest tests/test_host_event_ingress.py tests/test_native_wake_suppress.py -q
```

Expected: failures for missing dispositions, constructor argument, and Bridge method.

- [ ] **Step 3: Implement gate-first ingress**

Add:

```python
class HostEventDisposition(StringEnum):
    HOST_COMMAND = "host_command"
    HOST_WAKE_PREFIX = "host_wake_prefix"
    GROUPMATE_MESSAGE = "groupmate_message"
    IGNORE = "ignore"
    HOST_INTERACTION_BYPASS = "host_interaction_bypass"
    GROUPMATE_INTERACTION = "groupmate_interaction"
```

Update ingress while preserving default construction for existing callers:

```python
class AstrBotEventIngress:
    def __init__(self, gate, bridge, event_adapters=None):
        self.gate = gate
        self.bridge = bridge
        self.event_adapters = event_adapters or HostEventAdapterRuntime()

    async def handle_group_message(self, event):
        disposition = self.gate.classify(event)
        if disposition is not HostEventDisposition.GROUPMATE_MESSAGE:
            return disposition
        adapted = self.event_adapters.adapt(event)
        if adapted.status is HostEventAdapterStatus.BYPASSED:
            return HostEventDisposition.HOST_INTERACTION_BYPASS
        if adapted.status is HostEventAdapterStatus.ADMITTED:
            await self.bridge.handle_adapted_event(event, adapted.message)
            return HostEventDisposition.GROUPMATE_INTERACTION
        owner = self.bridge.apply_owner_to_event(event)
        if owner is TurnOwner.ASTRBOT_AGENT:
            await self.bridge.observe_only(event)
        else:
            await self.bridge.handle_event(event)
        return disposition
```

`enrich_request()` remains gate-only; synthetic interactions never create native LLM requests.

- [ ] **Step 4: Implement the Bridge entry without duplicating workflow logic**

Add one private owner marker and reuse it from existing ownership code:

```python
@staticmethod
def _mark_groupmate_owner(event):
    if hasattr(event, "should_call_llm"):
        event.should_call_llm(True)
    else:
        event.call_llm = True

async def handle_adapted_event(self, event, message):
    if not isinstance(message, ChatMessage):
        raise TypeError("message must be a ChatMessage")
    actor = await self._prepare_actor(event)
    if actor is None:
        return False
    self._mark_groupmate_owner(event)
    await actor.submit(message, schedule=not self.paused)
    return True
```

Make `apply_owner_to_event()` call `_mark_groupmate_owner()` for `TurnOwner.GROUPMATE`. Do not call `stop_event()` and do not retranslate the adapted message.

Add `poke_adapter` to `bridge.status()` using `settings.poke_enabled`.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run:

```bash
python3 -m pytest \
  tests/test_host_event_gate.py \
  tests/test_host_event_ingress.py \
  tests/test_native_wake_suppress.py \
  tests/test_plugin_loading.py -q
```

Expected: all selected tests pass and command tests still show zero `stop_event()` calls.

- [ ] **Step 6: Commit**

```bash
git add groupmate/host/event_gate.py groupmate/host/ingress.py groupmate/host/bridge.py tests/test_host_event_ingress.py tests/test_native_wake_suppress.py
git commit -m "feat: route adapted host interactions"
```

---

### Task 5: Explicit Interaction Trigger, Scene, Participation, And Actor Semantics

**Files:**
- Modify: `groupmate/models.py`
- Modify: `groupmate/engine/triggers.py`
- Modify: `groupmate/core/scenes.py`
- Modify: `groupmate/core/addressee.py`
- Modify: `groupmate/core/response_act.py`
- Modify: `groupmate/engine/direct_pressure.py`
- Modify: `groupmate/engine/participation.py`
- Modify: `groupmate/engine/runtime.py`
- Modify: `groupmate/engine/workflow.py`
- Modify: `tests/test_triggers.py`
- Modify: `tests/test_scenes.py`
- Modify: `tests/test_addressee.py`
- Modify: `tests/test_response_act.py`
- Modify: `tests/test_participation_decision.py`
- Modify: `tests/test_runtime.py`
- Modify: `tests/test_workflow.py`

- [ ] **Step 1: Write failing domain classification tests**

Add a shared synthetic poke fixture locally in each focused test module:

```python
def poke_message(**overrides):
    values = dict(
        message_id="poke-1", group_id="g1", sender_id="u1",
        sender_name="Alice", text="", timestamp=100,
        segment_types=("poke",), origin=MessageOrigin.SYSTEM_SYNTHETIC,
        metadata={"interaction_kind": "poke", "target_id": "bot",
                  "source_adapter": "aiocqhttp_poke"},
    )
    values.update(overrides)
    return ChatMessage(**values)
```

Tests must establish:

```python
assert TriggerRouter(("小爱",)).classify(poke_message()).kind is TriggerKind.HOST_INTERACTION
assert classify_scene(TriggerKind.HOST_INTERACTION, poke_message()) is InteractionScene.DIRECT_INTERACTION
assert policy_for_scene(InteractionScene.DIRECT_INTERACTION).quote_mode is QuoteMode.NEVER
assert is_hard_scene(InteractionScene.DIRECT_INTERACTION, TriggerKind.HOST_INTERACTION)
assert plan_response_act(
    InteractionScene.DIRECT_INTERACTION,
    reply_mode=ReplyMode.SHORT_SOCIAL,
    text="",
).act is ResponseAct.PLAYFUL_REPLY
```

Also assert unknown/mismatched synthetic metadata classifies as `IGNORE`.

For targeting, assert reply audience is the poke sender while memory subject is ambiguous with `no_personal_memory`; social target may identify the sender only for relationship posture lookup.

- [ ] **Step 2: Run classification tests and verify RED**

Run:

```bash
python3 -m pytest tests/test_triggers.py tests/test_scenes.py tests/test_addressee.py tests/test_response_act.py -q
```

Expected: enum members and interaction mappings are missing.

- [ ] **Step 3: Implement explicit domain values and strict router validation**

Add:

```python
class TriggerKind(StringEnum):
    IGNORE = "ignore"
    COMMAND = "command"
    NATIVE_DIRECT = "native_direct"
    ALIAS_DIRECT = "alias_direct"
    COPIED_AT = "copied_at"
    CONTINUATION = "continuation"
    ALIAS_MENTION = "alias_mention"
    CANDIDATE = "candidate"
    HOST_INTERACTION = "host_interaction"

class InteractionScene(StringEnum):
    DIRECT_ADDRESS = "direct_address"
    REPLY_TO_BOT = "reply_to_bot"
    ACTIVE_CONTINUATION = "active_continuation"
    SOCIAL_RESPONSE = "social_response"
    AMBIENT_CONTRIBUTION = "ambient_contribution"
    TASK_REQUEST = "task_request"
    DIRECT_INTERACTION = "direct_interaction"
```

At the beginning of `TriggerRouter.classify()`, after bot/command checks and before text routing:

```python
if message.origin is MessageOrigin.SYSTEM_SYNTHETIC:
    kind = str(message.metadata.get("interaction_kind", "") or "")
    if kind == "poke" and message.segment_types == ("poke",):
        return TriggerResult(TriggerKind.HOST_INTERACTION, "host_interaction:poke")
    return TriggerResult(TriggerKind.IGNORE, "invalid_host_interaction")
```

Map `HOST_INTERACTION` to `DIRECT_INTERACTION`; give that scene hard priority and `QuoteMode.NEVER`. Map the scene to `ResponseAct.PLAYFUL_REPLY` before generic empty-address handling.

- [ ] **Step 4: Implement targeting and participation semantics**

Add `HOST_INTERACTION` to `AddresseeResolver` hard triggers. After resolving reply/social targets, override only memory subject:

```python
if trigger is TriggerKind.HOST_INTERACTION:
    memory = _ambiguous(
        "synthetic_interaction",
        "no_personal_memory",
        evidence=evidence,
    )
```

Add `HOST_INTERACTION` to participation direct triggers. In direct-pressure bare-trigger detection, return true for host interaction so repeated pokes use the existing pressure window. The normal neutral poke remains `PLAYFUL_REPLY`; hostile/wary pester pressure can replace it with `BOUNDARY`. Quote mode remains `NEVER`.

Write tests that the first neutral poke speaks with `DIRECT_REQUIRED`, a friendly repeated poke stays playful, and a hostile repeated poke becomes a firm boundary.

- [ ] **Step 5: Preserve synthetic origin and hard-turn scheduling in Actor**

Change `_stamp_message()`:

```python
if message.origin in (
    MessageOrigin.BOT_DELIVERY,
    MessageOrigin.SYSTEM_SYNTHETIC,
):
    ingested_at = int(message.ingested_at or time.time())
    return message if message.ingested_at == ingested_at else replace(
        message, ingested_at=ingested_at
    )
```

Add `HOST_INTERACTION` to every hard-trigger tuple used for duplicate admission, hard-task retention, and queue handling. Do not add it to `_remember_continuation()`; the continuation grant set must remain only alias/native direct.

Add an Actor test proving a poke is evaluated immediately, retains `SYSTEM_SYNTHETIC` in window and memory, and creates no continuation grant after a sent outcome.

- [ ] **Step 6: Add workflow interaction decision semantics**

Treat `HOST_INTERACTION` as a direct trigger for fallback and urgency, but not as soft traffic. Extend `_build_decision()`:

```python
if trigger is TriggerKind.HOST_INTERACTION:
    reason_code = "host_interaction"
    contribution = "回应对方刚才对你的戳一戳互动，短而自然"
```

Add a workflow test using `RecordingGenerationModel` and `FakePlatform` to prove the response act is `PLAYFUL_REPLY`, the prompt passes through the Persona provider, the outbound reply uses Delivery/Outbox, and no direct quote is requested.

- [ ] **Step 7: Run focused domain and workflow tests**

Run:

```bash
python3 -m pytest \
  tests/test_triggers.py \
  tests/test_scenes.py \
  tests/test_addressee.py \
  tests/test_response_act.py \
  tests/test_direct_pressure.py \
  tests/test_participation_decision.py \
  tests/test_runtime.py \
  tests/test_workflow.py -q
```

Expected: all selected tests pass.

- [ ] **Step 8: Commit**

```bash
git add groupmate/models.py groupmate/engine/triggers.py groupmate/core/scenes.py groupmate/core/addressee.py groupmate/core/response_act.py groupmate/engine/direct_pressure.py groupmate/engine/participation.py groupmate/engine/runtime.py groupmate/engine/workflow.py tests/test_triggers.py tests/test_scenes.py tests/test_addressee.py tests/test_response_act.py tests/test_direct_pressure.py tests/test_participation_decision.py tests/test_runtime.py tests/test_workflow.py
git commit -m "feat: handle explicit host interactions"
```

---

### Task 6: Prompt, Session, Memory, And Projection Boundaries

**Files:**
- Modify: `groupmate/core/history_format.py`
- Modify: `groupmate/engine/workflow.py`
- Modify: `groupmate/memory/memory_writer.py`
- Modify: `tests/test_core_assembly.py`
- Modify: `tests/test_memory_writer.py`
- Modify: `tests/test_phase2_projections.py`
- Modify: `tests/test_workflow.py`

- [ ] **Step 1: Write failing prompt and memory boundary tests**

Add tests proving the fixed prompt label and absence of raw metadata:

```python
def test_synthetic_poke_uses_fixed_prompt_label():
    block = format_history_block((poke_message(),), {})
    assert "[互动：戳一戳]" in block
    assert "source_adapter" not in block
    assert "target_id" not in block
```

Add a MemoryWriter test where the topic latest message is `SYSTEM_SYNTHETIC` and `reply_text="我会帮你记住"`; assert `extract_candidates()` returns `([], {})` and no user or Bot-promise candidate is stored.

Add a workflow test proving a successful poke reply does not append either user or assistant turn to `GroupSession`.

Add a projection test that persists a synthetic poke plus Bot delivery, rebuilds state, and asserts the poke remains in `snapshot.messages` for short-term audit while neither message pair becomes a synthetic user session turn or creates continuation state.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
python3 -m pytest tests/test_core_assembly.py tests/test_memory_writer.py tests/test_phase2_projections.py tests/test_workflow.py -q
```

Expected: prompt renders `[图片]`, bot-promise extraction remains possible, or live session stores the reply.

- [ ] **Step 3: Implement fixed interaction formatting**

Add a private content formatter:

```python
def _message_content(message):
    if message.origin is MessageOrigin.SYSTEM_SYNTHETIC:
        labels = {"poke": "[互动：戳一戳]"}
        return labels.get(str(message.metadata.get("interaction_kind", "")), "[互动]")
    content = message.text or "[图片]"
    if message.image_urls and message.text:
        content += " [图片]"
    return content
```

Use it from `format_history_block()`. Do not interpolate metadata values.

- [ ] **Step 4: Exclude synthetic-triggered long-term and session writes**

At the start of `MemoryWriter.extract_candidates()` after selecting active messages:

```python
latest = active[-1] if active else None
if latest is not None and latest.origin is MessageOrigin.SYSTEM_SYNTHETIC:
    return [], {}
```

At the start of `CognitiveWorkflow._remember_session_turns()`:

```python
latest = topic.latest
if latest is not None and latest.origin is MessageOrigin.SYSTEM_SYNTHETIC:
    return
```

Keep the existing `StateProjector._session_turns()` synthetic skip. Do not add a database migration or delete synthetic ledger messages.

- [ ] **Step 5: Run focused boundary tests and verify GREEN**

Run:

```bash
python3 -m pytest \
  tests/test_core_assembly.py \
  tests/test_memory_writer.py \
  tests/test_phase2_projections.py \
  tests/test_workflow.py \
  tests/test_social_events.py -q
```

Expected: all selected tests pass; existing text memories and sessions remain unchanged.

- [ ] **Step 6: Commit**

```bash
git add groupmate/core/history_format.py groupmate/engine/workflow.py groupmate/memory/memory_writer.py tests/test_core_assembly.py tests/test_memory_writer.py tests/test_phase2_projections.py tests/test_workflow.py
git commit -m "feat: isolate synthetic interaction state"
```

---

### Task 7: End-To-End Regression, Documentation, And Closure

**Files:**
- Create: `tests/test_poke_interaction_flow.py`
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-07-31-groupmate-extension-spi-design.md`
- Modify: `docs/superpowers/specs/2026-08-03-host-event-adapter-phase-b-design.md`
- Modify: `docs/superpowers/plans/2026-08-03-host-event-adapter-phase-b.md`

- [ ] **Step 1: Write the end-to-end host interaction regression test**

Build one test with a real `HostEventGate`, `HostEventAdapterRuntime`, `PokeEventAdapter`, and `AstrBotEventIngress`, plus a narrow recording Bridge/Actor seam. Verify in one flow:

- `/取名` returns `HOST_COMMAND`, does not call adapters, does not mutate `call_llm`, and does not call `stop_event()`;
- disabled poke returns `HOST_INTERACTION_BYPASS` and stores nothing;
- enabled poke targeting another member bypasses;
- enabled poke targeting the bot produces one synthetic message and sets only Groupmate owner suppression;
- a following normal text event still follows the existing owner path after an adapter exception;
- pause passes `schedule=False` to the Actor and sends nothing.

Use separate tests if needed to keep each assertion focused; the file is an integration boundary, not a monolithic scenario.

- [ ] **Step 2: Run the end-to-end and host regression group**

Run:

```bash
python3 -m pytest \
  tests/test_host_event_adapter_runtime.py \
  tests/test_poke_event_adapter.py \
  tests/test_poke_interaction_flow.py \
  tests/test_host_event_gate.py \
  tests/test_host_event_ingress.py \
  tests/test_native_wake_suppress.py \
  tests/test_plugin_loading.py \
  tests/test_config.py -q
```

Expected: all selected tests pass.

- [ ] **Step 3: Run core regression groups**

Run:

```bash
python3 -m pytest \
  tests/test_triggers.py \
  tests/test_scenes.py \
  tests/test_addressee.py \
  tests/test_response_act.py \
  tests/test_direct_pressure.py \
  tests/test_participation_decision.py \
  tests/test_runtime.py \
  tests/test_phase1_runtime.py \
  tests/test_workflow.py \
  tests/test_memory_writer.py \
  tests/test_social_events.py \
  tests/test_phase2_projections.py -q
```

Expected: all selected tests pass.

- [ ] **Step 4: Run full automated verification**

Run:

```bash
python3 -m pytest -q
python3 -m eval.runner --mode deterministic --output eval/results/phase-b.json
git diff --check
```

Expected:

- full pytest passes;
- deterministic evaluation reports 120/120 completed with no safety regression;
- `git diff --check` prints no output.

- [ ] **Step 5: Run residual architecture scans**

Run:

```bash
rg -n "stop_event\(|should_call_llm\(|send\(" groupmate/host/event_adapters
rg -n "raw|message_obj|AstrMessageEvent|Context|DeliveryService|PlatformPort" groupmate/host/event_adapters
rg -n "HOST_INTERACTION|DIRECT_INTERACTION|SYSTEM_SYNTHETIC" groupmate tests
rg -n "PokeEventAdapter|HostEventAdapterRuntime" main.py groupmate/host tests
```

Expected:

- no adapter calls event-control or send methods;
- raw host access exists only inside `poke.py` extraction and is never placed in result metadata;
- explicit interaction semantics appear in the planned core boundaries and tests;
- production assembly is static and occurs only in `main.py`.

- [ ] **Step 6: Update documentation and status**

Update README configuration/status and architecture summary:

- `interaction_group.poke_enabled`, default `false`;
- enabling it assigns Groupmate expected final poke reply ownership;
- another direct poke reply plugin must be disabled or service-only;
- Phase A and Phase B are implemented; concrete external Integration Adapters remain Phase C.

Mark the Phase B design status implemented only after all verification succeeds. Mark every completed plan checkbox and add exact completion evidence with test counts and evaluation output.

- [ ] **Step 7: Re-run documentation checks**

Run:

```bash
rg -n "T[B]D|T[O]DO|待[定]" README.md docs/superpowers/specs/2026-08-03-host-event-adapter-phase-b-design.md docs/superpowers/plans/2026-08-03-host-event-adapter-phase-b.md
git diff --check
git status --short
```

Expected: no placeholders, no whitespace errors, and only planned files changed.

- [ ] **Step 8: Commit closure**

```bash
git add README.md docs/superpowers/specs/2026-07-31-groupmate-extension-spi-design.md docs/superpowers/specs/2026-08-03-host-event-adapter-phase-b-design.md docs/superpowers/plans/2026-08-03-host-event-adapter-phase-b.md tests/test_poke_interaction_flow.py
git commit -m "docs: close host event adapter phase b"
```

---

## Completion Evidence Requirements

At closure, replace this section with the exact focused host test count, focused core
test count, full pytest count, deterministic evaluation summary, host pause result,
Phase 2 projection result, residual scan result, and `git diff --check` result. Do not
mark the plan complete without command output from the current implementation commit.

## Design-to-Task Traceability

- Contract, immutable results, duplicate ownership, fail closed: Task 1.
- AIOCQHTTP poke recognition and whitelist translation: Task 2.
- Default-off configuration and static assembly: Task 3.
- Command-first ownership and no `stop_event()`: Task 4.
- Explicit trigger, scene, target, pressure, participation, and Actor semantics: Task 5.
- Prompt safety, no long-term memory, no synthetic session restoration: Task 6.
- Unique pipeline regression, pause, projections, full tests, evaluation, and docs: Task 7.

## Implementation Notes

- Keep all new production code compatible with Python 3.7; do not use built-in generic syntax such as `tuple[str, ...]` in runtime code.
- Do not add lifecycle methods to stateless event adapters.
- Do not make `OneBotTranslator` own special-event reply semantics.
- Do not add `HOST_INTERACTION` to continuation grant triggers.
- Do not persist arbitrary metadata or raw AstrBot objects.
- Do not claim automatic compatibility with a third-party poke plugin.
- Do not push or merge as part of task commits unless the user separately chooses a branch completion option.
