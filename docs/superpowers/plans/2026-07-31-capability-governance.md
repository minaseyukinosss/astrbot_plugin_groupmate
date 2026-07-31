# Capability Governance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a governed capability execution layer so every Groupmate internal ability has an explicit manifest, a safe execution context, media policy enforcement, deadlines, size checks, and a single invocation path before results reach persona/composer/delivery.

**Architecture:** Evolve the existing `CapabilityRequest`, `CapabilityResult`, `CapabilitySpec`, and `CapabilityRegistry` instead of creating a second plugin system. `CapabilityGovernor` becomes the only runtime execution entry point; `CapabilityRegistry` remains a static registry of explicitly declared specs. `CognitiveWorkflow` builds a `CapabilityContext` from persona/group/message/participation facts and passes capability results back through the existing PersonaAssembler, OutputFirewall, ResponseComposer, and DeliveryService chain.

**Tech Stack:** Python 3.7-compatible dataclasses, asyncio, pytest, existing Groupmate capability contracts, existing `BehaviorPolicy` / `MediaPolicy` / `BudgetTracker`.

**Design Spec:** `docs/superpowers/specs/2026-07-31-host-command-capability-boundary-design.md`

**Prerequisite:** `docs/superpowers/plans/2026-07-31-host-command-isolation.md` is complete and `main` passes `python3 -m pytest -q`.

---

## Scope

This plan implements **Capability Governance** only.

In scope:

- `CapabilityManifest`（能力清单）: stable identity, declared permissions, cost/latency class, failure policy, timeout, concurrency, and max result size.
- `CapabilityContext`（能力上下文）: persona/group/message/trace/deadline/permissions/media policy values only, no AstrBot event/context, memory store, platform, delivery, actor, or workflow objects.
- `CapabilityGovernor`（能力治理器）: validates registration, availability, permissions, deadline, timeout, concurrency, media policy, max result size, and failed-result structure before returning a `CapabilityResult`.
- Workflow wiring so `CognitiveWorkflow` calls `CapabilityGovernor.execute(...)`, not `CapabilityRegistry.execute(...)`.
- Built-in vision registration uses an explicit manifest and context permissions.
- Existing capability results still pass through persona, output guard, composer, and delivery.

Out of scope:

- Dynamic provider discovery.
- AstrBot Tool Gateway.
- MCP or external service adapters.
- Action execution.
- New slash commands.
- Provider lifecycle start/close/health beyond the static `available` flag already represented in `CapabilitySpec`.

---

## File Map

- Modify `groupmate/capabilities/contracts.py`: add manifest/context/value enums and context-only media policy.
- Modify `groupmate/capabilities/registry.py`: change `CapabilitySpec` to own a `CapabilityManifest`, keep explicit registration and existing support-resolution semantics.
- Create `groupmate/capabilities/governor.py`: implement governed execution around the registry.
- Modify `groupmate/capabilities/builtin.py`: make `vision_spec()` and `external_handoff_spec()` return manifest-backed specs.
- Modify `groupmate/capabilities/__init__.py`: export new contracts and governor.
- Modify `groupmate/engine/workflow.py`: accept/use `CapabilityGovernor`, build `CapabilityContext`, and remove direct registry execution from `_execute_capability()`.
- Modify `groupmate/host/bridge.py`: build a registry plus governor and inject both into `CognitiveWorkflow`.
- Modify `groupmate/engine/composer.py`: trust governor-filtered media but keep its existing final safety filter.
- Modify tests:
  - `tests/test_capability_contracts.py`
  - `tests/test_capability_registry.py`
  - Create `tests/test_capability_governor.py`
  - `tests/test_builtin_capabilities.py`
  - `tests/test_workflow.py`
  - `tests/test_native_wake_suppress.py`
  - `tests/test_provider_resolution.py`

---

## Execution Preflight

- [ ] **Step 1: Create or enter an isolated worktree**

Use `superpowers:using-git-worktrees` before executing this plan. Suggested branch:

```bash
git worktree add .worktrees/capability-governance -b feat/capability-governance
```

Expected: isolated worktree exists at `.worktrees/capability-governance`.

- [ ] **Step 2: Verify clean baseline**

Run:

```bash
python3 -m pytest -q
```

Expected: all tests pass, currently `620 passed`.

Stop if baseline fails. Do not mix baseline repair with this plan unless the failure is caused by this branch.

---

### Task 1: Manifest And Context Contracts

**Files:**
- Modify: `groupmate/capabilities/contracts.py`
- Modify: `tests/test_capability_contracts.py`

- [ ] **Step 1: Write manifest and context tests**

Append these tests to `tests/test_capability_contracts.py`:

```python
from groupmate.capabilities.contracts import (
    CapabilityCostClass,
    CapabilityFailurePolicy,
    CapabilityLatencyClass,
    CapabilityManifest,
    CapabilityMediaPolicy,
    CapabilityPermission,
    CapabilityContext,
)


def test_capability_manifest_is_immutable_and_declares_governance_fields():
    manifest = CapabilityManifest(
        name="vision",
        version="1.0.0",
        supported_intents=("image_understanding",),
        permission_profile=(CapabilityPermission.VISION_READ,),
        latency_class=CapabilityLatencyClass.INTERACTIVE,
        cost_class=CapabilityCostClass.METERED,
        failure_policy=CapabilityFailurePolicy.FAIL_CLOSED,
        max_result_size=512,
        default_timeout_seconds=3.5,
        max_concurrency=2,
    )

    assert manifest.name == "vision"
    assert manifest.version == "1.0.0"
    assert manifest.permission_profile == (CapabilityPermission.VISION_READ,)
    assert manifest.supported_intents == ("image_understanding",)
    assert manifest.max_result_size == 512
    assert manifest.default_timeout_seconds == 3.5
    assert manifest.max_concurrency == 2
    assert hash(manifest)

    with pytest.raises(FrozenInstanceError):
        manifest.name = "changed"


def test_capability_manifest_rejects_empty_permissions_and_bad_limits():
    with pytest.raises(ValueError, match="permission_profile"):
        CapabilityManifest(
            name="vision",
            version="1.0.0",
            permission_profile=(),
        )
    with pytest.raises(ValueError, match="max_result_size"):
        CapabilityManifest(
            name="vision",
            version="1.0.0",
            permission_profile=(CapabilityPermission.VISION_READ,),
            max_result_size=0,
        )
    with pytest.raises(ValueError, match="default_timeout_seconds"):
        CapabilityManifest(
            name="vision",
            version="1.0.0",
            permission_profile=(CapabilityPermission.VISION_READ,),
            default_timeout_seconds=0,
        )
    with pytest.raises(ValueError, match="max_concurrency"):
        CapabilityManifest(
            name="vision",
            version="1.0.0",
            permission_profile=(CapabilityPermission.VISION_READ,),
            max_concurrency=0,
        )


def test_capability_context_contains_only_safe_runtime_facts():
    context = CapabilityContext(
        persona_id="aemeath",
        group_id="g1",
        actor_id="u1",
        message_id="m1",
        trace_id="d1",
        deadline_at=123,
        allowed_permissions=(CapabilityPermission.VISION_READ,),
        media_policy=CapabilityMediaPolicy(capability_media_allowed=True),
    )
    field_names = {field.name for field in fields(CapabilityContext)}

    assert field_names == {
        "persona_id",
        "group_id",
        "actor_id",
        "message_id",
        "trace_id",
        "deadline_at",
        "allowed_permissions",
        "media_policy",
    }
    assert context.allowed_permissions == (CapabilityPermission.VISION_READ,)
    assert context.media_policy.capability_media_allowed is True
    assert not field_names.intersection(
        {
            "platform",
            "delivery_service",
            "memory",
            "memory_store",
            "workflow",
            "actor",
            "astrbot_context",
            "event",
        }
    )


def test_capability_media_policy_defaults_to_no_media():
    policy = CapabilityMediaPolicy()

    assert policy.capability_media_allowed is False
    assert policy.allowed_media_kinds == ()
    assert policy.allowed_safety_labels == ()
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
python3 -m pytest tests/test_capability_contracts.py -q
```

Expected: FAIL because `CapabilityManifest`, `CapabilityContext`, and related enums do not exist.

- [ ] **Step 3: Implement contract values**

In `groupmate/capabilities/contracts.py`, add these definitions after `CapabilityStatus`:

```python
class CapabilityPermission(StringEnum):
    VISION_READ = "vision.read"
    EXTERNAL_HANDOFF = "external.handoff"
    MEDIA_RESULT = "media.result"


class CapabilityLatencyClass(StringEnum):
    INLINE = "inline"
    INTERACTIVE = "interactive"
    BACKGROUND = "background"


class CapabilityCostClass(StringEnum):
    FREE = "free"
    METERED = "metered"
    EXPENSIVE = "expensive"


class CapabilityFailurePolicy(StringEnum):
    FAIL_CLOSED = "fail_closed"
    CLARIFY = "clarify"
    HANDOFF = "handoff"


@dataclass(frozen=True)
class CapabilityMediaPolicy:
    capability_media_allowed: bool = False
    allowed_media_kinds: Tuple[str, ...] = ()
    allowed_safety_labels: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        kinds = _clean_texts(self.allowed_media_kinds)
        labels = _clean_texts(self.allowed_safety_labels)
        object.__setattr__(self, "capability_media_allowed", bool(self.capability_media_allowed))
        object.__setattr__(self, "allowed_media_kinds", kinds)
        object.__setattr__(self, "allowed_safety_labels", labels)


@dataclass(frozen=True)
class CapabilityManifest:
    name: str
    version: str
    supported_intents: Tuple[str, ...] = ()
    permission_profile: Tuple[CapabilityPermission, ...] = ()
    latency_class: CapabilityLatencyClass = CapabilityLatencyClass.INTERACTIVE
    cost_class: CapabilityCostClass = CapabilityCostClass.FREE
    failure_policy: CapabilityFailurePolicy = CapabilityFailurePolicy.FAIL_CLOSED
    max_result_size: int = 2048
    default_timeout_seconds: float = 10.0
    max_concurrency: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", validate_capability_name(self.name))
        version = _clean_identifier(self.version)
        if not version:
            raise ValueError("capability manifest version is required")
        permissions = tuple(self.permission_profile or ())
        if not permissions:
            raise ValueError("permission_profile must declare at least one permission")
        if not all(isinstance(item, CapabilityPermission) for item in permissions):
            raise TypeError("permission_profile must contain CapabilityPermission values")
        if self.max_result_size <= 0:
            raise ValueError("max_result_size must be positive")
        if self.default_timeout_seconds <= 0:
            raise ValueError("default_timeout_seconds must be positive")
        if self.max_concurrency <= 0:
            raise ValueError("max_concurrency must be positive")
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "supported_intents", _clean_texts(self.supported_intents))
        object.__setattr__(self, "permission_profile", permissions)


@dataclass(frozen=True)
class CapabilityContext:
    persona_id: str
    group_id: str
    actor_id: str
    message_id: str
    trace_id: str
    deadline_at: int
    allowed_permissions: Tuple[CapabilityPermission, ...] = ()
    media_policy: CapabilityMediaPolicy = CapabilityMediaPolicy()

    def __post_init__(self) -> None:
        for field_name in ("persona_id", "group_id", "actor_id", "message_id", "trace_id"):
            value = _clean_identifier(getattr(self, field_name))
            if field_name in ("persona_id", "group_id", "trace_id") and not value:
                raise ValueError("{} is required".format(field_name))
            object.__setattr__(self, field_name, value)
        permissions = tuple(self.allowed_permissions or ())
        if not all(isinstance(item, CapabilityPermission) for item in permissions):
            raise TypeError("allowed_permissions must contain CapabilityPermission values")
        if not isinstance(self.media_policy, CapabilityMediaPolicy):
            raise TypeError("media_policy must be a CapabilityMediaPolicy")
        object.__setattr__(self, "deadline_at", int(self.deadline_at))
        object.__setattr__(self, "allowed_permissions", permissions)
```

- [ ] **Step 4: Run contract tests and verify GREEN**

Run:

```bash
python3 -m pytest tests/test_capability_contracts.py -q
```

Expected: all capability contract tests pass.

- [ ] **Step 5: Commit**

```bash
git add groupmate/capabilities/contracts.py tests/test_capability_contracts.py
git commit -m "feat: add capability governance contracts"
```

---

### Task 2: Manifest-Backed Registry

**Files:**
- Modify: `groupmate/capabilities/registry.py`
- Modify: `groupmate/capabilities/builtin.py`
- Modify: `groupmate/capabilities/__init__.py`
- Modify: `tests/test_capability_registry.py`
- Modify: `tests/test_builtin_capabilities.py`

- [ ] **Step 1: Write registry tests for manifest ownership**

Add these tests to `tests/test_capability_registry.py`:

```python
from groupmate.capabilities.contracts import (
    CapabilityManifest,
    CapabilityPermission,
)


def _manifest(name="echo", **overrides):
    values = {
        "name": name,
        "version": "1.0.0",
        "permission_profile": (CapabilityPermission.VISION_READ,),
        "default_timeout_seconds": 0.1,
    }
    values.update(overrides)
    return CapabilityManifest(**values)


def test_spec_owns_manifest_and_exposes_name_for_existing_callers():
    spec = CapabilitySpec(_manifest("echo"), _echo)

    assert spec.name == "echo"
    assert spec.manifest.name == "echo"
    assert spec.manifest.version == "1.0.0"


def test_registry_lists_registered_manifests_without_executors():
    registry = CapabilityRegistry()
    registry.register(CapabilitySpec(_manifest("echo"), _echo))

    manifests = registry.manifests()

    assert tuple(item.name for item in manifests) == ("echo",)
    assert all(not hasattr(item, "executor") for item in manifests)


def test_duplicate_manifest_name_is_rejected():
    registry = CapabilityRegistry()
    registry.register(CapabilitySpec(_manifest("echo"), _echo))

    with pytest.raises(ValueError, match="already registered"):
        registry.register(CapabilitySpec(_manifest("echo", version="2.0.0"), _echo))
```

Update every existing `CapabilitySpec("...", ...)` call in `tests/test_capability_registry.py` to pass a manifest first. Examples:

```python
CapabilitySpec("echo", _echo)
# becomes
CapabilitySpec(_manifest("echo"), _echo)

CapabilitySpec("slow", slow)
# becomes
CapabilitySpec(_manifest("slow"), slow)

CapabilitySpec("offline", _echo, available=False)
# becomes
CapabilitySpec(_manifest("offline"), _echo, available=False)
```

Add this test to `tests/test_builtin_capabilities.py`:

```python
def test_vision_spec_declares_manifest_for_governor():
    spec = vision_spec(NullVision("图片描述"))

    assert spec.manifest.name == "vision"
    assert spec.manifest.version
    assert spec.manifest.permission_profile
    assert spec.manifest.default_timeout_seconds > 0
    assert spec.manifest.max_result_size > 0
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
python3 -m pytest tests/test_capability_registry.py tests/test_builtin_capabilities.py -q
```

Expected: FAIL because `CapabilitySpec` still accepts a string name and has no `manifest` property.

- [ ] **Step 3: Change `CapabilitySpec` to store `CapabilityManifest`**

In `groupmate/capabilities/registry.py`, update imports:

```python
from .contracts import (
    CapabilityManifest,
    CapabilityRequest,
    CapabilityResult,
    CapabilityStatus,
    validate_capability_name,
)
```

Replace `CapabilitySpec` with:

```python
@dataclass(frozen=True)
class CapabilitySpec:
    manifest: CapabilityManifest
    executor: CapabilityExecutor
    required_information: Optional[InformationMatcher] = None
    available: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.manifest, CapabilityManifest):
            raise TypeError("capability manifest is required")
        if not callable(self.executor):
            raise TypeError("capability executor must be callable")
        if self.required_information is not None and not callable(
            self.required_information
        ):
            raise TypeError("required_information must be callable")
        if not isinstance(self.available, bool):
            raise TypeError("available must be a bool")

    @property
    def name(self) -> str:
        return self.manifest.name
```

Add to `CapabilityRegistry`:

```python
    def manifests(self) -> Tuple[CapabilityManifest, ...]:
        return tuple(spec.manifest for spec in self._specs.values())
```

Update typing import to include `Tuple`.

- [ ] **Step 4: Update built-in specs**

In `groupmate/capabilities/builtin.py`, import:

```python
from .contracts import (
    CapabilityCostClass,
    CapabilityFailurePolicy,
    CapabilityLatencyClass,
    CapabilityManifest,
    CapabilityPermission,
    CapabilityRequest,
    CapabilityResult,
    CapabilityStatus,
)
```

Update `vision_spec()`:

```python
def vision_spec(vision: Optional["VisionPort"]) -> CapabilitySpec:
    capability = VisionCapability(vision)
    manifest = CapabilityManifest(
        name=capability.name,
        version="1.0.0",
        supported_intents=("image_understanding",),
        permission_profile=(CapabilityPermission.VISION_READ,),
        latency_class=CapabilityLatencyClass.INTERACTIVE,
        cost_class=CapabilityCostClass.METERED,
        failure_policy=CapabilityFailurePolicy.FAIL_CLOSED,
        max_result_size=2048,
        default_timeout_seconds=10.0,
        max_concurrency=1,
    )
    return CapabilitySpec(
        manifest,
        capability,
        required_information=lambda request: (
            () if request.media_locators else ("media_locator",)
        ),
        available=vision is not None,
    )
```

Update `external_handoff_spec()`:

```python
def external_handoff_spec(
    reason: ExternalHandoffReason,
    target: ExternalHandoffTarget,
) -> CapabilitySpec:
    capability = ExternalHandoffCapability(reason, target)
    manifest = CapabilityManifest(
        name=capability.name,
        version="1.0.0",
        supported_intents=("external_handoff",),
        permission_profile=(CapabilityPermission.EXTERNAL_HANDOFF,),
        latency_class=CapabilityLatencyClass.INLINE,
        cost_class=CapabilityCostClass.FREE,
        failure_policy=CapabilityFailurePolicy.HANDOFF,
        max_result_size=512,
        default_timeout_seconds=2.0,
        max_concurrency=1,
    )
    return CapabilitySpec(manifest, capability)
```

- [ ] **Step 5: Export new contracts**

In `groupmate/capabilities/__init__.py`, export:

```python
CapabilityContext,
CapabilityCostClass,
CapabilityFailurePolicy,
CapabilityLatencyClass,
CapabilityManifest,
CapabilityMediaPolicy,
CapabilityPermission,
```

- [ ] **Step 6: Run registry and built-in tests**

Run:

```bash
python3 -m pytest tests/test_capability_registry.py tests/test_builtin_capabilities.py tests/test_capability_contracts.py -q
```

Expected: all selected tests pass.

- [ ] **Step 7: Commit**

```bash
git add groupmate/capabilities/registry.py groupmate/capabilities/builtin.py groupmate/capabilities/__init__.py tests/test_capability_registry.py tests/test_builtin_capabilities.py
git commit -m "refactor: require manifests for registered capabilities"
```

---

### Task 3: CapabilityGovernor Execution Boundary

**Files:**
- Create: `groupmate/capabilities/governor.py`
- Modify: `groupmate/capabilities/__init__.py`
- Create: `tests/test_capability_governor.py`

- [ ] **Step 1: Write governor tests**

Create `tests/test_capability_governor.py`:

```python
import asyncio

import pytest

from groupmate.capabilities import (
    CapabilityContext,
    CapabilityGovernor,
    CapabilityManifest,
    CapabilityMediaPolicy,
    CapabilityPermission,
    CapabilityRegistry,
    CapabilityRequest,
    CapabilityResult,
    CapabilitySpec,
    CapabilityStatus,
    MediaCandidate,
)


def _manifest(name="echo", **overrides):
    values = {
        "name": name,
        "version": "1.0.0",
        "permission_profile": (CapabilityPermission.VISION_READ,),
        "default_timeout_seconds": 0.1,
        "max_result_size": 256,
        "max_concurrency": 1,
    }
    values.update(overrides)
    return CapabilityManifest(**values)


def _context(**overrides):
    values = {
        "persona_id": "aemeath",
        "group_id": "g1",
        "actor_id": "u1",
        "message_id": "m1",
        "trace_id": "d1",
        "deadline_at": 200,
        "allowed_permissions": (CapabilityPermission.VISION_READ,),
        "media_policy": CapabilityMediaPolicy(
            capability_media_allowed=True,
            allowed_media_kinds=("image",),
            allowed_safety_labels=("safe",),
        ),
    }
    values.update(overrides)
    return CapabilityContext(**values)


def _request(name="echo", **overrides):
    values = {
        "capability_name": name,
        "message_text": "hello",
        "group_id": "g1",
        "actor_id": "u1",
        "message_id": "m1",
    }
    values.update(overrides)
    return CapabilityRequest(**values)


async def _echo(request):
    return CapabilityResult(
        CapabilityStatus.SUCCESS,
        request.capability_name,
        facts=(request.message_text,),
        user_text=request.message_text,
    )


def test_unregistered_capability_is_unsupported_without_executor():
    registry = CapabilityRegistry()
    governor = CapabilityGovernor(registry)

    result = asyncio.run(governor.execute(_request("missing"), _context(), now=100))

    assert result.status is CapabilityStatus.UNSUPPORTED
    assert result.error_code == "capability_not_registered"


def test_missing_permission_denies_before_executor_runs():
    calls = {"count": 0}

    async def executor(request):
        calls["count"] += 1
        return await _echo(request)

    registry = CapabilityRegistry()
    registry.register(CapabilitySpec(_manifest("echo"), executor))
    governor = CapabilityGovernor(registry)

    result = asyncio.run(
        governor.execute(
            _request("echo"),
            _context(allowed_permissions=()),
            now=100,
        )
    )

    assert result.status is CapabilityStatus.UNSUPPORTED
    assert result.error_code == "permission_denied"
    assert calls["count"] == 0


def test_deadline_expired_denies_before_executor_runs():
    calls = {"count": 0}

    async def executor(request):
        calls["count"] += 1
        return await _echo(request)

    registry = CapabilityRegistry()
    registry.register(CapabilitySpec(_manifest("echo"), executor))
    governor = CapabilityGovernor(registry)

    result = asyncio.run(governor.execute(_request("echo"), _context(deadline_at=99), now=100))

    assert result.status is CapabilityStatus.TIMEOUT
    assert result.error_code == "deadline_expired"
    assert calls["count"] == 0


def test_manifest_timeout_is_passed_to_registry():
    async def slow(_request):
        await asyncio.sleep(1)
        return CapabilityResult(CapabilityStatus.SUCCESS, "slow", facts=("done",))

    registry = CapabilityRegistry()
    registry.register(
        CapabilitySpec(
            _manifest("slow", default_timeout_seconds=0.001),
            slow,
        )
    )
    governor = CapabilityGovernor(registry)

    result = asyncio.run(governor.execute(_request("slow"), _context(), now=100))

    assert result.status is CapabilityStatus.TIMEOUT
    assert result.error_code == "execution_timeout"


def test_media_policy_strips_disallowed_media_but_keeps_facts():
    candidate = MediaCandidate(
        media_id="img-1",
        source="provider",
        locator="https://example.test/1.png",
        media_kind="image",
        semantic_label="preview",
        purpose="reply attachment",
        safety_label="safe",
    )

    async def executor(request):
        return CapabilityResult(
            CapabilityStatus.SUCCESS,
            request.capability_name,
            facts=("fact",),
            user_text="fact",
            media_candidates=(candidate,),
        )

    registry = CapabilityRegistry()
    registry.register(CapabilitySpec(_manifest("echo"), executor))
    governor = CapabilityGovernor(registry)

    result = asyncio.run(
        governor.execute(
            _request("echo"),
            _context(media_policy=CapabilityMediaPolicy(capability_media_allowed=False)),
            now=100,
        )
    )

    assert result.status is CapabilityStatus.SUCCESS
    assert result.facts == ("fact",)
    assert result.media_candidates == ()


def test_result_size_limit_fails_closed():
    async def executor(request):
        return CapabilityResult(
            CapabilityStatus.SUCCESS,
            request.capability_name,
            facts=("x" * 20,),
            user_text="x" * 20,
        )

    registry = CapabilityRegistry()
    registry.register(CapabilitySpec(_manifest("echo", max_result_size=10), executor))
    governor = CapabilityGovernor(registry)

    result = asyncio.run(governor.execute(_request("echo"), _context(), now=100))

    assert result.status is CapabilityStatus.FAILED
    assert result.error_code == "result_too_large"


def test_external_cancellation_is_not_swallowed():
    async def executor(_request):
        raise asyncio.CancelledError()

    registry = CapabilityRegistry()
    registry.register(CapabilitySpec(_manifest("cancelled"), executor))
    governor = CapabilityGovernor(registry)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(governor.execute(_request("cancelled"), _context(), now=100))
```

- [ ] **Step 2: Run governor tests and verify RED**

Run:

```bash
python3 -m pytest tests/test_capability_governor.py -q
```

Expected: collection FAIL because `CapabilityGovernor` does not exist.

- [ ] **Step 3: Implement governor**

Create `groupmate/capabilities/governor.py`:

```python
"""Governed capability execution boundary."""

from __future__ import annotations

import asyncio
from typing import Dict, Tuple

from .contracts import (
    CapabilityContext,
    CapabilityMediaPolicy,
    CapabilityRequest,
    CapabilityResult,
    CapabilityStatus,
    MediaCandidate,
)
from .registry import CapabilityRegistry


class CapabilityGovernor:
    def __init__(self, registry: CapabilityRegistry) -> None:
        if not isinstance(registry, CapabilityRegistry):
            raise TypeError("registry must be a CapabilityRegistry")
        self.registry = registry
        self._semaphores: Dict[str, asyncio.Semaphore] = {}

    async def execute(
        self,
        request: CapabilityRequest,
        context: CapabilityContext,
        *,
        now: int,
    ) -> CapabilityResult:
        if not isinstance(request, CapabilityRequest):
            raise TypeError("request must be a CapabilityRequest")
        if not isinstance(context, CapabilityContext):
            raise TypeError("context must be a CapabilityContext")
        spec = self.registry.lookup(request.capability_name)
        if spec is None:
            return self._unsupported(request.capability_name, "capability_not_registered")
        if not spec.available:
            return self._unsupported(request.capability_name, "capability_unavailable")
        if int(context.deadline_at) <= int(now):
            return CapabilityResult(
                CapabilityStatus.TIMEOUT,
                request.capability_name,
                user_text="The capability deadline expired.",
                error_code="deadline_expired",
            )
        required = set(spec.manifest.permission_profile)
        allowed = set(context.allowed_permissions)
        if not required.issubset(allowed):
            return self._unsupported(request.capability_name, "permission_denied")

        timeout = min(
            float(spec.manifest.default_timeout_seconds),
            max(0.001, float(int(context.deadline_at) - int(now))),
        )
        semaphore = self._semaphores.get(spec.name)
        if semaphore is None:
            semaphore = asyncio.Semaphore(spec.manifest.max_concurrency)
            self._semaphores[spec.name] = semaphore

        async with semaphore:
            result = await self.registry.execute(request, timeout_seconds=timeout)

        if result.status is not CapabilityStatus.SUCCESS:
            return result
        result = self._apply_media_policy(result, context.media_policy)
        if self._result_size(result) > spec.manifest.max_result_size:
            return CapabilityResult(
                CapabilityStatus.FAILED,
                request.capability_name,
                user_text="The capability result was too large.",
                error_code="result_too_large",
            )
        return result

    @staticmethod
    def _unsupported(capability_name: str, error_code: str) -> CapabilityResult:
        return CapabilityResult(
            CapabilityStatus.UNSUPPORTED,
            capability_name,
            user_text="This capability is not available.",
            error_code=error_code,
        )

    @staticmethod
    def _apply_media_policy(
        result: CapabilityResult,
        policy: CapabilityMediaPolicy,
    ) -> CapabilityResult:
        if not policy.capability_media_allowed:
            allowed_media: Tuple[MediaCandidate, ...] = ()
        else:
            allowed_kinds = set(policy.allowed_media_kinds)
            allowed_labels = set(policy.allowed_safety_labels)
            allowed_media = tuple(
                candidate
                for candidate in result.media_candidates
                if (
                    (not allowed_kinds or candidate.media_kind in allowed_kinds)
                    and (
                        not allowed_labels
                        or candidate.safety_label in allowed_labels
                    )
                )
            )
        if allowed_media == result.media_candidates:
            return result
        return CapabilityResult(
            result.status,
            result.capability_name,
            facts=result.facts,
            user_text=result.user_text,
            error_code=result.error_code,
            diagnostic=result.diagnostic,
            media_candidates=allowed_media,
        )

    @staticmethod
    def _result_size(result: CapabilityResult) -> int:
        text_size = sum(len(item) for item in result.facts)
        text_size += len(result.user_text)
        text_size += len(result.error_code)
        text_size += len(result.diagnostic)
        media_size = sum(
            len(candidate.media_id)
            + len(candidate.source)
            + len(candidate.locator)
            + len(candidate.media_kind)
            + len(candidate.semantic_label)
            + len(candidate.purpose)
            + len(candidate.safety_label)
            for candidate in result.media_candidates
        )
        return text_size + media_size
```

- [ ] **Step 4: Export governor**

In `groupmate/capabilities/__init__.py`, import and export `CapabilityGovernor`.

- [ ] **Step 5: Run governor tests and verify GREEN**

Run:

```bash
python3 -m pytest tests/test_capability_governor.py tests/test_capability_registry.py -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit**

```bash
git add groupmate/capabilities/governor.py groupmate/capabilities/__init__.py tests/test_capability_governor.py
git commit -m "feat: add capability governor"
```

---

### Task 4: Workflow Uses Governor Context

**Files:**
- Modify: `groupmate/engine/workflow.py`
- Modify: `tests/test_workflow.py`
- Modify: `tests/test_capability_governor.py`

- [ ] **Step 1: Write workflow tests for governed context**

Add this helper and tests near existing capability workflow tests in `tests/test_workflow.py`:

```python
from groupmate.capabilities import (
    CapabilityContext,
    CapabilityGovernor,
    CapabilityMediaPolicy,
    CapabilityPermission,
)


class RecordingGovernor:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def execute(self, request, context, *, now):
        self.calls.append((request, context, now))
        return self.result


def test_workflow_builds_safe_capability_context(message_factory, balanced_policy):
    governor = RecordingGovernor(
        CapabilityResult(
            CapabilityStatus.SUCCESS,
            "vision",
            facts=("图片描述",),
            user_text="图片描述",
        )
    )
    workflow = build_workflow(
        capability_governor=governor,
        task_response_resolver=lambda scene, latest: _resolution(
            "SUPPORTED",
            capability_name="vision",
        ),
    )

    topic = _task_topic(
        message_factory,
        "帮我看看图",
        image_urls=("https://example.test/image.png",),
    )
    outcome = asyncio.run(
        workflow.evaluate(topic, TriggerKind.ALIAS_DIRECT, balanced_policy)
    )

    assert outcome.sent is True
    assert len(governor.calls) == 1
    request, context, now = governor.calls[0]
    assert request.capability_name == "vision"
    assert isinstance(context, CapabilityContext)
    assert context.persona_id == "aemeath"
    assert context.group_id == "g1"
    assert context.actor_id == "u1"
    assert context.message_id == topic.latest.message_id
    assert context.trace_id
    assert CapabilityPermission.VISION_READ in context.allowed_permissions
    assert context.media_policy.capability_media_allowed is True
    assert not hasattr(context, "platform")
    assert not hasattr(context, "memory")


def test_workflow_denies_vision_permission_when_vision_disabled(
    message_factory, balanced_policy
):
    governor = RecordingGovernor(
        CapabilityResult(
            CapabilityStatus.UNSUPPORTED,
            "vision",
            error_code="permission_denied",
        )
    )
    workflow = build_workflow(
        vision_enabled=False,
        capability_governor=governor,
        task_response_resolver=lambda scene, latest: _resolution(
            "SUPPORTED",
            capability_name="vision",
        ),
    )

    outcome = asyncio.run(
        workflow.evaluate(
            _task_topic(
                message_factory,
                "帮我看看图",
                image_urls=("https://example.test/image.png",),
            ),
            TriggerKind.ALIAS_DIRECT,
            balanced_policy,
        )
    )

    assert outcome.sent is True
    assert governor.calls == []
```

If `build_workflow()` does not accept `capability_governor`, update the helper in `tests/test_workflow.py` to pass it through to `CognitiveWorkflow`.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
python3 -m pytest tests/test_workflow.py::test_workflow_builds_safe_capability_context tests/test_workflow.py::test_workflow_denies_vision_permission_when_vision_disabled -q
```

Expected: FAIL because `CognitiveWorkflow` does not accept or use `capability_governor`.

- [ ] **Step 3: Update workflow constructor**

In `groupmate/engine/workflow.py`, import:

```python
from ..capabilities import CapabilityGovernor
from ..capabilities.contracts import (
    CapabilityContext,
    CapabilityMediaPolicy,
    CapabilityPermission,
)
```

Change constructor signature:

```python
        capabilities: Optional[CapabilityRegistry] = None,
        capability_governor: Optional[CapabilityGovernor] = None,
        composer: Optional[ResponseComposer] = None,
```

Set fields:

```python
        self.capabilities = capabilities
        self.capability_governor = (
            capability_governor
            if capability_governor is not None
            else (
                CapabilityGovernor(capabilities)
                if capabilities is not None
                else None
            )
        )
```

- [ ] **Step 4: Route execution through governor**

Change the capability call site around current line 373:

```python
            capability_result = await self._execute_capability(
                decision_id,
                topic,
                capability_name,
                participation.media_policy,
                now,
            )
```

Change `_execute_capability()` signature:

```python
    async def _execute_capability(
        self,
        decision_id: str,
        topic: TopicSnapshot,
        capability_name: str,
        media_policy,
        now: int,
    ) -> CapabilityResult:
```

Replace the direct `self.capabilities.execute(request)` branch with:

```python
        elif self.capability_governor is None:
            result = CapabilityResult(
                CapabilityStatus.UNSUPPORTED,
                capability_name,
                user_text="This capability is not available.",
                error_code="capability_not_registered",
            )
        else:
            latest = topic.latest
            request = CapabilityRequest(
                capability_name=capability_name,
                message_text=latest.text if latest is not None else "",
                media_locators=self._topic_image_urls(topic),
                group_id=topic.group_id,
                actor_id=latest.sender_id if latest is not None else "",
                message_id=latest.message_id if latest is not None else "",
            )
            context = CapabilityContext(
                persona_id=self.persona_context.persona_id,
                group_id=topic.group_id,
                actor_id=latest.sender_id if latest is not None else "",
                message_id=latest.message_id if latest is not None else "",
                trace_id=decision_id,
                deadline_at=now + 10,
                allowed_permissions=(
                    (CapabilityPermission.VISION_READ,)
                    if capability_name == "vision"
                    else ()
                ),
                media_policy=CapabilityMediaPolicy(
                    capability_media_allowed=bool(
                        getattr(media_policy, "capability_media_allowed", False)
                    ),
                    allowed_media_kinds=("image",),
                    allowed_safety_labels=("catalog_approved", "provider_approved", "reviewed", "safe"),
                ),
            )
            try:
                result = await self.capability_governor.execute(
                    request,
                    context,
                    now=now,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - capability boundary fails closed
                result = CapabilityResult(
                    CapabilityStatus.FAILED,
                    capability_name,
                    user_text="The capability could not complete the request.",
                    error_code="execution_error",
                    diagnostic=type(exc).__name__,
                )
```

Keep the existing early `vision_disabled` and `cost_budget_exhausted` branches for this task so behavior stays stable. Governor will own all provider execution after those host-level availability checks.

- [ ] **Step 5: Run workflow capability tests**

Run:

```bash
python3 -m pytest tests/test_workflow.py tests/test_capability_governor.py -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit**

```bash
git add groupmate/engine/workflow.py tests/test_workflow.py
git commit -m "refactor: execute capabilities through governor context"
```

---

### Task 5: Bridge Wiring And Built-In Vision Governance

**Files:**
- Modify: `groupmate/host/bridge.py`
- Modify: `tests/test_native_wake_suppress.py`
- Modify: `tests/test_provider_resolution.py`
- Modify: `tests/test_builtin_capabilities.py`

- [ ] **Step 1: Add bridge wiring assertions**

Add to `tests/test_native_wake_suppress.py` near `test_bridge_registers_policy_scoped_vision_capability`:

```python
def test_bridge_injects_governed_capabilities(tmp_path):
    bridge = _bridge(tmp_path, vision_enabled=True)
    workflow = bridge._workflow_for("g1", bridge.persona_context)

    assert workflow.capabilities is not None
    assert workflow.capability_governor is not None
    manifest_names = tuple(item.name for item in workflow.capabilities.manifests())
    assert manifest_names == ("vision",)
```

Add to `tests/test_provider_resolution.py` or extend an existing vision-disabled test:

```python
def test_disabled_vision_is_registered_but_unavailable(tmp_path):
    bridge = _bridge(tmp_path, vision_enabled=False)
    workflow = bridge._workflow_for("g1", bridge.persona_context)

    spec = workflow.capabilities.lookup("vision")
    assert spec is not None
    assert spec.available is False
    assert workflow.capability_governor is not None
```

- [ ] **Step 2: Run tests and verify RED or confirm current compatibility**

Run:

```bash
python3 -m pytest tests/test_native_wake_suppress.py::test_bridge_injects_governed_capabilities tests/test_provider_resolution.py -q
```

Expected before bridge update: FAIL if `workflow.capability_governor` is missing.

- [ ] **Step 3: Wire governor in bridge**

In `groupmate/host/bridge.py`, import `CapabilityGovernor`:

```python
from ..capabilities import CapabilityGovernor, CapabilityRegistry, CapabilityRequest, vision_spec
```

After registering vision:

```python
        governor = CapabilityGovernor(capabilities)
```

Pass it to `CognitiveWorkflow`:

```python
            capabilities=capabilities,
            capability_governor=governor,
```

- [ ] **Step 4: Run bridge and provider tests**

Run:

```bash
python3 -m pytest tests/test_native_wake_suppress.py tests/test_provider_resolution.py tests/test_builtin_capabilities.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```bash
git add groupmate/host/bridge.py tests/test_native_wake_suppress.py tests/test_provider_resolution.py tests/test_builtin_capabilities.py
git commit -m "refactor: inject governed capability runtime"
```

---

### Task 6: Composer Media Policy Regression

**Files:**
- Modify: `tests/test_composer.py`
- Modify: `groupmate/engine/composer.py` only if tests reveal a gap

- [ ] **Step 1: Add regression that governor-filtered media is still guarded**

Add to `tests/test_composer.py`:

```python
def test_composer_keeps_final_guard_after_governor_media_filter():
    act_plan = ResponseActPlan(ResponseAct.TASK_HANDOFF, capability_name="image_tool")
    unsafe = MediaCandidate(
        media_id="img-unsafe",
        source="provider",
        locator="https://example.test/unsafe.png",
        media_kind="image",
        semantic_label="unsafe image",
        purpose="reply attachment",
        safety_label="untrusted",
    )
    safe = MediaCandidate(
        media_id="img-safe",
        source="provider",
        locator="https://example.test/safe.png",
        media_kind="image",
        semantic_label="safe image",
        purpose="reply attachment",
        safety_label="safe",
    )
    result = CapabilityResult(
        CapabilityStatus.SUCCESS,
        "image_tool",
        facts=("fact",),
        user_text="fact",
        media_candidates=(unsafe, safe),
    )

    draft = ResponseComposer().compose(
        text="看这张。",
        act_plan=act_plan,
        quote_message_id=None,
        capability_result=result,
    )

    assert [segment.media_id for segment in draft.segments if segment.media_id] == [
        "img-safe"
    ]
```

- [ ] **Step 2: Run composer tests**

Run:

```bash
python3 -m pytest tests/test_composer.py -q
```

Expected: pass with existing final filter. If it fails, keep `ResponseComposer._safe_capability_media()` as the last safety filter and adjust only the smallest condition needed.

- [ ] **Step 3: Commit**

If only tests changed:

```bash
git add tests/test_composer.py
git commit -m "test: preserve composer media guard after governance"
```

If production code changed:

```bash
git add groupmate/engine/composer.py tests/test_composer.py
git commit -m "fix: keep composer media guard after governance"
```

---

### Task 7: Documentation And Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-07-31-host-command-capability-boundary-design.md`
- Modify: `docs/superpowers/plans/2026-07-31-capability-governance.md`

- [ ] **Step 1: Update README architecture note**

In `README.md`, under the “规格” or capability paragraph, add:

```markdown
内部能力通过 `CapabilityManifest`、`CapabilityContext` 和 `CapabilityGovernor` 显式治理。Provider 只能返回结构化事实、媒体候选或 handoff 状态；最终表达和发送仍由人格、OutputFirewall、Composer 和 DeliveryService 统一处理。
```

- [ ] **Step 2: Update design status**

In `docs/superpowers/specs/2026-07-31-host-command-capability-boundary-design.md`, change the status line to:

```markdown
状态：Host Command Isolation 与 Capability Governance 已实施；Provider SPI 待实施
```

Do not mark Provider SPI, Tool Gateway, MCP, or dynamic provider discovery complete.

- [ ] **Step 3: Mark this plan complete after verification**

Only after all commands in Step 4 and Step 5 pass, add below the Goal line:

```markdown
**Status:** Complete; verified by focused capability tests, the full pytest suite, and `git diff --check`.
```

- [ ] **Step 4: Run focused capability tests**

Run:

```bash
python3 -m pytest \
  tests/test_capability_contracts.py \
  tests/test_capability_registry.py \
  tests/test_capability_governor.py \
  tests/test_builtin_capabilities.py \
  tests/test_workflow.py \
  tests/test_native_wake_suppress.py \
  tests/test_provider_resolution.py \
  tests/test_composer.py \
  -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Run full verification and residual scan**

Run:

```bash
python3 -m pytest -q
git diff --check
rg -n "CapabilityRegistry\(\).*execute|capabilities\.execute\(" groupmate tests
```

Expected:

- `python3 -m pytest -q`: all tests pass.
- `git diff --check`: no output, exit 0.
- residual scan: no direct runtime execution bypassing `CapabilityGovernor`. Registry unit tests may still call `registry.execute()` to verify the primitive; production workflow must not.

- [ ] **Step 6: Commit docs**

```bash
git add README.md docs/superpowers/specs/2026-07-31-host-command-capability-boundary-design.md docs/superpowers/plans/2026-07-31-capability-governance.md
git commit -m "docs: close capability governance plan"
```

---

## Self-Review

Spec coverage:

- Manifest, permission, latency/cost/failure, timeout, concurrency and max result size: Task 1 and Task 2.
- Context without AstrBot event/context, delivery, memory, actor or workflow dependencies: Task 1 and Task 4.
- Explicit registration only and duplicate rejection: Task 2.
- Governor permission/deadline/timeout/media/size enforcement: Task 3.
- Workflow single governed invocation path: Task 4.
- Bridge wiring for built-in vision: Task 5.
- Result still passes through persona/composer/delivery, not direct send: Task 4 keeps existing workflow path, Task 6 verifies final composer media guard.
- Provider SPI, Tool Gateway and external services remain out of scope by design.

Placeholder scan:

- This plan contains no unresolved placeholder markers.
- Every task includes concrete files, test code, implementation shape, commands, expected results, and commit commands.

Type consistency:

- `CapabilityManifest`, `CapabilityContext`, `CapabilityMediaPolicy`, `CapabilityPermission`, and `CapabilityGovernor` are introduced before later tasks reference them.
- `CapabilitySpec.name` remains available as a property so existing callers can continue using `spec.name` while tests migrate to `spec.manifest`.
- `CapabilityGovernor.execute(request, context, *, now)` is used consistently in tests and workflow wiring.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-31-capability-governance.md`.

Two execution options:

1. **Subagent-Driven (recommended)** - dispatch a fresh subagent per task and review between tasks.
2. **Inline Execution** - execute tasks in this session using `superpowers:executing-plans`, with checkpoints after each task.
