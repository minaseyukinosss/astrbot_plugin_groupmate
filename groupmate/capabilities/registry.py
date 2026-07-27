"""Static capability registration and controlled async execution."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, Optional, Sequence

from ..core.response_act import TaskResolution, TaskResolutionStatus
from .contracts import (
    CapabilityRequest,
    CapabilityResult,
    CapabilityStatus,
    validate_capability_name,
)


CapabilityExecutor = Callable[[CapabilityRequest], Awaitable[CapabilityResult]]
InformationMatcher = Callable[[CapabilityRequest], Sequence[str]]


@dataclass(frozen=True)
class CapabilitySpec:
    name: str
    executor: CapabilityExecutor
    required_information: Optional[InformationMatcher] = None
    available: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", validate_capability_name(self.name))
        if not callable(self.executor):
            raise TypeError("capability executor must be callable")
        if self.required_information is not None and not callable(
            self.required_information
        ):
            raise TypeError("required_information must be callable")
        if not isinstance(self.available, bool):
            raise TypeError("available must be a bool")


class CapabilityRegistry:
    """Registry that can execute only explicitly registered capability specs."""

    def __init__(self, default_timeout_seconds: float = 10.0) -> None:
        if default_timeout_seconds <= 0:
            raise ValueError("default_timeout_seconds must be positive")
        self._default_timeout_seconds = float(default_timeout_seconds)
        self._specs: Dict[str, CapabilitySpec] = {}

    def register(self, spec: CapabilitySpec) -> None:
        if not isinstance(spec, CapabilitySpec):
            raise TypeError("spec must be a CapabilitySpec")
        if spec.name in self._specs:
            raise ValueError("capability already registered: {}".format(spec.name))
        self._specs[spec.name] = spec

    def lookup(self, capability_name: str) -> Optional[CapabilitySpec]:
        name = validate_capability_name(capability_name)
        return self._specs.get(name)

    def describe(self, capability_name: str) -> TaskResolution:
        name = validate_capability_name(capability_name)
        spec = self._specs.get(name)
        return TaskResolution(
            status=(
                TaskResolutionStatus.SUPPORTED
                if spec is not None and spec.available
                else TaskResolutionStatus.UNSUPPORTED
            ),
            capability_name=name,
        )

    def resolve(self, request: CapabilityRequest) -> TaskResolution:
        if not isinstance(request, CapabilityRequest):
            raise TypeError("request must be a CapabilityRequest")
        spec = self._specs.get(request.capability_name)
        if spec is None or not spec.available:
            return TaskResolution(
                status=TaskResolutionStatus.UNSUPPORTED,
                capability_name=request.capability_name,
            )
        if spec.required_information is None:
            required_information = ()
        else:
            try:
                required_information = spec.required_information(request)
                if isinstance(required_information, (str, bytes)):
                    raise TypeError("required information must be a sequence")
                required_information = tuple(required_information or ())
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - support resolution fails closed
                return TaskResolution(
                    status=TaskResolutionStatus.UNKNOWN,
                    capability_name=request.capability_name,
                )
        return TaskResolution(
            status=TaskResolutionStatus.SUPPORTED,
            capability_name=request.capability_name,
            required_information=required_information,
        )

    async def execute(
        self,
        request: CapabilityRequest,
        timeout_seconds: Optional[float] = None,
    ) -> CapabilityResult:
        if not isinstance(request, CapabilityRequest):
            raise TypeError("request must be a CapabilityRequest")
        spec = self._specs.get(request.capability_name)
        if spec is None:
            return self._unsupported(request.capability_name, "capability_not_registered")
        if not spec.available:
            return self._unsupported(request.capability_name, "capability_unavailable")
        timeout = (
            self._default_timeout_seconds
            if timeout_seconds is None
            else float(timeout_seconds)
        )
        if timeout <= 0:
            raise ValueError("timeout_seconds must be positive")
        try:
            result = await asyncio.wait_for(spec.executor(request), timeout=timeout)
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            return CapabilityResult(
                CapabilityStatus.TIMEOUT,
                request.capability_name,
                user_text="The capability timed out before completing.",
                error_code="execution_timeout",
            )
        except Exception as exc:  # noqa: BLE001 - capability boundary fails closed
            return CapabilityResult(
                CapabilityStatus.FAILED,
                request.capability_name,
                user_text="The capability could not complete the request.",
                error_code="execution_error",
                diagnostic=type(exc).__name__,
            )
        if not isinstance(result, CapabilityResult):
            return self._invalid_result(request.capability_name)
        if result.capability_name != request.capability_name:
            return self._invalid_result(request.capability_name)
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
    def _invalid_result(capability_name: str) -> CapabilityResult:
        return CapabilityResult(
            CapabilityStatus.FAILED,
            capability_name,
            user_text="The capability returned an invalid result.",
            error_code="invalid_result",
        )
