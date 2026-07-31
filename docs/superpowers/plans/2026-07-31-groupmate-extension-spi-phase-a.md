# Groupmate Extension SPI Phase A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a static, lifecycle-aware `CapabilityProvider` SPI so Groupmate-owned abilities and future integration adapters can be registered without changing workflow, persona, or delivery code.

**Architecture:** Keep `CapabilityRegistry` as the static spec catalog and `CapabilityGovernor` as the only runtime execution boundary. Add a Python 3.7-compatible provider base class, immutable health value, and `CapabilityProviderRuntime` that starts providers, samples health once, creates `CapabilitySpec` values, and closes providers in reverse order. Migrate built-in vision and external handoff behind this SPI while preserving their existing compatibility helpers.

**Tech Stack:** Python 3.7-compatible ABCs and dataclasses, asyncio capability executors, pytest, existing Groupmate capability contracts and AstrBot bridge.

**Design Spec:** `docs/superpowers/specs/2026-07-31-groupmate-extension-spi-design.md`

---

## Scope

This plan implements Phase A only.

In scope:

- `CapabilityProvider` base contract;
- immutable startup `CapabilityHealth`;
- static `CapabilityProviderRuntime` lifecycle and registry assembly;
- built-in vision and external handoff providers;
- Bridge-owned provider runtimes and deterministic close order;
- compatibility for existing `vision_spec()` / `external_handoff_spec()` callers.

Out of scope:

- `HostEventAdapter` and poke events;
- dynamic provider discovery or Python entry-point scanning;
- external AstrBot plugin integration;
- Tool Gateway, MCP, action execution, background health refresh;
- configuration for arbitrary providers.

## File Map

- Create `groupmate/capabilities/provider.py`: provider ABC, health value, and provider-to-spec helper.
- Create `groupmate/capabilities/provider_runtime.py`: static lifecycle manager and registry assembly.
- Create `groupmate/capabilities/providers/__init__.py`: built-in provider exports.
- Create `groupmate/capabilities/providers/vision.py`: built-in vision provider.
- Create `groupmate/capabilities/providers/external_handoff.py`: built-in handoff provider.
- Modify `groupmate/capabilities/builtin.py`: compatibility exports and spec factories.
- Modify `groupmate/capabilities/__init__.py`: public SPI exports.
- Modify `groupmate/host/bridge.py`: build and close per-group provider runtimes.
- Create `tests/test_capability_provider.py`: contract tests.
- Create `tests/test_capability_provider_runtime.py`: lifecycle and assembly tests.
- Modify `tests/test_builtin_capabilities.py`: built-in provider compatibility tests.
- Modify `tests/test_native_wake_suppress.py`: Bridge runtime wiring and close tests.

---

### Task 1: Provider Contract And Health

**Files:**
- Create: `groupmate/capabilities/provider.py`
- Modify: `groupmate/capabilities/__init__.py`
- Create: `tests/test_capability_provider.py`

- [ ] **Step 1: Write failing provider contract tests**

Add tests covering immutable health, validation, default lifecycle methods, manifest validation, and required-information defaults:

```python
import asyncio
from dataclasses import FrozenInstanceError

import pytest

from groupmate.capabilities import (
    CapabilityHealth,
    CapabilityManifest,
    CapabilityPermission,
    CapabilityProvider,
    CapabilityRequest,
    CapabilityResult,
    CapabilityStatus,
)


class EchoProvider(CapabilityProvider):
    manifest = CapabilityManifest(
        name="echo",
        version="1.0.0",
        permission_profile=(CapabilityPermission.VISION_READ,),
    )

    async def execute(self, request):
        return CapabilityResult(
            CapabilityStatus.SUCCESS,
            request.capability_name,
            facts=(request.message_text,),
        )


def test_health_is_immutable_and_validated():
    health = CapabilityHealth(True, "ready", 100)
    assert health.available is True
    assert health.reason_code == "ready"
    assert health.checked_at == 100
    with pytest.raises(FrozenInstanceError):
        health.available = False


def test_provider_defaults_are_safe():
    provider = EchoProvider()
    provider.start()
    assert provider.health() == CapabilityHealth(True, "ready", 0)
    assert provider.required_information(CapabilityRequest("echo")) == ()
    result = asyncio.run(provider.execute(CapabilityRequest("echo", "hello")))
    assert result.facts == ("hello",)
    provider.close()


def test_provider_requires_manifest_and_execute():
    class MissingManifest(CapabilityProvider):
        async def execute(self, request):
            raise AssertionError(request)

    with pytest.raises(TypeError):
        MissingManifest()
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
python3 -m pytest tests/test_capability_provider.py -q
```

Expected: collection fails because `CapabilityHealth` and `CapabilityProvider` do not exist.

- [ ] **Step 3: Implement the minimal provider contract**

Create `groupmate/capabilities/provider.py` with Python 3.7-compatible ABCs:

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Sequence

from .contracts import (
    CapabilityManifest,
    CapabilityRequest,
    CapabilityResult,
)


@dataclass(frozen=True)
class CapabilityHealth:
    available: bool
    reason_code: str = "ready"
    checked_at: int = 0

    def __post_init__(self):
        if not isinstance(self.available, bool):
            raise TypeError("available must be a bool")
        reason = str(self.reason_code or "").strip()
        if not reason:
            raise ValueError("reason_code is required")
        object.__setattr__(self, "reason_code", reason)
        object.__setattr__(self, "checked_at", int(self.checked_at))


class CapabilityProvider(ABC):
    manifest = None

    def __init__(self):
        if not isinstance(self.manifest, CapabilityManifest):
            raise TypeError("provider manifest is required")

    def start(self):
        return None

    def health(self):
        return CapabilityHealth(True, "ready", 0)

    def required_information(self, request):
        if not isinstance(request, CapabilityRequest):
            raise TypeError("request must be a CapabilityRequest")
        return ()

    @abstractmethod
    async def execute(self, request: CapabilityRequest) -> CapabilityResult:
        raise NotImplementedError

    def close(self):
        return None
```

Export `CapabilityHealth` and `CapabilityProvider` from `groupmate/capabilities/__init__.py`.

- [ ] **Step 4: Run contract tests and verify GREEN**

Run:

```bash
python3 -m pytest tests/test_capability_provider.py tests/test_capability_contracts.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```bash
git add groupmate/capabilities/provider.py groupmate/capabilities/__init__.py tests/test_capability_provider.py
git commit -m "feat: add capability provider contract"
```

---

### Task 2: Static Provider Runtime

**Files:**
- Create: `groupmate/capabilities/provider_runtime.py`
- Modify: `groupmate/capabilities/__init__.py`
- Create: `tests/test_capability_provider_runtime.py`

- [ ] **Step 1: Write failing lifecycle and assembly tests**

Cover explicit startup, health gating, duplicate rejection, cancellation-safe execution through Governor, reverse close order, and idempotent close:

```python
import asyncio

import pytest

from groupmate.capabilities import (
    CapabilityContext,
    CapabilityGovernor,
    CapabilityHealth,
    CapabilityManifest,
    CapabilityPermission,
    CapabilityProvider,
    CapabilityProviderRuntime,
    CapabilityRequest,
    CapabilityResult,
    CapabilityStatus,
)


def _manifest(name):
    return CapabilityManifest(
        name=name,
        version="1.0.0",
        permission_profile=(CapabilityPermission.VISION_READ,),
    )


class RecordingProvider(CapabilityProvider):
    def __init__(self, name, events, available=True):
        self.manifest = _manifest(name)
        self.events = events
        self.available = available
        self.calls = 0
        super().__init__()

    def start(self):
        self.events.append("start:" + self.manifest.name)

    def health(self):
        return CapabilityHealth(self.available, "ready" if self.available else "offline", 100)

    async def execute(self, request):
        self.calls += 1
        return CapabilityResult(CapabilityStatus.SUCCESS, request.capability_name, facts=("ok",))

    def close(self):
        self.events.append("close:" + self.manifest.name)


def test_runtime_starts_registers_and_closes_in_reverse_order():
    events = []
    runtime = CapabilityProviderRuntime(
        (RecordingProvider("one", events), RecordingProvider("two", events))
    )
    assert tuple(item.name for item in runtime.registry.manifests()) == ("one", "two")
    runtime.close()
    runtime.close()
    assert events == ["start:one", "start:two", "close:two", "close:one"]


def test_unhealthy_provider_is_registered_but_not_executed():
    provider = RecordingProvider("offline", [], available=False)
    runtime = CapabilityProviderRuntime((provider,))
    result = asyncio.run(runtime.registry.execute(CapabilityRequest("offline")))
    assert result.status is CapabilityStatus.UNSUPPORTED
    assert result.error_code == "capability_unavailable"
    assert provider.calls == 0


def test_duplicate_manifest_name_fails_before_second_start():
    events = []
    with pytest.raises(ValueError):
        CapabilityProviderRuntime(
            (RecordingProvider("same", events), RecordingProvider("same", events))
        )
    assert events == []
```

Also add a startup-failure test that verifies a failing provider is registered as unavailable with reason `start_error`, while already-started providers still close normally.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
python3 -m pytest tests/test_capability_provider_runtime.py -q
```

Expected: collection fails because `CapabilityProviderRuntime` does not exist.

- [ ] **Step 3: Implement runtime assembly**

Create `groupmate/capabilities/provider_runtime.py`:

```python
from typing import Dict, Iterable, Tuple

from .contracts import CapabilityManifest
from .provider import CapabilityHealth, CapabilityProvider
from .registry import CapabilityRegistry, CapabilitySpec


class CapabilityProviderRuntime:
    def __init__(self, providers: Iterable[CapabilityProvider] = ()) -> None:
        self.registry = CapabilityRegistry()
        self._providers: Tuple[CapabilityProvider, ...] = tuple(providers or ())
        self._started = []
        self._health: Dict[str, CapabilityHealth] = {}
        self._closed = False
        self._validate_providers()
        self._start_and_register()

    def _validate_providers(self):
        seen = set()
        for provider in self._providers:
            if not isinstance(provider, CapabilityProvider):
                raise TypeError("providers must contain CapabilityProvider values")
            if not isinstance(provider.manifest, CapabilityManifest):
                raise TypeError("provider manifest is required")
            if provider.manifest.name in seen:
                raise ValueError("duplicate provider: {}".format(provider.manifest.name))
            seen.add(provider.manifest.name)

    def _start_and_register(self):
        for provider in self._providers:
            try:
                provider.start()
                self._started.append(provider)
                health = provider.health()
                if not isinstance(health, CapabilityHealth):
                    raise TypeError("provider health must be a CapabilityHealth")
            except Exception:
                health = CapabilityHealth(False, "start_error", 0)
            self._health[provider.manifest.name] = health
            self.registry.register(
                CapabilitySpec(
                    provider.manifest,
                    provider.execute,
                    required_information=provider.required_information,
                    available=health.available,
                )
            )

    def health(self, capability_name):
        return self._health[capability_name]

    def close(self):
        if self._closed:
            return
        self._closed = True
        for provider in reversed(self._started):
            try:
                provider.close()
            except Exception:
                continue
```

Export `CapabilityProviderRuntime` from `groupmate/capabilities/__init__.py`.

- [ ] **Step 4: Run runtime and registry tests**

Run:

```bash
python3 -m pytest tests/test_capability_provider_runtime.py tests/test_capability_registry.py tests/test_capability_governor.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```bash
git add groupmate/capabilities/provider_runtime.py groupmate/capabilities/__init__.py tests/test_capability_provider_runtime.py
git commit -m "feat: add static capability provider runtime"
```

---

### Task 3: Migrate Built-In Providers

**Files:**
- Create: `groupmate/capabilities/providers/__init__.py`
- Create: `groupmate/capabilities/providers/vision.py`
- Create: `groupmate/capabilities/providers/external_handoff.py`
- Modify: `groupmate/capabilities/builtin.py`
- Modify: `groupmate/capabilities/__init__.py`
- Modify: `tests/test_builtin_capabilities.py`

- [ ] **Step 1: Write failing built-in provider tests**

Add assertions that `VisionProvider` and `ExternalHandoffProvider` implement `CapabilityProvider`, expose their current manifests, report health without executing, and preserve existing result semantics:

```python
def test_vision_provider_uses_provider_spi():
    provider = VisionProvider(StaticVision("图片描述"))
    assert isinstance(provider, CapabilityProvider)
    assert provider.manifest.name == "vision"
    assert provider.health().available is True


def test_disabled_vision_provider_reports_unavailable():
    provider = VisionProvider(None)
    assert provider.health().available is False
    assert provider.health().reason_code == "vision_unavailable"


def test_external_handoff_provider_uses_provider_spi():
    provider = ExternalHandoffProvider(
        ExternalHandoffReason.EXTERNAL_ACTION_REQUIRED,
        ExternalHandoffTarget.CONFIGURED_SERVICE,
    )
    assert isinstance(provider, CapabilityProvider)
    assert provider.manifest.name == "external_handoff"
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
python3 -m pytest tests/test_builtin_capabilities.py -q
```

Expected: import fails because the provider classes do not exist.

- [ ] **Step 3: Move built-ins behind Provider SPI**

Implement `VisionProvider` and `ExternalHandoffProvider` with the same behavior currently held by `VisionCapability` and `ExternalHandoffCapability`. Each provider owns its immutable manifest, health, required-information matcher, and async `execute()` method.

Keep compatibility in `builtin.py`:

```python
VisionCapability = VisionProvider
ExternalHandoffCapability = ExternalHandoffProvider


def vision_spec(vision):
    return provider_spec(VisionProvider(vision))


def external_handoff_spec(reason, target):
    return provider_spec(ExternalHandoffProvider(reason, target))
```

Add `provider_spec(provider)` to `provider.py`; it validates health and returns a `CapabilitySpec` without owning lifecycle, for legacy tests and callers only. Runtime code must use `CapabilityProviderRuntime`.

- [ ] **Step 4: Run built-in, runtime, and workflow tests**

Run:

```bash
python3 -m pytest tests/test_builtin_capabilities.py tests/test_capability_provider_runtime.py tests/test_workflow.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```bash
git add groupmate/capabilities/providers groupmate/capabilities/builtin.py groupmate/capabilities/provider.py groupmate/capabilities/__init__.py tests/test_builtin_capabilities.py
git commit -m "refactor: migrate built-ins to provider spi"
```

---

### Task 4: Bridge Owns Provider Runtime

**Files:**
- Modify: `groupmate/host/bridge.py`
- Modify: `tests/test_native_wake_suppress.py`
- Modify: `tests/test_provider_resolution.py`

- [ ] **Step 1: Write failing Bridge lifecycle tests**

Add tests that each group workflow receives a registry and governor from one stored provider runtime, disabled vision remains registered as unavailable, and `bridge.close()` closes and clears all provider runtimes exactly once.

```python
def test_bridge_stores_provider_runtime_per_group(tmp_path):
    bridge = _bridge(tmp_path, vision_enabled=True)
    workflow = bridge._workflow_for("g1", bridge.persona_context)
    runtime = bridge._capability_runtimes["g1"]
    assert workflow.capabilities is runtime.registry
    assert workflow.capability_governor.registry is runtime.registry


def test_bridge_close_releases_provider_runtimes(tmp_path):
    async def scenario():
        bridge = _bridge(tmp_path, vision_enabled=True)
        bridge._workflow_for("g1", bridge.persona_context)
        runtime = bridge._capability_runtimes["g1"]
        await bridge.close()
        return runtime, bridge._capability_runtimes

    runtime, runtimes = asyncio.run(scenario())
    assert runtime.closed is True
    assert runtimes == {}
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
python3 -m pytest tests/test_native_wake_suppress.py::test_bridge_stores_provider_runtime_per_group tests/test_native_wake_suppress.py::test_bridge_close_releases_provider_runtimes tests/test_provider_resolution.py -q
```

Expected: failures because Bridge has no `_capability_runtimes`.

- [ ] **Step 3: Wire runtime into Bridge**

In `AstrBotBridge.__init__` add:

```python
self._capability_runtimes: Dict[str, CapabilityProviderRuntime] = {}
```

In `_workflow_for`, replace direct `CapabilityRegistry` and `vision_spec` assembly:

```python
provider_runtime = self._capability_runtimes.get(group_id)
if provider_runtime is None:
    provider_runtime = CapabilityProviderRuntime(
        (
            VisionProvider(
                vision if self.settings.vision_enabled else None
            ),
        )
    )
    self._capability_runtimes[group_id] = provider_runtime
capabilities = provider_runtime.registry
governor = CapabilityGovernor(capabilities)
```

In `close()`, after closing the group runtime and before closing memory:

```python
for provider_runtime in tuple(self._capability_runtimes.values()):
    provider_runtime.close()
self._capability_runtimes.clear()
```

- [ ] **Step 4: Run Bridge and host regression tests**

Run:

```bash
python3 -m pytest tests/test_native_wake_suppress.py tests/test_provider_resolution.py tests/test_host_event_gate.py tests/test_host_event_ingress.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```bash
git add groupmate/host/bridge.py tests/test_native_wake_suppress.py tests/test_provider_resolution.py
git commit -m "refactor: assemble capabilities through provider runtime"
```

---

### Task 5: Documentation And Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-07-31-groupmate-extension-spi-design.md`
- Modify: `docs/superpowers/plans/2026-07-31-groupmate-extension-spi-phase-a.md`

- [ ] **Step 1: Update architecture status**

Document that Phase A static Provider SPI is implemented, while HostEventAdapter, external plugin adapters, dynamic discovery, Tool Gateway, MCP, and actions remain unimplemented.

- [ ] **Step 2: Run focused verification**

Run:

```bash
python3 -m pytest \
  tests/test_capability_provider.py \
  tests/test_capability_provider_runtime.py \
  tests/test_builtin_capabilities.py \
  tests/test_capability_registry.py \
  tests/test_capability_governor.py \
  tests/test_workflow.py \
  tests/test_native_wake_suppress.py \
  tests/test_provider_resolution.py \
  -q
```

- [ ] **Step 3: Run full verification and residual scans**

Run:

```bash
python3 -m pytest -q
git diff --check
rg -n "CapabilityRegistry\(|vision_spec\(" groupmate/host groupmate/engine
rg -n "start\(|close\(" groupmate/capabilities/provider_runtime.py tests/test_capability_provider_runtime.py
```

Expected:

- full pytest suite passes;
- no production workflow or Bridge creates an unmanaged Registry;
- built-in compatibility helpers remain only in capability modules and tests;
- `git diff --check` has no output.

- [ ] **Step 4: Mark this plan complete and commit docs**

Add below the Goal line only after verification:

```markdown
**Status:** Complete; verified by focused provider tests, full pytest, residual scans, and `git diff --check`.
```

Commit:

```bash
git add README.md docs/superpowers/specs/2026-07-31-groupmate-extension-spi-design.md docs/superpowers/plans/2026-07-31-groupmate-extension-spi-phase-a.md
git commit -m "docs: close extension spi phase a"
```

---

## Self-Review

Spec coverage:

- Own-code-first internal providers: Tasks 1-3.
- Stable provider lifecycle and startup health sampling: Tasks 1-2.
- Registry remains static and Governor remains the only execution boundary: Tasks 2 and 4.
- Bridge is the explicit assembly root: Task 4.
- Built-in vision and handoff retain compatibility helpers: Task 3.
- External plugin adapters, event adapters, dynamic discovery and action execution remain out of scope.

Placeholder scan:

- No unresolved placeholder markers.
- Every implementation task has concrete files, test names, commands, expected failures, and commit boundaries.

Type consistency:

- `CapabilityProvider` uses synchronous lifecycle methods because current Bridge workflow construction is synchronous; provider execution remains async.
- `CapabilityProviderRuntime.registry` is the single Registry injected into Governor and Workflow.
- `CapabilityHealth` is sampled once during startup, matching the approved Phase A scope.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-31-groupmate-extension-spi-phase-a.md`.

Two execution options:

1. **Subagent-Driven (recommended)** - dispatch a fresh subagent per task and review between tasks.
2. **Inline Execution** - execute tasks in this session using `superpowers:executing-plans`, with checkpoints after each task.
