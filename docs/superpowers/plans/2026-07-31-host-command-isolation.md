# Host Command Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 AstrBot 通用群消息入口前识别已注册命令和宿主唤醒前缀，使 `/取名` 等其他插件命令以及 Groupmate 自有管理命令完全不进入 Groupmate 的 Actor、话题、记忆、模型和发送链路。

**Architecture:** 新增纯宿主适配组件 `HostEventGate`，只读取 AstrBot 事件事实和当前会话配置，输出不可变的 `HostEventDisposition`。新增 `AstrBotEventIngress` 作为 `main.py` 与 `AstrBotBridge` 之间的单一入口；只有 `GROUPMATE_MESSAGE` 可以调用 Bridge。领域 Runtime 同时把 `is_command=True` 作为最后一道防线，在追加 TopicWindow 和持久化前直接旁路。

**Tech Stack:** Python 3.7-compatible standard library、AstrBot `Context.get_config(umo)` / `activated_handlers`、OneBot v11 原始消息段、asyncio、pytest、SQLite 测试存储。

---

## Scope

本计划只实施 `docs/superpowers/specs/2026-07-31-host-command-capability-boundary-design.md` 的第一阶段 **Host Command Isolation**。

不在本计划实施：

- Capability Manifest、Capability Context 或 Capability Governor；
- Provider 生命周期；
- AstrBot Tool Gateway、MCP 或新外部能力；
- 动态能力扫描；
- 新的 Groupmate 管理命令。

Capability Governance 和 Provider SPI 分别建立后续实施计划。

## File Map

- Create `groupmate/host/event_gate.py`: AstrBot 事件事实提取、命令/前缀分类和启用群过滤。
- Create `groupmate/host/ingress.py`: 在 Gate 通过后调用 Bridge，保持唯一入口。
- Modify `groupmate/host/__init__.py`: 导出新的宿主边界类型。
- Modify `main.py`: 构造 Gate/Ingress，普通群消息和 LLM Hook 统一从 Ingress 进入。
- Modify `groupmate/host/bridge.py`: 删除 `activated_handlers` 反射和晚期命令判断。
- Modify `groupmate/engine/runtime.py`: 命令在 TopicWindow 和 Memory 之前旁路。
- Create `tests/test_host_event_gate.py`: 命令、优先级顺序、可配置前缀、原始消息和真实 @ 分类契约。
- Create `tests/test_host_event_ingress.py`: 宿主事件不会调用 Bridge 或 `stop_event()` 的入口契约。
- Modify `tests/test_runtime.py`: 领域命令不追加窗口、不写消息、不触发评估。
- Modify `tests/test_plugin_loading.py`: AstrBot 插件入口委托给 Ingress。
- Modify `README.md`: 明确宿主命令在进入 Groupmate 前旁路。
- Modify `docs/superpowers/specs/2026-07-31-host-command-capability-boundary-design.md`: 标记第一阶段实施状态。

## Execution Preflight

执行代码前使用 `superpowers:using-git-worktrees` 创建或确认隔离工作区，分支名使用 `feat/host-command-isolation`。在隔离工作区运行：

```bash
python3 -m pytest -q
```

Expected: exit 0，全部现有测试通过。若基线失败，停止实施并报告，不把基线失败混入本计划。

---

### Task 1: Runtime Command Bypass Before Persistence

**Files:**
- Modify: `tests/test_runtime.py`
- Modify: `groupmate/engine/runtime.py:198-221`

- [ ] **Step 1: Write the failing runtime test**

在 `tests/test_runtime.py` 的基础 Actor 测试区域加入：

```python
def test_command_bypasses_window_memory_and_evaluation(message_factory):
    async def scenario():
        workflow = RecordingWorkflow()
        actor = actor_for(workflow)
        await actor.start()
        await actor.submit(
            message_factory(
                message_id="command",
                text="/取名 小明",
                is_command=True,
            )
        )
        await actor.drain()
        snapshot = actor.window.snapshot()
        last_trigger = actor.last_trigger
        await actor.close()
        return workflow, snapshot, last_trigger

    workflow, snapshot, last_trigger = asyncio.run(scenario())

    assert snapshot.messages == ()
    assert workflow.memory.messages == []
    assert workflow.evaluations == []
    assert last_trigger.value == "command"
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
python3 -m pytest tests/test_runtime.py::test_command_bypasses_window_memory_and_evaluation -q
```

Expected: FAIL，因为当前 `_handle_ingest()` 会在识别 `TriggerKind.COMMAND` 前追加 TopicWindow 并调用 `save_message_async()`。

- [ ] **Step 3: Move command bypass before append**

将 `GroupActor._handle_ingest()` 的开头改为：

```python
    async def _handle_ingest(self, item: _Ingest) -> None:
        message = item.message
        classified = self.router.classify(message)
        if classified.kind is TriggerKind.COMMAND:
            self.last_trigger = classified.kind
            return

        appended = self.window.append(message)
        if appended:
            await self.workflow.memory.save_message_async(
                self.persona_context.persona_id,
                message,
            )
        if not item.schedule or not self._dispatch_enabled:
            return

        result = self._maybe_continue(message, classified)
```

保留后续的 `IGNORE` 判断。不要把普通 bot/空消息的持久化语义一并改掉，本任务只收紧 Command。

- [ ] **Step 4: Run focused runtime tests and verify GREEN**

Run:

```bash
python3 -m pytest tests/test_runtime.py tests/test_phase1_runtime.py tests/test_triggers.py -q
```

Expected: exit 0，命令零持久化测试和现有调度/触发测试全部通过。

- [ ] **Step 5: Commit runtime defense**

```bash
git add groupmate/engine/runtime.py tests/test_runtime.py
git commit -m "fix: bypass commands before runtime persistence"
```

---

### Task 2: Pure Host Event Gate

**Files:**
- Create: `groupmate/host/event_gate.py`
- Create: `tests/test_host_event_gate.py`

- [ ] **Step 1: Write command and prefix classification tests**

创建 `tests/test_host_event_gate.py`：

```python
from types import SimpleNamespace

import pytest

from groupmate.host.event_gate import HostEventDisposition, HostEventGate


class CommandFilter:
    pass


class EventMessageTypeFilter:
    pass


class FakeEvent:
    def __init__(
        self,
        *,
        text="普通消息",
        raw_text=None,
        filters=(),
        at_bot=False,
        group_id="g1",
        sender_id="u1",
        stopped=False,
    ):
        segments = []
        if at_bot:
            segments.append({"type": "at", "data": {"qq": "bot"}})
        segments.append(
            {
                "type": "text",
                "data": {"text": text if raw_text is None else raw_text},
            }
        )
        self.message_str = text
        self.message_obj = SimpleNamespace(
            raw_message={"message": segments},
            message=(),
        )
        self.unified_msg_origin = "aiocqhttp:GroupMessage:{}".format(group_id)
        self.is_at_or_wake_command = at_bot
        self._group_id = group_id
        self._sender_id = sender_id
        self._stopped = stopped
        self._extras = {
            "activated_handlers": [SimpleNamespace(event_filters=list(filters))]
            if filters
            else []
        }

    def get_group_id(self):
        return self._group_id

    def get_sender_id(self):
        return self._sender_id

    def get_self_id(self):
        return "bot"

    def get_extra(self, key=None, default=None):
        return self._extras.get(key, default)

    def is_stopped(self):
        return self._stopped


def gate(prefixes=("/",), enabled_groups=()):
    return HostEventGate(
        config_resolver=lambda umo: {"wake_prefix": list(prefixes)},
        enabled_groups=enabled_groups,
    )


@pytest.mark.parametrize("command_index", [0, 1])
def test_registered_command_wins_regardless_of_handler_order(command_index):
    event = FakeEvent(text="取名 小明", raw_text="/取名 小明")
    generic = SimpleNamespace(event_filters=[EventMessageTypeFilter()])
    command = SimpleNamespace(event_filters=[CommandFilter()])
    handlers = [generic, command]
    if command_index == 0:
        handlers.reverse()
    event._extras["activated_handlers"] = handlers

    assert gate().classify(event) is HostEventDisposition.HOST_COMMAND


def test_unknown_configured_prefix_stays_with_astrbot():
    event = FakeEvent(text="未知命令", raw_text="!未知命令")

    assert gate(("!",)).classify(event) is HostEventDisposition.HOST_WAKE_PREFIX


def test_raw_prefix_survives_astrbot_message_stripping_and_bot_at():
    event = FakeEvent(text="取名", raw_text=" /取名", at_bot=True)

    assert gate().classify(event) is HostEventDisposition.HOST_WAKE_PREFIX


def test_native_bot_at_without_prefix_enters_groupmate():
    event = FakeEvent(text="你今天怎样", at_bot=True)

    assert gate().classify(event) is HostEventDisposition.GROUPMATE_MESSAGE


def test_wake_event_without_raw_direct_evidence_stays_with_astrbot():
    event = FakeEvent(text="help")
    event.is_at_or_wake_command = True
    event.message_obj.raw_message = None

    assert gate().classify(event) is HostEventDisposition.HOST_WAKE_PREFIX


def test_ordinary_group_message_enters_groupmate():
    assert gate().classify(FakeEvent()) is HostEventDisposition.GROUPMATE_MESSAGE


@pytest.mark.parametrize(
    "event",
    [
        FakeEvent(group_id=""),
        FakeEvent(sender_id="bot"),
        FakeEvent(stopped=True),
        FakeEvent(group_id="g2"),
    ],
)
def test_ignored_events_never_enter_groupmate(event):
    assert gate(enabled_groups=("g1",)).classify(event) is HostEventDisposition.IGNORE
```

- [ ] **Step 2: Run the gate tests and verify RED**

Run:

```bash
python3 -m pytest tests/test_host_event_gate.py -q
```

Expected: collection ERROR with `ModuleNotFoundError: groupmate.host.event_gate`.

- [ ] **Step 3: Implement `HostEventGate`**

创建 `groupmate/host/event_gate.py`：

```python
"""AstrBot-owned event classification before Groupmate runtime admission."""

from __future__ import annotations

from typing import Any, Callable, Optional, Sequence, Tuple

from ..models import StringEnum


class HostEventDisposition(StringEnum):
    HOST_COMMAND = "host_command"
    HOST_WAKE_PREFIX = "host_wake_prefix"
    GROUPMATE_MESSAGE = "groupmate_message"
    IGNORE = "ignore"


class HostEventGate:
    """Classify host-owned traffic without mutating the AstrBot event."""

    def __init__(
        self,
        config_resolver: Optional[Callable[[str], Any]] = None,
        enabled_groups: Sequence[str] = (),
    ) -> None:
        self._config_resolver = config_resolver
        self._enabled_groups = frozenset(
            str(group_id).strip()
            for group_id in (enabled_groups or ())
            if str(group_id).strip()
        )

    def classify(self, event: Any) -> HostEventDisposition:
        group_id = self._call_identifier(event, "get_group_id")
        if not group_id or (
            self._enabled_groups and group_id not in self._enabled_groups
        ):
            return HostEventDisposition.IGNORE
        if self._is_stopped(event):
            return HostEventDisposition.IGNORE
        sender_id = self._call_identifier(event, "get_sender_id")
        bot_id = self._call_identifier(event, "get_self_id")
        if sender_id and bot_id and sender_id == bot_id:
            return HostEventDisposition.IGNORE
        if self._has_command_handler(event):
            return HostEventDisposition.HOST_COMMAND

        raw_text = self._raw_text(event)
        if self._starts_with_wake_prefix(event, raw_text):
            return HostEventDisposition.HOST_WAKE_PREFIX
        if bool(getattr(event, "is_at_or_wake_command", False)) and not (
            self._has_explicit_direct_target(event, bot_id)
        ):
            return HostEventDisposition.HOST_WAKE_PREFIX
        return HostEventDisposition.GROUPMATE_MESSAGE

    def _starts_with_wake_prefix(self, event: Any, raw_text: str) -> bool:
        text = str(raw_text or "").strip()
        return bool(text) and any(
            text.startswith(prefix) for prefix in self._wake_prefixes(event)
        )

    def _wake_prefixes(self, event: Any) -> Tuple[str, ...]:
        values = ("/",)
        if self._config_resolver is not None:
            try:
                config = self._config_resolver(
                    str(getattr(event, "unified_msg_origin", "") or "")
                )
                configured = config.get("wake_prefix", values)
                if isinstance(configured, str):
                    configured = (configured,)
                values = tuple(configured or values)
            except Exception:
                values = ("/",)
        normalized = tuple(
            str(value).strip()
            for value in values
            if str(value or "").strip()
        )
        return normalized or ("/",)

    @classmethod
    def _has_command_handler(cls, event: Any) -> bool:
        handlers = cls._extra(event, "activated_handlers", ()) or ()
        for handler in handlers:
            for event_filter in getattr(handler, "event_filters", ()) or ():
                names = (
                    base.__name__.lower()
                    for base in type(event_filter).__mro__
                )
                if any("command" in name for name in names):
                    return True
        return False

    @classmethod
    def _has_explicit_direct_target(cls, event: Any, bot_id: str) -> bool:
        raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
        if isinstance(raw, dict):
            if bool(raw.get("reply_to_bot", False)):
                return True
            for segment in raw.get("message", ()) or ():
                if not isinstance(segment, dict):
                    continue
                if str(segment.get("type", "")).lower() != "at":
                    continue
                data = segment.get("data") or {}
                target = str(data.get("qq", data.get("user_id", "")))
                if target and target == bot_id:
                    return True
        components = getattr(getattr(event, "message_obj", None), "message", ()) or ()
        for component in components:
            component_type = str(getattr(component, "type", "")).lower()
            class_name = component.__class__.__name__.lower()
            if class_name != "reply" and not component_type.endswith("reply"):
                continue
            if str(getattr(component, "sender_id", "") or "") == bot_id:
                return True
        return False

    @staticmethod
    def _raw_text(event: Any) -> str:
        raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
        payload = raw.get("message", raw.get("raw_message", "")) if isinstance(raw, dict) else raw
        if isinstance(payload, str):
            return payload
        if isinstance(payload, (list, tuple)):
            parts = []
            for segment in payload:
                if not isinstance(segment, dict):
                    continue
                if str(segment.get("type", "")).lower() not in ("text", "plain"):
                    continue
                data = segment.get("data") or {}
                parts.append(str(data.get("text", segment.get("text", "")) or ""))
            if parts:
                return "".join(parts)
        return str(getattr(event, "message_str", "") or "")

    @staticmethod
    def _call_identifier(event: Any, method_name: str) -> str:
        method = getattr(event, method_name, None)
        if not callable(method):
            return ""
        try:
            return str(method() or "").strip()
        except Exception:
            return ""

    @staticmethod
    def _is_stopped(event: Any) -> bool:
        method = getattr(event, "is_stopped", None)
        if not callable(method):
            return False
        try:
            return bool(method())
        except Exception:
            return True

    @staticmethod
    def _extra(event: Any, key: str, default: Any) -> Any:
        method = getattr(event, "get_extra", None)
        if not callable(method):
            return default
        try:
            return method(key, default)
        except TypeError:
            value = method(key)
            return default if value is None else value
```

- [ ] **Step 4: Run gate tests and verify GREEN**

Run:

```bash
python3 -m pytest tests/test_host_event_gate.py tests/test_astrbot_translation.py -q
```

Expected: exit 0，所有 HostEventGate 与现有 OneBot 翻译测试通过。

- [ ] **Step 5: Commit the pure host gate**

```bash
git add groupmate/host/event_gate.py tests/test_host_event_gate.py
git commit -m "feat: classify AstrBot-owned host events"
```

---

### Task 3: Single AstrBot Event Ingress

**Files:**
- Create: `groupmate/host/ingress.py`
- Create: `tests/test_host_event_ingress.py`

- [ ] **Step 1: Write ingress side-effect tests**

创建 `tests/test_host_event_ingress.py`：

```python
import asyncio

from groupmate.host.bridge import TurnOwner
from groupmate.host.event_gate import HostEventDisposition
from groupmate.host.ingress import AstrBotEventIngress


class StaticGate:
    def __init__(self, disposition):
        self.disposition = disposition

    def classify(self, event):
        del event
        return self.disposition


class RecordingBridge:
    def __init__(self, owner=TurnOwner.OBSERVE_ONLY):
        self.owner = owner
        self.calls = []

    def apply_owner_to_event(self, event):
        self.calls.append(("owner", event))
        return self.owner

    async def handle_event(self, event):
        self.calls.append(("handle", event))

    async def observe_only(self, event):
        self.calls.append(("observe", event))

    async def enrich_request(self, event, req):
        self.calls.append(("enrich", event, req))


class Event:
    def __init__(self):
        self.stop_calls = 0

    def stop_event(self):
        self.stop_calls += 1


def test_host_command_returns_without_bridge_or_stop_event():
    event = Event()
    bridge = RecordingBridge()
    ingress = AstrBotEventIngress(
        StaticGate(HostEventDisposition.HOST_COMMAND),
        bridge,
    )

    disposition = asyncio.run(ingress.handle_group_message(event))

    assert disposition is HostEventDisposition.HOST_COMMAND
    assert bridge.calls == []
    assert event.stop_calls == 0


def test_host_prefix_skips_llm_enrichment():
    event = Event()
    request = object()
    bridge = RecordingBridge()
    ingress = AstrBotEventIngress(
        StaticGate(HostEventDisposition.HOST_WAKE_PREFIX),
        bridge,
    )

    disposition = asyncio.run(ingress.enrich_request(event, request))

    assert disposition is HostEventDisposition.HOST_WAKE_PREFIX
    assert bridge.calls == []


def test_groupmate_owner_uses_normal_bridge_path():
    event = Event()
    bridge = RecordingBridge(TurnOwner.GROUPMATE)
    ingress = AstrBotEventIngress(
        StaticGate(HostEventDisposition.GROUPMATE_MESSAGE),
        bridge,
    )

    asyncio.run(ingress.handle_group_message(event))

    assert bridge.calls == [("owner", event), ("handle", event)]


def test_astrbot_agent_owner_only_preloads_context():
    event = Event()
    bridge = RecordingBridge(TurnOwner.ASTRBOT_AGENT)
    ingress = AstrBotEventIngress(
        StaticGate(HostEventDisposition.GROUPMATE_MESSAGE),
        bridge,
    )

    asyncio.run(ingress.handle_group_message(event))

    assert bridge.calls == [("owner", event), ("observe", event)]


def test_admitted_request_reaches_bridge_enrichment():
    event = Event()
    request = object()
    bridge = RecordingBridge()
    ingress = AstrBotEventIngress(
        StaticGate(HostEventDisposition.GROUPMATE_MESSAGE),
        bridge,
    )

    asyncio.run(ingress.enrich_request(event, request))

    assert bridge.calls == [("enrich", event, request)]
```

- [ ] **Step 2: Run ingress tests and verify RED**

Run:

```bash
python3 -m pytest tests/test_host_event_ingress.py -q
```

Expected: collection ERROR with `ModuleNotFoundError: groupmate.host.ingress`.

- [ ] **Step 3: Implement `AstrBotEventIngress`**

创建 `groupmate/host/ingress.py`：

```python
"""Single AstrBot event ingress for Groupmate host traffic."""

from __future__ import annotations

from typing import Any

from .bridge import TurnOwner
from .event_gate import HostEventDisposition, HostEventGate


class AstrBotEventIngress:
    def __init__(self, gate: HostEventGate, bridge: Any) -> None:
        self.gate = gate
        self.bridge = bridge

    async def handle_group_message(self, event: Any) -> HostEventDisposition:
        disposition = self.gate.classify(event)
        if disposition is not HostEventDisposition.GROUPMATE_MESSAGE:
            return disposition
        owner = self.bridge.apply_owner_to_event(event)
        if owner is TurnOwner.ASTRBOT_AGENT:
            await self.bridge.observe_only(event)
        else:
            await self.bridge.handle_event(event)
        return disposition

    async def enrich_request(self, event: Any, req: Any) -> HostEventDisposition:
        disposition = self.gate.classify(event)
        if disposition is not HostEventDisposition.GROUPMATE_MESSAGE:
            return disposition
        await self.bridge.enrich_request(event, req)
        return disposition
```

- [ ] **Step 4: Run ingress tests and verify GREEN**

Run:

```bash
python3 -m pytest tests/test_host_event_ingress.py -q
```

Expected: exit 0，宿主命令和前缀不会调用 Bridge 或 `stop_event()`，Groupmate/AstrBot Agent 原有归属路径保持通过。

- [ ] **Step 5: Commit the ingress boundary**

```bash
git add groupmate/host/ingress.py tests/test_host_event_ingress.py
git commit -m "feat: add single AstrBot event ingress"
```

---

### Task 4: Wire Groupmate Plugin and Remove Late Command Reflection

**Files:**
- Modify: `main.py:12-44`
- Modify: `groupmate/host/__init__.py`
- Modify: `groupmate/host/bridge.py:88-95,260-265,398-404`
- Modify: `tests/test_plugin_loading.py`
- Modify: `tests/test_host_event_gate.py`

- [ ] **Step 1: Add failing plugin delegation and cleanup tests**

在 `tests/test_host_event_gate.py` 末尾加入：

```python
def test_bridge_no_longer_owns_host_command_reflection():
    from groupmate.host.bridge import AstrBotBridge

    assert not hasattr(AstrBotBridge, "_is_command_event")
```

在 `tests/test_plugin_loading.py` 的子进程脚本中，将导入语句替换为以下结尾：

```python
module = importlib.import_module("data.plugins.astrbot_plugin_groupmate.main")


class FakeIngress:
    def __init__(self):
        self.calls = []

    async def handle_group_message(self, event):
        self.calls.append(("handle", event))

    async def enrich_request(self, event, request):
        self.calls.append(("enrich", event, request))


class FakeEvent:
    @staticmethod
    def is_private_chat():
        return False


plugin = object.__new__(module.GroupmatePlugin)
plugin.ingress = FakeIngress()
event = FakeEvent()
request = object()
import asyncio
asyncio.run(plugin.observe_group_message(event))
asyncio.run(plugin.enrich_native_request(event, request))
assert plugin.ingress.calls == [("handle", event), ("enrich", event, request)]
```

- [ ] **Step 2: Run wiring tests and verify RED**

Run:

```bash
python3 -m pytest tests/test_plugin_loading.py tests/test_host_event_gate.py::test_bridge_no_longer_owns_host_command_reflection -q
```

Expected: FAIL；当前主插件直接调用 `bridge`，并且 `AstrBotBridge._is_command_event` 仍存在。

- [ ] **Step 3: Export the new host boundary**

在 `groupmate/host/__init__.py` 加入：

```python
from .event_gate import HostEventDisposition, HostEventGate
from .ingress import AstrBotEventIngress
```

并在 `__all__` 中加入：

```python
    "AstrBotEventIngress",
    "HostEventDisposition",
    "HostEventGate",
```

- [ ] **Step 4: Construct and use the ingress in `main.py`**

把宿主导入改为：

```python
from .groupmate.host import (
    AstrBotBridge,
    AstrBotEventIngress,
    HostEventGate,
)
```

在 `self.bridge` 构造后加入：

```python
        self.event_gate = HostEventGate(
            config_resolver=getattr(context, "get_config", None),
            enabled_groups=self.config.enabled_groups,
        )
        self.ingress = AstrBotEventIngress(self.event_gate, self.bridge)
```

把通用群消息处理器改为：

```python
    async def observe_group_message(self, event: AstrMessageEvent):
        """旁路观察 QQ 群消息，不抢占已有指令。"""
        await self.ingress.handle_group_message(event)
```

把 LLM Hook 改为：

```python
    async def enrich_native_request(self, event: AstrMessageEvent, req):
        """为 AstrBot 原生唤醒请求补充有限群聊上下文。"""
        if event.is_private_chat():
            return
        await self.ingress.enrich_request(event, req)
```

`main.py` 不再导入 `TurnOwner`。

- [ ] **Step 5: Remove late command reflection from Bridge**

在 `AstrBotBridge.enrich_request()` 中把：

```python
        if self.paused or not group_id or self._is_command_event(event):
```

改为：

```python
        if self.paused or not group_id:
```

把 `_message_from_event()` 改为：

```python
    def _message_from_event(self, event: Any) -> ChatMessage:
        return OneBotTranslator.from_event(
            event,
            bot_id=str(event.get_self_id()),
        )
```

删除 `AstrBotBridge._is_command_event()`。保留 `OneBotTranslator.from_event(..., is_command=...)` 的通用参数和 `TriggerKind.COMMAND`，它们仍是非 AstrBot 适配器和领域防线的一部分。

- [ ] **Step 6: Run host wiring regression tests and verify GREEN**

Run:

```bash
python3 -m pytest tests/test_plugin_loading.py tests/test_host_event_gate.py tests/test_host_event_ingress.py tests/test_native_wake_suppress.py tests/test_provider_resolution.py -q
```

Expected: exit 0；主插件统一委托 Ingress，Bridge 不再反射 AstrBot command filters，原生唤醒唯一归属测试保持通过。

- [ ] **Step 7: Scan for residual late command detection**

Run:

```bash
rg -n "_is_command_event|activated_handlers" main.py groupmate
```

Expected: `activated_handlers` 只出现在 `groupmate/host/event_gate.py`；`_is_command_event` 无匹配。

- [ ] **Step 8: Commit plugin wiring**

```bash
git add main.py groupmate/host/__init__.py groupmate/host/bridge.py tests/test_plugin_loading.py tests/test_host_event_gate.py
git commit -m "refactor: gate AstrBot commands before Groupmate bridge"
```

---

### Task 5: Documentation, Contract Verification, and Completion

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-07-31-host-command-capability-boundary-design.md`
- Modify: `docs/superpowers/plans/2026-07-31-host-command-isolation.md`

- [ ] **Step 1: Update user-facing command behavior**

把 README “唤醒路径”中的第 5 项改为：

```markdown
5. **AstrBot 指令**：已注册命令和使用宿主唤醒前缀的输入在进入 Groupmate Actor 前旁路；不写入话题、记忆或 outbox，也不阻止其他插件处理
```

- [ ] **Step 2: Mark only Stage 1 implemented in the design**

把设计文档头部状态改为：

```markdown
状态：Host Command Isolation 已实施；Capability Governance 与 Provider SPI 待实施
```

不要把 Capability Governance 或 Provider SPI 标记为完成。

- [ ] **Step 3: Run focused contract tests**

Run:

```bash
python3 -m pytest tests/test_host_event_gate.py tests/test_host_event_ingress.py tests/test_runtime.py tests/test_plugin_loading.py tests/test_native_wake_suppress.py -q
```

Expected: exit 0，所有命令隔离、Runtime 防线、插件加载和原生唤醒测试通过。

- [ ] **Step 4: Run the full suite**

Run:

```bash
python3 -m pytest -q
```

Expected: exit 0，全部测试通过且无失败。

- [ ] **Step 5: Verify formatting and residuals**

Run:

```bash
git diff --check
```

Expected: no output，exit 0。

Run:

```bash
rg -n "_is_command_event" main.py groupmate tests
```

Expected: no output；运行时代码与测试不再依赖该方法。

- [ ] **Step 6: Mark this plan complete and commit documentation**

勾选本计划所有已完成步骤，并在文件头部 Goal 下加入：

```markdown
**Status:** Complete; verified by the full pytest suite and `git diff --check`.
```

然后提交：

```bash
git add README.md docs/superpowers/specs/2026-07-31-host-command-capability-boundary-design.md docs/superpowers/plans/2026-07-31-host-command-isolation.md
git commit -m "docs: close host command isolation plan"
```

- [ ] **Step 7: Use branch completion workflow**

调用 `superpowers:verification-before-completion` 读取最新完整测试输出，再调用 `superpowers:finishing-a-development-branch` 提供合并、PR、保留或丢弃分支的标准选项。不要在用户选择前自动合并或删除工作树。
