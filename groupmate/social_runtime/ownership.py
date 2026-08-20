"""Structured ownership for social messages and external plugin triggers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Iterable, Mapping
from urllib.parse import urlsplit


_URL = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)


class InteractionOwner(str, Enum):
    SOCIAL_RUNTIME = "SOCIAL_RUNTIME"
    EXTERNAL_PLUGIN = "EXTERNAL_PLUGIN"
    PLATFORM = "PLATFORM"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class InteractionOwnership:
    owner: InteractionOwner
    social_eligible: bool
    owner_ref: str | None
    source: str
    trigger_kind: str | None = None
    trigger_value: str | None = None


@dataclass(frozen=True)
class ExternalTriggerPolicy:
    """Deployment-specific rules for plugins that do not stop propagation."""

    command_prefixes: Mapping[str, str]
    link_domains: Mapping[str, str]

    @classmethod
    def create(
        cls,
        *,
        command_prefixes: Mapping[str, str] | None = None,
        link_domains: Mapping[str, str] | None = None,
    ) -> "ExternalTriggerPolicy":
        return cls(
            MappingProxyType(
                cls._normalize_rules(command_prefixes or {}, "command prefix")
            ),
            MappingProxyType(
                cls._normalize_domains(link_domains or {})
            ),
        )

    @classmethod
    def from_entries(
        cls,
        *,
        command_prefixes: Iterable[str] = (),
        link_domains: Iterable[str] = (),
    ) -> "ExternalTriggerPolicy":
        return cls.create(
            command_prefixes=cls._parse_entries(
                command_prefixes, "external command prefix"
            ),
            link_domains=cls._parse_entries(
                link_domains, "external link domain"
            ),
        )

    def classify(self, text: object) -> InteractionOwnership | None:
        value = str(text or "").lstrip()
        lowered = value.casefold()
        for rule, owner_ref in self.command_prefixes.items():
            wildcard = rule.endswith("*")
            exact = rule.endswith("$")
            prefix = rule[:-1] if wildcard or exact else rule
            if not lowered.startswith(prefix):
                continue
            remainder = lowered[len(prefix) :]
            if exact and remainder:
                continue
            if not wildcard and remainder and self._is_token_character(remainder[0]):
                continue
            return InteractionOwnership(
                owner=InteractionOwner.EXTERNAL_PLUGIN,
                social_eligible=False,
                owner_ref=owner_ref,
                source="configured_trigger",
                trigger_kind="command_prefix",
                trigger_value=rule,
            )
        for match in _URL.finditer(value):
            host = (urlsplit(match.group(0)).hostname or "").casefold().rstrip(".")
            for domain, owner_ref in self.link_domains.items():
                if host == domain or host.endswith(f".{domain}"):
                    return InteractionOwnership(
                        owner=InteractionOwner.EXTERNAL_PLUGIN,
                        social_eligible=False,
                        owner_ref=owner_ref,
                        source="configured_trigger",
                        trigger_kind="link_domain",
                        trigger_value=domain,
                    )
        return None

    @staticmethod
    def _is_token_character(value: str) -> bool:
        return value.isascii() and (value.isalnum() or value in {"_", "-"})

    @staticmethod
    def _normalize_rules(values: Mapping[str, str], label: str) -> dict[str, str]:
        result: dict[str, str] = {}
        for raw_rule, raw_owner in values.items():
            rule = str(raw_rule or "").strip().casefold()
            owner = str(raw_owner or "").strip()
            if not rule or not owner:
                raise ValueError(f"{label} rules require a rule and owner")
            if any(character.isspace() for character in rule):
                raise ValueError(f"{label} must not contain whitespace")
            if rule in {"*", "$"} or "*" in rule[:-1] or "$" in rule[:-1]:
                raise ValueError(
                    f"{label} wildcard/exact marker is only allowed at the end"
                )
            if rule.endswith("*$") or rule.endswith("$*"):
                raise ValueError(f"{label} cannot combine wildcard and exact markers")
            if rule in result:
                raise ValueError(f"duplicate {label}: {rule}")
            result[rule] = owner
        return result

    @classmethod
    def _normalize_domains(cls, values: Mapping[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for raw_domain, owner in values.items():
            domain = str(raw_domain or "").strip().casefold().rstrip(".")
            if "://" in domain or "/" in domain or not domain:
                raise ValueError("link domain must be a bare hostname")
            normalized.update(cls._normalize_rules({domain: owner}, "link domain"))
        return normalized

    @staticmethod
    def _parse_entries(values: Iterable[str], label: str) -> dict[str, str]:
        result: dict[str, str] = {}
        for value in values:
            rule, separator, owner = str(value or "").strip().partition("=")
            if not separator or not rule.strip() or not owner.strip():
                raise ValueError(f"{label} entry must use rule=owner")
            result[rule.strip()] = owner.strip()
        return result


__all__ = (
    "ExternalTriggerPolicy",
    "InteractionOwner",
    "InteractionOwnership",
)
