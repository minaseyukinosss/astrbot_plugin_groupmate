"""Static lifecycle and registry assembly for capability providers."""

from __future__ import annotations

from typing import Dict, Iterable, List, Tuple

from .contracts import CapabilityManifest, validate_capability_name
from .provider import CapabilityHealth, CapabilityProvider
from .registry import CapabilityRegistry, CapabilitySpec


class CapabilityProviderRuntime:
    """Own provider lifecycle while exposing the existing static registry."""

    def __init__(
        self,
        providers: Iterable[CapabilityProvider] = (),
    ) -> None:
        self.registry = CapabilityRegistry()
        self._providers: Tuple[CapabilityProvider, ...] = tuple(
            providers or ()
        )
        self._started: List[CapabilityProvider] = []
        self._health: Dict[str, CapabilityHealth] = {}
        self._closed = False
        self._validate_providers()
        self._start_and_register()

    @property
    def closed(self) -> bool:
        return self._closed

    def health(self, capability_name: str) -> CapabilityHealth:
        return self._health[validate_capability_name(capability_name)]

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for provider in reversed(self._started):
            try:
                provider.close()
            except Exception:  # noqa: BLE001 - close remaining providers
                continue

    def _validate_providers(self) -> None:
        seen = set()
        for provider in self._providers:
            if not isinstance(provider, CapabilityProvider):
                raise TypeError(
                    "providers must contain CapabilityProvider values"
                )
            if not isinstance(provider.manifest, CapabilityManifest):
                raise TypeError("provider manifest is required")
            name = provider.manifest.name
            if name in seen:
                raise ValueError("duplicate provider: {}".format(name))
            seen.add(name)

    def _start_and_register(self) -> None:
        for provider in self._providers:
            try:
                provider.start()
                self._started.append(provider)
                health = provider.health()
                if not isinstance(health, CapabilityHealth):
                    raise TypeError(
                        "provider health must be a CapabilityHealth"
                    )
            except Exception:  # noqa: BLE001 - startup fails closed
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
