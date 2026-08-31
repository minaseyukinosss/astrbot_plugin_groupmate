"""Bounded, privacy-safe discovery search contracts."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Mapping, Protocol

from .contracts import SourceEvidence


_INTENTS = {
    "official_next_version",
    "official_recent_update",
    "rumor_next_version",
    "named_fact_verification",
    "unknown_entity_learning",
}
_REQUEST_FIELDS = {
    "request_id",
    "game_entity_id",
    "game_name",
    "entity_id",
    "entity_name",
    "query_intents",
    "region",
    "platform",
    "max_results",
    "deadline",
    "entity_hint",
    "now",
}
_EVIDENCE_FIELDS = {
    "evidence_id",
    "source_id",
    "canonical_url",
    "domain",
    "publisher",
    "source_class",
    "title",
    "published_at",
    "fetched_at",
    "evidence_excerpt",
    "content_hash",
}
_STATUSES = {
    "complete",
    "partial",
    "timed_out",
    "unavailable",
    "failed",
    "invalid_result",
}
_DIAGNOSTICS = {
    "partial_result",
    "search_timed_out",
    "search_unavailable",
    "search_failed",
    "invalid_search_result",
}
_ENTITY_HINT = re.compile(r"^[\w\u3400-\u9fff·・.：:+()（）《》\- ]+$")


def _text(value: object, name: str, maximum: int, *, optional: bool = False):
    normalized = " ".join(
        unicodedata.normalize("NFKC", str(value or "")).split()
    )
    if not normalized:
        if optional:
            return None
        raise ValueError(f"{name} must not be empty")
    if len(normalized) > maximum:
        raise ValueError(f"{name} is too long")
    return normalized


@dataclass(frozen=True)
class SearchRequest:
    request_id: str
    game_entity_id: str
    game_name: str
    entity_id: str | None
    entity_name: str | None
    query_intents: tuple[str, ...]
    region: str | None
    platform: str | None
    max_results: int
    deadline: int
    entity_hint: str | None

    @property
    def queries(self) -> tuple[str, ...]:
        scope = " ".join(
            item for item in (self.region, self.platform) if item
        )
        suffix = f" {scope}" if scope else ""
        templates = {
            "official_next_version": (
                f"{self.game_name} 下一版本 官方公告{suffix}"
            ),
            "official_recent_update": (
                f"{self.game_name} 最近更新 官方公告{suffix}"
            ),
            "rumor_next_version": (
                f"{self.game_name} 下一版本 公开爆料{suffix}"
            ),
            "named_fact_verification": (
                f"{self.game_name} {self.entity_name or ''} "
                f"公开资料核验{suffix}"
            ),
            "unknown_entity_learning": (
                f"{self.game_name} {self.entity_hint or ''} 游戏术语含义"
            ),
        }
        return tuple(templates[intent] for intent in self.query_intents)

    @classmethod
    def create(cls, **values: object) -> "SearchRequest":
        unknown = set(values) - _REQUEST_FIELDS
        if unknown:
            raise ValueError("search request contains forbidden fields")
        now = _integer(values.get("now"), "now")
        deadline = _integer(values.get("deadline"), "deadline")
        if deadline <= now:
            raise ValueError("deadline has expired")
        maximum = _integer(values.get("max_results"), "max_results")
        if not 1 <= maximum <= 4:
            raise ValueError("max_results must be between 1 and 4")
        intents = tuple(
            dict.fromkeys(
                _text(value, "query_intent", 64)
                for value in tuple(values.get("query_intents") or ())
            )
        )
        if not intents or len(intents) > 2 or set(intents) - _INTENTS:
            raise ValueError("query_intents are unsupported")
        if "unknown_entity_learning" in intents and len(intents) != 1:
            raise ValueError("unknown entity learning must be isolated")
        entity_hint = _text(
            values.get("entity_hint"), "entity_hint", 48, optional=True
        )
        if entity_hint is not None and intents != ("unknown_entity_learning",):
            raise ValueError("entity_hint is only allowed for background learning")
        if intents == ("unknown_entity_learning",) and entity_hint is None:
            raise ValueError("unknown entity learning requires entity_hint")
        if entity_hint is not None and not _ENTITY_HINT.fullmatch(entity_hint):
            raise ValueError("entity_hint contains unsafe characters")
        entity_id = _text(
            values.get("entity_id"), "entity_id", 128, optional=True
        )
        entity_name = _text(
            values.get("entity_name"), "entity_name", 80, optional=True
        )
        if "named_fact_verification" in intents and (
            entity_id is None or entity_name is None
        ):
            raise ValueError("named fact verification requires canonical entity")
        game_name = _text(values.get("game_name"), "game_name", 80)
        region = _text(values.get("region"), "region", 48, optional=True)
        platform = _text(
            values.get("platform"), "platform", 48, optional=True
        )
        return cls(
            request_id=_text(values.get("request_id"), "request_id", 128),
            game_entity_id=_text(
                values.get("game_entity_id"), "game_entity_id", 128
            ),
            game_name=game_name,
            entity_id=entity_id,
            entity_name=entity_name,
            query_intents=intents,
            region=region,
            platform=platform,
            max_results=maximum,
            deadline=deadline,
            entity_hint=entity_hint,
        )


@dataclass(frozen=True)
class DiscoverySearchResult:
    request_id: str
    status: str
    candidates: tuple[SourceEvidence, ...]
    completed_at: int
    diagnostic_code: str | None

    @classmethod
    def create(
        cls,
        *,
        request: SearchRequest,
        status: object,
        candidates: object = (),
        completed_at: object,
        diagnostic_code: object = None,
    ) -> "DiscoverySearchResult":
        normalized_status = str(status or "")
        if normalized_status not in _STATUSES:
            raise ValueError("search status is unsupported")
        completed = _integer(completed_at, "completed_at")
        diagnostic = _text(
            diagnostic_code, "diagnostic_code", 64, optional=True
        )
        if normalized_status == "complete" and diagnostic is not None:
            raise ValueError("complete result cannot carry a diagnostic")
        if normalized_status != "complete" and diagnostic not in _DIAGNOSTICS:
            raise ValueError("non-complete result requires a fixed diagnostic")
        normalized_candidates = []
        for value in tuple(candidates or ()):
            if isinstance(value, SourceEvidence):
                candidate = value
            elif isinstance(value, Mapping):
                raw = dict(value)
                if set(raw) != _EVIDENCE_FIELDS:
                    raise ValueError("search candidate contains forbidden fields")
                candidate = SourceEvidence.create(**raw)
            else:
                raise ValueError("search candidate is invalid")
            normalized_candidates.append(candidate)
        if len(normalized_candidates) > min(4, request.max_results):
            raise ValueError("search result exceeds request limit")
        if normalized_status not in {"complete", "partial"} and (
            normalized_candidates
        ):
            raise ValueError("failed search cannot carry candidates")
        return cls(
            request_id=request.request_id,
            status=normalized_status,
            candidates=tuple(normalized_candidates),
            completed_at=completed,
            diagnostic_code=diagnostic,
        )


class DiscoverySearchPort(Protocol):
    async def search(self, request: SearchRequest) -> DiscoverySearchResult:
        """Search only the fixed, canonical queries carried by ``request``."""


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be an integer") from error
    if normalized < 0:
        raise ValueError(f"{name} must not be negative")
    return normalized


__all__ = (
    "DiscoverySearchPort",
    "DiscoverySearchResult",
    "SearchRequest",
)
