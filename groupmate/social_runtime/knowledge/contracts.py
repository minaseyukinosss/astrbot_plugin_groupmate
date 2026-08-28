"""Immutable contracts for shared game and world knowledge."""

from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping, Protocol, TypeVar


MAX_FRAME_GAMES = 4
MAX_FRAME_ENTITIES = 8
MAX_FRAME_TERMS = 12
MAX_FRAME_REFERENTS = 8
MAX_FRAME_AMBIGUITIES = 12
MAX_FRAME_SUPPORTING_IDS = 16
MAX_PROMPT_CHARS = 1800

_HASH = re.compile(r"^[0-9a-f]{64}$")


class OriginClass(str, Enum):
    SEED = "seed"
    HUMAN_CHAT = "human_chat"
    OWN_OUTPUT = "own_output"
    EXTERNAL_BOT = "external_bot"
    UNKNOWN_ACTOR = "unknown_actor"
    COMMAND = "command"
    FORWARD = "forward"
    OFFICIAL_PAGE = "official_page"
    SEARCH_RESULT = "search_result"
    ADMIN = "admin"


class KnowledgeScope(str, Enum):
    GLOBAL = "global"
    GROUP = "group"


class ObservationStatus(str, Enum):
    PENDING = "pending"
    ADMITTED = "admitted"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ClaimKind(str, Enum):
    STABLE_SEMANTIC = "stable_semantic"
    PUBLIC_FACT = "public_fact"
    RUMOR = "rumor"


class EvidenceLevel(str, Enum):
    BUNDLED = "bundled"
    OFFICIAL = "official"
    CORROBORATED = "corroborated"
    SECONDARY = "secondary"
    UNOFFICIAL = "unofficial"


class ClaimStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    STALE = "stale"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"
    DISPUTED = "disputed"


class KnowledgeNeedOutcome(str, Enum):
    NONE = "none"
    LOCAL_SUFFICIENT = "local_sufficient"
    FRESH_EVIDENCE_REQUIRED = "fresh_evidence_required"
    BACKGROUND_LEARNING = "background_learning"
    UNRESOLVABLE = "unresolvable"


class RiskClass(str, Enum):
    STABLE_SEMANTIC = "stable_semantic"
    VERSION_STATE = "version_state"
    DATE_TIME = "date_time"
    ENTITY_LIST = "entity_list"
    NUMERIC = "numeric"
    OFFICIAL_STATUS = "official_status"
    RUMOR_STATUS = "rumor_status"


EnumType = TypeVar("EnumType", bound=Enum)


def _text(
    value: object,
    name: str,
    *,
    maximum: int,
    optional: bool = False,
) -> str | None:
    if value is None and optional:
        return None
    normalized = " ".join(
        unicodedata.normalize("NFKC", str(value or "")).split()
    )
    if not normalized:
        if optional:
            return None
        raise ValueError(f"{name} must not be empty")
    if len(normalized) > maximum:
        raise ValueError(f"{name} must contain at most {maximum} characters")
    return normalized


def _enum(enum_type: type[EnumType], value: object, name: str) -> EnumType:
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} is unsupported") from exc


def _confidence(value: object, name: str = "confidence") -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be between 0 and 1")
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be between 0 and 1") from exc
    if not math.isfinite(normalized) or not 0.0 <= normalized <= 1.0:
        raise ValueError(f"{name} must be between 0 and 1")
    return normalized


def _timestamp(value: object, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must not be negative")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must not be negative") from exc
    if normalized < 0:
        raise ValueError(f"{name} must not be negative")
    return normalized


def _texts(
    values: Iterable[object],
    name: str,
    *,
    limit: int,
    item_limit: int = 128,
) -> tuple[str, ...]:
    normalized = tuple(
        dict.fromkeys(
            _text(value, name, maximum=item_limit)
            for value in tuple(values or ())
        )
    )
    if len(normalized) > limit:
        raise ValueError(f"{name} exceeds {limit} items")
    return normalized


def _objects(
    values: Iterable[object],
    object_type: type,
    name: str,
    *,
    limit: int,
) -> tuple:
    normalized = []
    for value in tuple(values or ()):
        if isinstance(value, object_type):
            item = value
        elif isinstance(value, Mapping):
            item = object_type.create(**dict(value))
        else:
            raise ValueError(f"{name} contains an invalid item")
        normalized.append(item)
    if len(normalized) > limit:
        raise ValueError(f"{name} exceeds {limit} items")
    return tuple(normalized)


@dataclass(frozen=True)
class ResolvedEntity:
    entity_id: str
    entity_type: str
    canonical_name: str
    canonical_game_id: str
    matched_alias: str | None
    confidence: float
    supporting_knowledge_ids: tuple[str, ...]

    @classmethod
    def create(cls, **values: object) -> "ResolvedEntity":
        return cls(
            entity_id=_text(values.get("entity_id"), "entity_id", maximum=128),
            entity_type=_text(
                values.get("entity_type"), "entity_type", maximum=48
            ),
            canonical_name=_text(
                values.get("canonical_name"), "canonical_name", maximum=80
            ),
            canonical_game_id=_text(
                values.get("canonical_game_id"),
                "canonical_game_id",
                maximum=128,
            ),
            matched_alias=_text(
                values.get("matched_alias"),
                "matched_alias",
                maximum=48,
                optional=True,
            ),
            confidence=_confidence(values.get("confidence")),
            supporting_knowledge_ids=_texts(
                values.get("supporting_knowledge_ids", ()),
                "supporting_knowledge_ids",
                limit=8,
            ),
        )

    def to_prompt_facts(self) -> dict[str, object]:
        return {
            "entity_id": self.entity_id,
            "entity_type": self.entity_type,
            "canonical_name": self.canonical_name,
            "canonical_game_id": self.canonical_game_id,
            "matched_alias": self.matched_alias,
        }


@dataclass(frozen=True)
class ResolvedTerm:
    term_id: str
    canonical_text: str
    meaning_summary: str
    game_id: str
    term_kind: str
    confidence: float
    supporting_knowledge_ids: tuple[str, ...]

    @classmethod
    def create(cls, **values: object) -> "ResolvedTerm":
        return cls(
            term_id=_text(values.get("term_id"), "term_id", maximum=128),
            canonical_text=_text(
                values.get("canonical_text"), "canonical_text", maximum=48
            ),
            meaning_summary=_text(
                values.get("meaning_summary"), "meaning_summary", maximum=160
            ),
            game_id=_text(values.get("game_id"), "game_id", maximum=128),
            term_kind=_text(
                values.get("term_kind"), "term_kind", maximum=48
            ),
            confidence=_confidence(values.get("confidence")),
            supporting_knowledge_ids=_texts(
                values.get("supporting_knowledge_ids", ()),
                "supporting_knowledge_ids",
                limit=8,
            ),
        )

    def to_prompt_facts(self) -> dict[str, object]:
        return {
            "term_id": self.term_id,
            "canonical_text": self.canonical_text,
            "meaning_summary": self.meaning_summary,
            "game_id": self.game_id,
            "term_kind": self.term_kind,
        }


@dataclass(frozen=True)
class DiscourseReferent:
    surface: str
    referent_kind: str
    entity_id: str | None
    confidence: float

    @classmethod
    def create(cls, **values: object) -> "DiscourseReferent":
        kind = _text(
            values.get("referent_kind"), "referent_kind", maximum=32
        )
        if kind not in {"entity", "term", "version", "topic"}:
            raise ValueError("referent_kind is unsupported")
        entity_id = _text(
            values.get("entity_id"), "entity_id", maximum=128, optional=True
        )
        if kind == "entity" and entity_id is None:
            raise ValueError("entity referent requires entity_id")
        return cls(
            surface=_text(values.get("surface"), "surface", maximum=64),
            referent_kind=kind,
            entity_id=entity_id,
            confidence=_confidence(values.get("confidence")),
        )

    def to_prompt_facts(self) -> dict[str, object]:
        return {
            "surface": self.surface,
            "referent_kind": self.referent_kind,
            "entity_id": self.entity_id,
        }


@dataclass(frozen=True)
class VersionReference:
    game_id: str
    relative_kind: str
    disclosure_kind: str
    region: str | None
    platform: str | None
    confidence: float

    @classmethod
    def create(cls, **values: object) -> "VersionReference":
        relative_kind = _text(
            values.get("relative_kind"), "relative_kind", maximum=32
        )
        if relative_kind not in {
            "current",
            "next",
            "previous",
            "recent_update",
            "new",
            "unspecified",
        }:
            raise ValueError("relative_kind is unsupported")
        disclosure_kind = _text(
            values.get("disclosure_kind"), "disclosure_kind", maximum=32
        )
        if disclosure_kind not in {
            "none",
            "official",
            "preview",
            "rumor",
            "test_server",
            "release",
        }:
            raise ValueError("disclosure_kind is unsupported")
        return cls(
            game_id=_text(values.get("game_id"), "game_id", maximum=128),
            relative_kind=relative_kind,
            disclosure_kind=disclosure_kind,
            region=_text(
                values.get("region"), "region", maximum=48, optional=True
            ),
            platform=_text(
                values.get("platform"),
                "platform",
                maximum=48,
                optional=True,
            ),
            confidence=_confidence(values.get("confidence")),
        )

    def to_prompt_facts(self) -> dict[str, object]:
        return {
            "game_id": self.game_id,
            "relative_kind": self.relative_kind,
            "disclosure_kind": self.disclosure_kind,
            "region": self.region,
            "platform": self.platform,
        }


@dataclass(frozen=True)
class TopicUnderstandingFrame:
    frame_id: str
    game_ids: tuple[str, ...]
    resolved_entities: tuple[ResolvedEntity, ...]
    resolved_terms: tuple[ResolvedTerm, ...]
    discourse_referents: tuple[DiscourseReferent, ...]
    version_reference: VersionReference | None
    conversation_intent_hint: str | None
    ambiguity_codes: tuple[str, ...]
    confidence: float
    supporting_knowledge_ids: tuple[str, ...]

    @classmethod
    def create(cls, **values: object) -> "TopicUnderstandingFrame":
        game_ids = _texts(
            values.get("game_ids", ()), "game_ids", limit=MAX_FRAME_GAMES
        )
        entities = _objects(
            values.get("resolved_entities", ()),
            ResolvedEntity,
            "resolved_entities",
            limit=MAX_FRAME_ENTITIES,
        )
        terms = _objects(
            values.get("resolved_terms", ()),
            ResolvedTerm,
            "resolved_terms",
            limit=MAX_FRAME_TERMS,
        )
        referents = _objects(
            values.get("discourse_referents", ()),
            DiscourseReferent,
            "discourse_referents",
            limit=MAX_FRAME_REFERENTS,
        )
        version_value = values.get("version_reference")
        if version_value is None:
            version_reference = None
        elif isinstance(version_value, VersionReference):
            version_reference = version_value
        elif isinstance(version_value, Mapping):
            version_reference = VersionReference.create(**dict(version_value))
        else:
            raise ValueError("version_reference is invalid")
        referenced_games = {
            *(item.canonical_game_id for item in entities),
            *(item.game_id for item in terms),
        }
        if version_reference is not None:
            referenced_games.add(version_reference.game_id)
        if referenced_games - set(game_ids):
            raise ValueError("resolved knowledge must belong to game_ids")
        return cls(
            frame_id=_text(values.get("frame_id"), "frame_id", maximum=128),
            game_ids=game_ids,
            resolved_entities=entities,
            resolved_terms=terms,
            discourse_referents=referents,
            version_reference=version_reference,
            conversation_intent_hint=_text(
                values.get("conversation_intent_hint"),
                "conversation_intent_hint",
                maximum=64,
                optional=True,
            ),
            ambiguity_codes=_texts(
                values.get("ambiguity_codes", ()),
                "ambiguity_codes",
                limit=MAX_FRAME_AMBIGUITIES,
                item_limit=64,
            ),
            confidence=_confidence(values.get("confidence")),
            supporting_knowledge_ids=_texts(
                values.get("supporting_knowledge_ids", ()),
                "supporting_knowledge_ids",
                limit=MAX_FRAME_SUPPORTING_IDS,
            ),
        )

    def to_prompt_facts(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "frame_id": self.frame_id,
            "game_ids": [],
            "resolved_entities": [],
            "resolved_terms": [],
            "discourse_referents": [],
            "version_reference": (
                None
                if self.version_reference is None
                else self.version_reference.to_prompt_facts()
            ),
            "conversation_intent_hint": self.conversation_intent_hint,
            "ambiguity_codes": [],
            "confidence": self.confidence,
            "supporting_knowledge_ids": [],
        }
        candidates = (
            ("game_ids", self.game_ids),
            (
                "resolved_entities",
                tuple(item.to_prompt_facts() for item in self.resolved_entities),
            ),
            (
                "resolved_terms",
                tuple(item.to_prompt_facts() for item in self.resolved_terms),
            ),
            (
                "discourse_referents",
                tuple(item.to_prompt_facts() for item in self.discourse_referents),
            ),
            ("ambiguity_codes", self.ambiguity_codes),
            ("supporting_knowledge_ids", self.supporting_knowledge_ids),
        )
        for field, items in candidates:
            packed = payload[field]
            assert isinstance(packed, list)
            for item in items:
                packed.append(item)
                if len(
                    json.dumps(payload, ensure_ascii=False, sort_keys=True)
                ) > MAX_PROMPT_CHARS:
                    packed.pop()
                    break
        json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
        return payload


@dataclass(frozen=True)
class KnowledgeObservation:
    observation_id: str
    origin_class: OriginClass
    scope_kind: KnowledgeScope
    group_id: str | None
    author_ref: str | None
    source_event_id: str | None
    source_id: str | None
    entity_hint: str | None
    safe_summary: str
    content_hash: str
    occurred_at: int
    recorded_at: int
    status: ObservationStatus

    @classmethod
    def create(cls, **values: object) -> "KnowledgeObservation":
        origin = _enum(
            OriginClass, values.get("origin_class"), "origin_class"
        )
        scope = _enum(KnowledgeScope, values.get("scope_kind"), "scope_kind")
        group_id = _text(
            values.get("group_id"), "group_id", maximum=128, optional=True
        )
        if scope is KnowledgeScope.GLOBAL and group_id is not None:
            raise ValueError("global observation cannot carry group_id")
        if scope is KnowledgeScope.GROUP and group_id is None:
            raise ValueError("group observation requires group_id")
        author_ref = _text(
            values.get("author_ref"),
            "author_ref",
            maximum=128,
            optional=True,
        )
        if author_ref is not None and not (
            scope is KnowledgeScope.GROUP and origin is OriginClass.HUMAN_CHAT
        ):
            raise ValueError("author_ref is only allowed for group human_chat")
        content_hash = _text(
            values.get("content_hash"), "content_hash", maximum=64
        )
        if not _HASH.fullmatch(content_hash):
            raise ValueError("content_hash must be a lowercase SHA-256 digest")
        occurred_at = _timestamp(values.get("occurred_at"), "occurred_at")
        recorded_at = _timestamp(values.get("recorded_at"), "recorded_at")
        if recorded_at < occurred_at:
            raise ValueError("recorded_at cannot precede occurred_at")
        return cls(
            observation_id=_text(
                values.get("observation_id"), "observation_id", maximum=128
            ),
            origin_class=origin,
            scope_kind=scope,
            group_id=group_id,
            author_ref=author_ref,
            source_event_id=_text(
                values.get("source_event_id"),
                "source_event_id",
                maximum=128,
                optional=True,
            ),
            source_id=_text(
                values.get("source_id"),
                "source_id",
                maximum=128,
                optional=True,
            ),
            entity_hint=_text(
                values.get("entity_hint"),
                "entity_hint",
                maximum=48,
                optional=True,
            ),
            safe_summary=_text(
                values.get("safe_summary"), "safe_summary", maximum=240
            ),
            content_hash=content_hash,
            occurred_at=occurred_at,
            recorded_at=recorded_at,
            status=_enum(
                ObservationStatus, values.get("status"), "status"
            ),
        )


@dataclass(frozen=True)
class KnowledgeNeed:
    outcome: KnowledgeNeedOutcome
    gap_codes: tuple[str, ...]
    entity_ids: tuple[str, ...]
    query_intents: tuple[str, ...]
    expires_at: int

    @classmethod
    def create(cls, **values: object) -> "KnowledgeNeed":
        return cls(
            outcome=_enum(
                KnowledgeNeedOutcome, values.get("outcome"), "outcome"
            ),
            gap_codes=_texts(
                values.get("gap_codes", ()),
                "gap_codes",
                limit=8,
                item_limit=64,
            ),
            entity_ids=_texts(
                values.get("entity_ids", ()), "entity_ids", limit=8
            ),
            query_intents=_texts(
                values.get("query_intents", ()),
                "query_intents",
                limit=2,
                item_limit=64,
            ),
            expires_at=_timestamp(values.get("expires_at"), "expires_at"),
        )


class KnowledgeResolverPort(Protocol):
    def resolve(
        self,
        event: object,
        context_events: Iterable[object],
        group_id: str,
        now: int,
    ) -> TopicUnderstandingFrame:
        """Resolve a bounded local frame without network or side effects."""


__all__ = (
    "ClaimKind",
    "ClaimStatus",
    "DiscourseReferent",
    "EvidenceLevel",
    "KnowledgeNeed",
    "KnowledgeNeedOutcome",
    "KnowledgeObservation",
    "KnowledgeResolverPort",
    "KnowledgeScope",
    "ObservationStatus",
    "OriginClass",
    "ResolvedEntity",
    "ResolvedTerm",
    "RiskClass",
    "TopicUnderstandingFrame",
    "VersionReference",
)
