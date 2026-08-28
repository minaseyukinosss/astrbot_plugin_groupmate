"""Safe official-source probe contracts; this module performs no network I/O."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol
from urllib.parse import urlsplit

from .contracts import SourceClass, SourceEvidence, canonical_source_url


_PROBE_STATUSES = {
    "complete",
    "partial",
    "timed_out",
    "unavailable",
    "failed",
}
_DIAGNOSTIC_CODES = {
    "official_probe_incomplete",
    "official_probe_partial",
    "official_probe_timed_out",
    "official_probe_unavailable",
    "official_probe_failed",
}
def _text(value: object, name: str, maximum: int) -> str:
    normalized = " ".join(str(value or "").split())
    if not normalized:
        raise ValueError(f"{name} must not be empty")
    if len(normalized) > maximum:
        raise ValueError(f"{name} exceeds {maximum} characters")
    return normalized


def _timestamp(value: object, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must not be negative")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must not be negative") from error
    if normalized < 0:
        raise ValueError(f"{name} must not be negative")
    return normalized


def _optional_text(value: object, name: str, maximum: int) -> str | None:
    return None if value is None else _text(value, name, maximum)


class SafeSourceUrlPolicy:
    """Canonicalize registry URLs and reject obvious SSRF targets."""

    def canonicalize(self, value: object) -> str:
        return canonical_source_url(value)


@dataclass(frozen=True)
class OfficialSourceDefinition:
    source_id: str
    game_entity_id: str
    publisher: str
    canonical_url: str
    domain: str
    required: bool

    @classmethod
    def create(cls, **values: object) -> "OfficialSourceDefinition":
        if type(values.get("required")) is not bool:
            raise ValueError("required must be a boolean")
        canonical_url = SafeSourceUrlPolicy().canonicalize(
            values.get("canonical_url")
        )
        hostname = urlsplit(canonical_url).hostname
        assert hostname is not None
        return cls(
            source_id=_text(values.get("source_id"), "source_id", 128),
            game_entity_id=_text(
                values.get("game_entity_id"), "game_entity_id", 128
            ),
            publisher=_text(values.get("publisher"), "publisher", 80),
            canonical_url=canonical_url,
            domain=hostname.casefold(),
            required=bool(values["required"]),
        )


@dataclass(frozen=True)
class OfficialProbeRequest:
    request_id: str
    game_entity_id: str
    query_intent: str
    sources: tuple[OfficialSourceDefinition, ...]
    region: str | None
    platform: str | None
    requested_at: int

    @classmethod
    def create(cls, **values: object) -> "OfficialProbeRequest":
        game_id = _text(
            values.get("game_entity_id"), "game_entity_id", 128
        )
        sources = []
        for value in tuple(values.get("sources") or ()):
            if isinstance(value, OfficialSourceDefinition):
                source = value
            elif isinstance(value, Mapping):
                source = OfficialSourceDefinition.create(**dict(value))
            else:
                raise ValueError("sources contains an invalid definition")
            if source.game_entity_id != game_id:
                raise ValueError("source registry game does not match request")
            sources.append(source)
        if not sources or len(sources) > 16:
            raise ValueError("sources must contain 1-16 definitions")
        if len({item.source_id for item in sources}) != len(sources):
            raise ValueError("source registry contains duplicate source_id")
        return cls(
            request_id=_text(values.get("request_id"), "request_id", 128),
            game_entity_id=game_id,
            query_intent=_text(
                values.get("query_intent"), "query_intent", 64
            ),
            sources=tuple(sources),
            region=_optional_text(values.get("region"), "region", 48),
            platform=_optional_text(
                values.get("platform"), "platform", 48
            ),
            requested_at=_timestamp(
                values.get("requested_at"), "requested_at"
            ),
        )


@dataclass(frozen=True)
class OfficialProbeResult:
    request_id: str
    game_entity_id: str
    status: str
    evidence: tuple[SourceEvidence, ...]
    covered_source_ids: tuple[str, ...]
    diagnostic_code: str | None

    @classmethod
    def create(cls, **values: object) -> "OfficialProbeResult":
        request = values.get("request")
        if not isinstance(request, OfficialProbeRequest):
            raise ValueError("request must be an OfficialProbeRequest")
        status = str(values.get("status") or "")
        if status not in _PROBE_STATUSES:
            raise ValueError("status is unsupported")
        definitions = {item.source_id: item for item in request.sources}
        covered = tuple(
            dict.fromkeys(
                _text(value, "covered_source_ids", 128)
                for value in tuple(values.get("covered_source_ids") or ())
            )
        )
        if set(covered) - set(definitions):
            raise ValueError("covered source is not in registry")
        required = {
            item.source_id for item in request.sources if item.required
        }
        if status == "complete" and not required.issubset(covered):
            raise ValueError("complete probe must cover every required source")
        evidence = []
        for value in tuple(values.get("evidence") or ()):
            if isinstance(value, SourceEvidence):
                item = value
            elif isinstance(value, Mapping):
                item = SourceEvidence.create(**dict(value))
            else:
                raise ValueError("evidence contains an invalid item")
            definition = definitions.get(item.source_id)
            if (
                definition is None
                or item.source_class is not SourceClass.OFFICIAL
                or item.publisher != definition.publisher
                or item.domain != definition.domain
            ):
                raise ValueError("official evidence does not match registry")
            evidence.append(item)
        if len(evidence) > 32:
            raise ValueError("evidence exceeds 32 items")
        diagnostic = values.get("diagnostic_code")
        if status == "complete":
            if diagnostic is not None:
                raise ValueError("complete probe cannot carry diagnostic_code")
            diagnostic_code = None
        else:
            diagnostic_code = _text(
                diagnostic, "diagnostic_code", 64
            )
            if diagnostic_code not in _DIAGNOSTIC_CODES:
                raise ValueError("diagnostic_code is unsupported")
        return cls(
            request_id=request.request_id,
            game_entity_id=request.game_entity_id,
            status=status,
            evidence=tuple(evidence),
            covered_source_ids=covered,
            diagnostic_code=diagnostic_code,
        )


class OfficialSourceProbePort(Protocol):
    async def probe(self, request: OfficialProbeRequest) -> OfficialProbeResult:
        """Probe only registered official sources and return bounded evidence."""


__all__ = (
    "OfficialProbeRequest",
    "OfficialProbeResult",
    "OfficialSourceDefinition",
    "OfficialSourceProbePort",
    "SafeSourceUrlPolicy",
)
