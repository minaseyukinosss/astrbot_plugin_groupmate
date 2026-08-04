"""Static ordered dispatcher for host event adapters."""

from __future__ import annotations

from typing import Any, Iterable, Tuple

from .base import (
    HostEventAdapter,
    HostEventAdapterResult,
    HostEventAdapterStatus,
)


class HostEventAdapterRuntime:
    def __init__(
        self,
        adapters: Iterable[HostEventAdapter] = (),
    ) -> None:
        values = tuple(adapters or ())
        names = set()
        event_kinds = set()
        for adapter in values:
            if not isinstance(adapter, HostEventAdapter):
                raise TypeError("runtime values must be host event adapters")
            manifest = adapter.manifest
            if manifest.name in names:
                raise ValueError(
                    "duplicate adapter name: {}".format(manifest.name)
                )
            names.add(manifest.name)
            for event_kind in manifest.event_kinds:
                if event_kind in event_kinds:
                    raise ValueError(
                        "duplicate event kind: {}".format(event_kind)
                    )
                event_kinds.add(event_kind)
        self._adapters = values

    @property
    def adapters(self) -> Tuple[HostEventAdapter, ...]:
        return self._adapters

    def adapt(self, event: Any) -> HostEventAdapterResult:
        for adapter in self._adapters:
            try:
                result = adapter.adapt(event)
                HostEventAdapterResult.validate(result)
            except Exception:
                return HostEventAdapterResult.bypassed("adapter_error")
            if result.status is not HostEventAdapterStatus.NOT_MATCHED:
                return result
        return HostEventAdapterResult.not_matched()
