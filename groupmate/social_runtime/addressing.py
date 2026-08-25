"""Deterministic Persona addressing facts derived from one message."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class AddressResolution:
    addressed_to_bot: bool
    address_kind: str
    matched_alias: str | None
    address_remainder: str
    address_confidence: str
    alias_candidate: str | None = None


class PersonaAddressResolver:
    """Resolve confirmed Persona names without fuzzy or substring matching."""

    _CALL_PREFIXES = (
        "呢",
        "在",
        "说",
        "讲",
        "帮",
        "看",
        "回",
        "你",
        "来",
        "给",
        "能",
        "会",
        "要",
        "别",
    )
    _LEADING_SEPARATORS = " ，,。.!！?？~～…:：、"
    _SUFFIX_SEPARATORS = " ，,：:、"

    def __init__(self, primary_name: str, aliases: tuple[str, ...] = ()) -> None:
        names = tuple(
            dict.fromkeys(
                (str(primary_name).strip(), *(str(alias).strip() for alias in aliases))
            )
        )
        if any(not value for value in names):
            raise ValueError("persona address names must not be empty")
        self.names = tuple(sorted(names, key=len, reverse=True))

    def resolve(
        self, *, text: str, mentions_bot: bool, reply_to_bot: bool
    ) -> AddressResolution:
        normalized = self._normalize(text)
        if reply_to_bot:
            return AddressResolution(True, "REPLY", None, normalized, "HIGH")
        if mentions_bot:
            return AddressResolution(True, "AT", None, normalized, "HIGH")
        return self.resolve_text(normalized)

    def resolve_text(self, text: str) -> AddressResolution:
        value = self._normalize(text)
        for name in self.names:
            if value == name:
                return AddressResolution(True, "PURE_ALIAS", name, "", "HIGH")
            if value.startswith(name):
                suffix = value[len(name) :]
                if self._prefix_boundary(suffix):
                    remainder = suffix.lstrip(self._LEADING_SEPARATORS)
                    return AddressResolution(
                        True,
                        "ALIAS_PREFIX",
                        name,
                        remainder,
                        "HIGH",
                        self._candidate(remainder),
                    )
            if value.endswith(name):
                leading = value[: -len(name)]
                if leading and leading[-1] in self._SUFFIX_SEPARATORS:
                    remainder = leading.rstrip(self._SUFFIX_SEPARATORS)
                    if remainder:
                        return AddressResolution(
                            True,
                            "ALIAS_SUFFIX",
                            name,
                            remainder,
                            "HIGH",
                        )
        return AddressResolution(False, "NONE", None, value, "NONE")

    @classmethod
    def _prefix_boundary(cls, suffix: str) -> bool:
        if not suffix:
            return True
        if suffix[0] in cls._LEADING_SEPARATORS:
            return True
        if suffix.startswith("以后叫你"):
            return True
        return any(suffix.startswith(prefix) for prefix in cls._CALL_PREFIXES)

    @staticmethod
    def _candidate(remainder: str) -> str | None:
        match = re.fullmatch(
            r"(?:以后)?叫你([\w\u3400-\u9fff·]{2,24})[。.!！?？~～]*",
            remainder,
        )
        return match.group(1) if match else None

    @staticmethod
    def _normalize(text: object) -> str:
        return " ".join(str(text or "").strip().split())


__all__ = ("AddressResolution", "PersonaAddressResolver")
