"""Host tool discovery, planning, policy, and execution contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Tuple


class ToolSource(str, Enum):
    CAPABILITY = "capability"
    LLM_TOOL = "llm_tool"
    COMMAND = "command"
    BUILTIN = "builtin"


class ToolRisk(str, Enum):
    READ_ONLY = "read_only"
    NORMAL = "normal"
    DANGEROUS = "dangerous"
    UNKNOWN = "unknown"


class ToolExecutionStatus(str, Enum):
    SUCCESS = "success"
    DENIED = "denied"
    FAILED = "failed"
    TIMEOUT = "timeout"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class ToolDescriptor:
    tool_id: str
    name: str
    description: str
    source: ToolSource
    parameters: Mapping[str, Any] = field(default_factory=dict)
    aliases: Tuple[str, ...] = ()
    plugin_name: str = ""
    handler_module_path: str = ""
    permission: str = "member"
    risk: ToolRisk = ToolRisk.UNKNOWN
    timeout_seconds: float = 30.0
    compatible: bool = True
    compatibility_reason: str = ""
    passthrough_send: bool = False
    native: Any = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        tool_id = str(self.tool_id or "").strip()
        name = str(self.name or "").strip()
        if not tool_id or not name:
            raise ValueError("tool_id and name are required")
        if not isinstance(self.source, ToolSource):
            object.__setattr__(self, "source", ToolSource(str(self.source)))
        if not isinstance(self.risk, ToolRisk):
            object.__setattr__(self, "risk", ToolRisk(str(self.risk)))
        object.__setattr__(self, "tool_id", tool_id)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "description", str(self.description or "").strip())
        object.__setattr__(
            self,
            "aliases",
            tuple(
                dict.fromkeys(
                    str(item).strip()
                    for item in (self.aliases or ())
                    if str(item).strip()
                )
            ),
        )
        object.__setattr__(self, "parameters", dict(self.parameters or {}))
        object.__setattr__(self, "plugin_name", str(self.plugin_name or "").strip())
        object.__setattr__(
            self,
            "handler_module_path",
            str(self.handler_module_path or "").strip(),
        )
        object.__setattr__(self, "permission", str(self.permission or "member").strip())
        object.__setattr__(
            self, "timeout_seconds", max(1.0, float(self.timeout_seconds))
        )
        object.__setattr__(self, "passthrough_send", bool(self.passthrough_send))

    @property
    def required_parameters(self) -> Tuple[str, ...]:
        required = self.parameters.get("required", ())
        if isinstance(required, (str, bytes)):
            return (str(required),)
        return tuple(str(item) for item in (required or ()) if str(item).strip())


@dataclass(frozen=True)
class ToolPlan:
    selected: bool
    tool_id: str = ""
    arguments: Mapping[str, Any] = field(default_factory=dict)
    missing_arguments: Tuple[str, ...] = ()
    reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "selected", bool(self.selected))
        object.__setattr__(self, "tool_id", str(self.tool_id or "").strip())
        object.__setattr__(self, "arguments", dict(self.arguments or {}))
        object.__setattr__(
            self,
            "missing_arguments",
            tuple(
                str(item).strip()
                for item in (self.missing_arguments or ())
                if str(item).strip()
            ),
        )
        object.__setattr__(self, "reason", str(self.reason or "").strip())
        if self.selected and not self.tool_id:
            raise ValueError("selected tool plan requires tool_id")


@dataclass(frozen=True)
class ToolPolicyDecision:
    allowed: bool
    reason: str = ""


@dataclass(frozen=True)
class ToolExecutionResult:
    status: ToolExecutionStatus
    tool_id: str
    outputs: Tuple[str, ...] = ()
    error_code: str = ""
    diagnostic: str = ""
    direct_sent: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.status, ToolExecutionStatus):
            object.__setattr__(
                self, "status", ToolExecutionStatus(str(self.status))
            )
        object.__setattr__(self, "tool_id", str(self.tool_id or "").strip())
        object.__setattr__(
            self,
            "outputs",
            tuple(
                str(item).strip()
                for item in (self.outputs or ())
                if str(item).strip()
            ),
        )
        object.__setattr__(self, "error_code", str(self.error_code or "").strip())
        object.__setattr__(self, "diagnostic", str(self.diagnostic or "").strip())
        object.__setattr__(self, "direct_sent", bool(self.direct_sent))
