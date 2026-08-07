"""Version-tolerant execution of AstrBot function tools and command handlers."""

from __future__ import annotations

import asyncio
import inspect
import re
import types
import typing
from types import SimpleNamespace
from typing import Any, Iterable, Mapping

from .contracts import (
    ToolDescriptor,
    ToolExecutionResult,
    ToolExecutionStatus,
    ToolSource,
)


class CapturingEventProxy:
    """Delegate identity/platform reads while isolating mutable event output state."""

    def __init__(self, event: Any, *, passthrough_send: bool = False) -> None:
        object.__setattr__(self, "_event", event)
        object.__setattr__(self, "_result", None)
        object.__setattr__(self, "_outputs", [])
        object.__setattr__(self, "_extras", {})
        object.__setattr__(self, "_stopped", False)
        object.__setattr__(self, "_call_llm", False)
        object.__setattr__(self, "_passthrough_send", bool(passthrough_send))
        object.__setattr__(self, "_direct_sent", False)

    def __getattr__(self, name: str) -> Any:
        return getattr(object.__getattribute__(self, "_event"), name)

    @property
    def captured_outputs(self) -> tuple[str, ...]:
        return tuple(object.__getattribute__(self, "_outputs"))

    @property
    def direct_sent(self) -> bool:
        return bool(object.__getattribute__(self, "_direct_sent"))

    def set_result(self, result: Any) -> None:
        object.__setattr__(self, "_result", result)
        self._capture(result)

    def get_result(self) -> Any:
        return object.__getattribute__(self, "_result")

    def clear_result(self) -> None:
        object.__setattr__(self, "_result", None)

    async def send(self, message: Any) -> bool:
        self._capture(message)
        if object.__getattribute__(self, "_passthrough_send"):
            sender = getattr(object.__getattribute__(self, "_event"), "send", None)
            if callable(sender):
                await sender(message)
                # AstrBot 4.27 event.send() returns None after a successful send.
                object.__setattr__(self, "_direct_sent", True)
                return True
        return True

    def stop_event(self) -> None:
        object.__setattr__(self, "_stopped", True)

    def continue_event(self) -> None:
        object.__setattr__(self, "_stopped", False)

    def is_stopped(self) -> bool:
        return bool(object.__getattribute__(self, "_stopped"))

    def should_call_llm(self, value: bool) -> None:
        object.__setattr__(self, "_call_llm", bool(value))

    def set_extra(self, key: str, value: Any) -> None:
        object.__getattribute__(self, "_extras")[str(key)] = value

    def get_extra(self, key: str, default: Any = None) -> Any:
        extras = object.__getattribute__(self, "_extras")
        if str(key) in extras:
            return extras[str(key)]
        getter = getattr(object.__getattribute__(self, "_event"), "get_extra", None)
        return getter(key, default) if callable(getter) else default

    def _capture(self, value: Any) -> None:
        text = extract_text(value)
        if text and text not in object.__getattribute__(self, "_outputs"):
            object.__getattribute__(self, "_outputs").append(text)


def create_capturing_event(
    event: Any,
    *,
    passthrough_send: bool = False,
) -> CapturingEventProxy:
    """Preserve ``isinstance(event, PlatformEvent)`` when Python layout permits."""

    event_type = type(event)
    if event_type is object or isinstance(event, CapturingEventProxy):
        return CapturingEventProxy(event, passthrough_send=passthrough_send)
    try:
        proxy_type = type(
            "GroupmateCaptured" + event_type.__name__,
            (CapturingEventProxy, event_type),
            {},
        )
        proxy = object.__new__(proxy_type)
        CapturingEventProxy.__init__(
            proxy,
            event,
            passthrough_send=passthrough_send,
        )
        return proxy
    except (TypeError, AttributeError):
        return CapturingEventProxy(event, passthrough_send=passthrough_send)


class HostToolExecutor:
    def __init__(self, context: Any) -> None:
        self.context = context

    async def execute(
        self,
        descriptor: ToolDescriptor,
        arguments: Mapping[str, Any],
        event: Any,
    ) -> ToolExecutionResult:
        if not descriptor.compatible:
            return ToolExecutionResult(
                ToolExecutionStatus.UNSUPPORTED,
                descriptor.tool_id,
                error_code=descriptor.compatibility_reason or "incompatible_tool",
            )
        proxy = create_capturing_event(
            event,
            passthrough_send=descriptor.passthrough_send,
        )
        timed_out = False
        task = asyncio.create_task(
            self._execute(descriptor, dict(arguments or {}), proxy)
        )

        async def _expire() -> None:
            nonlocal timed_out
            try:
                await asyncio.sleep(descriptor.timeout_seconds)
                timed_out = True
                task.cancel()
            except asyncio.CancelledError:
                return

        watcher = asyncio.create_task(_expire())
        result: Any = None
        try:
            result = await task
        except asyncio.CancelledError:
            timed_out = True
        except PermissionError as exc:
            watcher.cancel()
            return ToolExecutionResult(
                ToolExecutionStatus.DENIED,
                descriptor.tool_id,
                outputs=proxy.captured_outputs,
                error_code="permission_denied",
                diagnostic=str(exc),
                direct_sent=proxy.direct_sent,
            )
        except Exception as exc:
            watcher.cancel()
            return ToolExecutionResult(
                ToolExecutionStatus.FAILED,
                descriptor.tool_id,
                outputs=proxy.captured_outputs,
                error_code=exc.__class__.__name__,
                diagnostic=str(exc)[:500],
                direct_sent=proxy.direct_sent,
            )
        finally:
            watcher.cancel()
            try:
                await watcher
            except asyncio.CancelledError:
                pass

        # Some plugins swallow CancelledError; still surface the deadline.
        if timed_out:
            return ToolExecutionResult(
                ToolExecutionStatus.TIMEOUT,
                descriptor.tool_id,
                outputs=proxy.captured_outputs,
                error_code="tool_timeout",
                direct_sent=proxy.direct_sent,
            )

        outputs = list(proxy.captured_outputs)
        returned = extract_text(result)
        if returned and returned not in outputs:
            outputs.append(returned)
        if any(_permission_denied(item) for item in outputs):
            status = ToolExecutionStatus.DENIED
            error_code = "permission_denied"
        else:
            status = ToolExecutionStatus.SUCCESS
            error_code = ""
        return ToolExecutionResult(
            status,
            descriptor.tool_id,
            outputs=tuple(outputs),
            error_code=error_code,
            direct_sent=proxy.direct_sent,
        )

    async def _execute(
        self,
        descriptor: ToolDescriptor,
        arguments: Mapping[str, Any],
        proxy: CapturingEventProxy,
    ) -> Any:
        if descriptor.source is ToolSource.LLM_TOOL:
            return await self._execute_llm_tool(descriptor.native, arguments, proxy)
        if descriptor.source is ToolSource.COMMAND:
            return await self._execute_command(descriptor.native, arguments, proxy)
        raise RuntimeError("unsupported tool source")

    async def _execute_llm_tool(
        self,
        tool: Any,
        arguments: Mapping[str, Any],
        proxy: CapturingEventProxy,
    ) -> Any:
        handler = getattr(tool, "handler", None)
        if callable(handler):
            return await _consume(handler(proxy, **dict(arguments)))

        call = getattr(tool, "call", None)
        if not callable(call):
            raise RuntimeError("tool has no callable entry")
        try:
            from astrbot.core.agent.run_context import ContextWrapper

            wrapped_context = ContextWrapper(
                SimpleNamespace(event=proxy),
                tool_call_timeout=120,
            )
        except Exception:
            wrapped_context = SimpleNamespace(
                context=SimpleNamespace(event=proxy),
                tool_call_timeout=120,
            )
        return await _consume(call(wrapped_context, **dict(arguments)))

    async def _execute_command(
        self,
        native: Any,
        arguments: Mapping[str, Any],
        proxy: CapturingEventProxy,
    ) -> Any:
        try:
            metadata, command_filter = native
        except Exception as exc:
            raise RuntimeError("invalid command metadata") from exc
        await self._check_command_filters(metadata, command_filter, proxy)
        kwargs = _coerce_command_arguments(command_filter, arguments)
        handler = getattr(metadata, "handler", None)
        if not callable(handler):
            raise RuntimeError("command handler unavailable")
        try:
            from astrbot.core.pipeline.context_utils import call_handler

            async for item in call_handler(proxy, handler, **kwargs):
                proxy._capture(item)
        except ImportError:
            return await _consume(handler(proxy, **kwargs))
        return proxy.get_result()

    async def _check_command_filters(
        self,
        metadata: Any,
        command_filter: Any,
        proxy: CapturingEventProxy,
    ) -> None:
        config_getter = getattr(self.context, "get_config", None)
        try:
            config = config_getter() if callable(config_getter) else {}
        except Exception:
            config = {}
        custom_check = getattr(command_filter, "custom_filter_ok", None)
        if callable(custom_check) and not bool(custom_check(proxy, config)):
            raise PermissionError("command custom filter rejected the request")
        for item in getattr(metadata, "event_filters", ()) or ():
            if item is command_filter or item.__class__.__name__ == "CommandFilter":
                continue
            checker = getattr(item, "filter", None)
            if not callable(checker):
                continue
            accepted = checker(proxy, config)
            if inspect.isawaitable(accepted):
                accepted = await accepted
            if not accepted:
                raise PermissionError(
                    "{} rejected the request".format(item.__class__.__name__)
                )


async def _consume(value: Any) -> Any:
    if inspect.isasyncgen(value):
        last = None
        async for item in value:
            last = item
        return last
    if inspect.isawaitable(value):
        return await value
    return value


def _coerce_command_arguments(
    command_filter: Any,
    arguments: Mapping[str, Any],
) -> dict[str, Any]:
    declared = dict(getattr(command_filter, "handler_params", {}) or {})
    result: dict[str, Any] = {}
    for name, value in dict(arguments or {}).items():
        if name not in declared:
            continue
        result[name] = _coerce_value(value, declared[name])
    return result


def _coerce_value(value: Any, annotation_or_default: Any) -> Any:
    target = _resolve_annotation_type(annotation_or_default)
    if target is None:
        # Default-only metadata (e.g. None) — still try common numeric strings.
        parsed = _parse_number_like(value, integer=True)
        return parsed if parsed is not None else value
    if target is bool and isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("true", "yes", "1", "on"):
            return True
        if normalized in ("false", "no", "0", "off"):
            return False
    if target is int:
        parsed = _parse_number_like(value, integer=True)
        if parsed is not None:
            return parsed
    if target is float:
        parsed = _parse_number_like(value, integer=False)
        if parsed is not None:
            return parsed
    if target in (str, int, float, bool) and not isinstance(value, target):
        return target(value)
    return value


def _resolve_annotation_type(annotation_or_default: Any) -> type | None:
    target = annotation_or_default
    origin = typing.get_origin(target)
    if origin in (typing.Union, types.UnionType):
        args = [item for item in typing.get_args(target) if item is not type(None)]
        target = args[0] if len(args) == 1 else None
    if isinstance(target, type):
        return target
    if target is None or target is inspect.Parameter.empty:
        return None
    return type(target)


def _parse_number_like(value: Any, *, integer: bool) -> Any | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return int(value) if integer else float(value)
    if isinstance(value, float):
        return int(value) if integer else float(value)
    text = str(value or "").strip().lower()
    if not text:
        return None
    try:
        return int(text) if integer else float(text)
    except ValueError:
        pass
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    number = match.group(0)
    try:
        return int(float(number)) if integer else float(number)
    except ValueError:
        return None


def extract_text(value: Any) -> str:
    return "\n".join(_extract_texts(value)).strip()


def _extract_texts(value: Any, depth: int = 0) -> Iterable[str]:
    if value is None or depth > 5:
        return ()
    if isinstance(value, str):
        cleaned = value.strip()
        return (cleaned,) if cleaned else ()
    if isinstance(value, bytes):
        return ()
    if isinstance(value, Mapping):
        texts = []
        for key, item in value.items():
            if str(key).lower() in ("diagnostic", "traceback"):
                continue
            texts.extend(_extract_texts(item, depth + 1))
        return tuple(texts)
    if isinstance(value, (list, tuple, set)):
        texts = []
        for item in value:
            texts.extend(_extract_texts(item, depth + 1))
        return tuple(texts)
    for attr in ("text", "message", "result", "chain", "message_chain"):
        if not hasattr(value, attr):
            continue
        try:
            nested = getattr(value, attr)
        except Exception:
            continue
        texts = tuple(_extract_texts(nested, depth + 1))
        if texts:
            return texts
    return ()


def _permission_denied(text: str) -> bool:
    normalized = str(text or "").lower()
    return any(
        marker in normalized
        for marker in (
            "permission denied",
            "权限不足",
            "没有权限",
            "无权",
            "requires admin",
        )
    )
