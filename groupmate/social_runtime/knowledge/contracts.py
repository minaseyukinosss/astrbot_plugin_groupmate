"""Immutable contracts for shared game and world knowledge."""

from __future__ import annotations

import json
import ipaddress
import math
import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping, Protocol, TypeVar
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


MAX_FRAME_GAMES = 4
MAX_FRAME_ENTITIES = 8
MAX_FRAME_TERMS = 12
MAX_FRAME_REFERENTS = 8
MAX_FRAME_AMBIGUITIES = 12
MAX_FRAME_SUPPORTING_IDS = 16
MAX_PROMPT_CHARS = 1800

_HASH = re.compile(r"^[0-9a-f]{64}$")
_NUMERIC_HOST = re.compile(
    r"(?:0x[0-9a-f]+|[0-9]+)(?:\.(?:0x[0-9a-f]+|[0-9]+))*$",
    re.IGNORECASE,
)
_BLOCKED_SOURCE_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
}
_SEMANTIC_SOURCE_QUERY_KEYS = {
    "article_id",
    "id",
    "lang",
    "locale",
    "news_id",
    "p",
    "page",
    "version",
}


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


class SourceClass(str, Enum):
    OFFICIAL = "official"
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


def _optional_timestamp(value: object, name: str) -> int | None:
    return None if value is None else _timestamp(value, name)


def _positive_revision(value: object, name: str = "revision") -> int:
    normalized = _timestamp(value, name)
    if normalized < 1:
        raise ValueError(f"{name} must be positive")
    return normalized


def canonical_source_url(value: object) -> str:
    raw = str(value or "").strip()
    if not raw or len(raw) > 2048:
        raise ValueError("URL must be a safe public HTTP(S) URL")
    if any(character.isspace() or ord(character) < 32 for character in raw):
        raise ValueError("URL must be a safe public HTTP(S) URL")
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as error:
        raise ValueError("URL must be a safe public HTTP(S) URL") from error
    scheme = parsed.scheme.casefold()
    hostname = (parsed.hostname or "").rstrip(".").casefold()
    if (
        scheme not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or hostname in _BLOCKED_SOURCE_HOSTS
        or hostname.endswith((".localhost", ".local", ".internal"))
        or not hostname.isascii()
        or _NUMERIC_HOST.fullmatch(hostname)
    ):
        raise ValueError("URL must be a safe public HTTP(S) URL")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("URL must be a safe public HTTP(S) URL")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("URL must be a safe public HTTP(S) URL")
    default_port = (scheme == "https" and port == 443) or (
        scheme == "http" and port == 80
    )
    display_host = f"[{hostname}]" if ":" in hostname else hostname
    netloc = (
        display_host
        if port is None or default_port
        else f"{display_host}:{port}"
    )
    query = urlencode(
        sorted(
            (key.casefold(), item)
            for key, item in parse_qsl(
                parsed.query, keep_blank_values=True, strict_parsing=False
            )
            if key.casefold() in _SEMANTIC_SOURCE_QUERY_KEYS
        ),
        doseq=True,
    )
    canonical = urlunsplit(
        (scheme, netloc, parsed.path or "/", query, "")
    )
    if len(canonical) > 2048:
        raise ValueError("URL must be a safe public HTTP(S) URL")
    return canonical


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


@dataclass(frozen=True)
class SourceEvidence:
    evidence_id: str
    source_id: str
    canonical_url: str
    domain: str
    publisher: str
    source_class: SourceClass
    title: str
    published_at: int | None
    fetched_at: int
    evidence_excerpt: str
    content_hash: str

    @classmethod
    def create(cls, **values: object) -> "SourceEvidence":
        canonical_url = canonical_source_url(values.get("canonical_url"))
        parsed = urlsplit(canonical_url)
        domain = _text(values.get("domain"), "domain", maximum=253).casefold()
        if parsed.hostname.casefold() != domain:
            raise ValueError("domain must match canonical_url")
        content_hash = _text(
            values.get("content_hash"), "content_hash", maximum=64
        )
        if not _HASH.fullmatch(content_hash):
            raise ValueError("content_hash must be a lowercase SHA-256 digest")
        published_at = _optional_timestamp(
            values.get("published_at"), "published_at"
        )
        fetched_at = _timestamp(values.get("fetched_at"), "fetched_at")
        if published_at is not None and published_at > fetched_at:
            raise ValueError("published_at cannot follow fetched_at")
        return cls(
            evidence_id=_text(
                values.get("evidence_id"), "evidence_id", maximum=128
            ),
            source_id=_text(
                values.get("source_id"), "source_id", maximum=128
            ),
            canonical_url=canonical_url,
            domain=domain,
            publisher=_text(
                values.get("publisher"), "publisher", maximum=80
            ),
            source_class=_enum(
                SourceClass, values.get("source_class"), "source_class"
            ),
            title=_text(values.get("title"), "title", maximum=180),
            published_at=published_at,
            fetched_at=fetched_at,
            evidence_excerpt=_text(
                values.get("evidence_excerpt"),
                "evidence_excerpt",
                maximum=320,
            ),
            content_hash=content_hash,
        )


@dataclass(frozen=True)
class KnowledgeClaimCandidate:
    candidate_id: str
    subject_entity_id: str
    predicate: str
    safe_summary: str
    claim_kind: ClaimKind
    evidence_level: EvidenceLevel
    applies_to_version_slot_id: str | None
    region: str | None
    platform: str | None
    valid_from: int | None
    valid_until: int | None
    checked_at: int

    @classmethod
    def create(cls, **values: object) -> "KnowledgeClaimCandidate":
        claim_kind = _enum(
            ClaimKind, values.get("claim_kind"), "claim_kind"
        )
        checked_at = _timestamp(values.get("checked_at"), "checked_at")
        valid_from = _optional_timestamp(
            values.get("valid_from"), "valid_from"
        )
        valid_until = _optional_timestamp(
            values.get("valid_until"), "valid_until"
        )
        if claim_kind in {ClaimKind.PUBLIC_FACT, ClaimKind.RUMOR} and (
            valid_until is None
        ):
            raise ValueError("valid_until is required for temporal claims")
        if valid_until is not None and valid_until <= (
            valid_from if valid_from is not None else checked_at
        ):
            raise ValueError("valid_until must follow claim validity start")
        return cls(
            candidate_id=_text(
                values.get("candidate_id"), "candidate_id", maximum=128
            ),
            subject_entity_id=_text(
                values.get("subject_entity_id"),
                "subject_entity_id",
                maximum=128,
            ),
            predicate=_text(
                values.get("predicate"), "predicate", maximum=80
            ),
            safe_summary=_text(
                values.get("safe_summary"), "safe_summary", maximum=240
            ),
            claim_kind=claim_kind,
            evidence_level=_enum(
                EvidenceLevel,
                values.get("evidence_level"),
                "evidence_level",
            ),
            applies_to_version_slot_id=_text(
                values.get("applies_to_version_slot_id"),
                "applies_to_version_slot_id",
                maximum=128,
                optional=True,
            ),
            region=_text(
                values.get("region"), "region", maximum=48, optional=True
            ),
            platform=_text(
                values.get("platform"),
                "platform",
                maximum=48,
                optional=True,
            ),
            valid_from=valid_from,
            valid_until=valid_until,
            checked_at=checked_at,
        )


@dataclass(frozen=True)
class VersionSlot:
    version_slot_id: str
    game_entity_id: str
    official_label: str | None
    region: str
    platform: str
    release_state: str
    official_state: str
    rumor_state: str
    announced_at: int | None
    release_at: int | None
    effective_until: int | None
    official_checked_at: int | None
    rumor_checked_at: int | None
    fresh_until: int
    status: str
    revision: int

    @classmethod
    def create(cls, **values: object) -> "VersionSlot":
        release_state = _text(
            values.get("release_state"), "release_state", maximum=16
        )
        official_state = _text(
            values.get("official_state"), "official_state", maximum=16
        )
        rumor_state = _text(
            values.get("rumor_state"), "rumor_state", maximum=24
        )
        status = _text(values.get("status"), "status", maximum=16)
        if release_state not in {"future", "current", "past"}:
            raise ValueError("release_state is unsupported")
        if official_state not in {
            "none",
            "teaser",
            "preview",
            "notice",
            "released",
        }:
            raise ValueError("official_state is unsupported")
        if rumor_state not in {
            "none_observed",
            "weak",
            "corroborated",
            "conflicted",
            "stale",
        }:
            raise ValueError("rumor_state is unsupported")
        if status not in {"active", "superseded", "disputed"}:
            raise ValueError("status is unsupported")
        release_at = _optional_timestamp(
            values.get("release_at"), "release_at"
        )
        if (official_state == "released") != (
            release_state in {"current", "past"}
        ):
            raise ValueError(
                "released official state must match current or past release state"
            )
        if official_state == "released" and release_at is None:
            raise ValueError("released official state requires release_at")
        announced_at = _optional_timestamp(
            values.get("announced_at"), "announced_at"
        )
        official_checked_at = _optional_timestamp(
            values.get("official_checked_at"), "official_checked_at"
        )
        rumor_checked_at = _optional_timestamp(
            values.get("rumor_checked_at"), "rumor_checked_at"
        )
        fresh_until = _timestamp(
            values.get("fresh_until"), "fresh_until"
        )
        if official_state != "none" and official_checked_at is None:
            raise ValueError(
                "official_checked_at is required for official state"
            )
        if rumor_state != "none_observed" and rumor_checked_at is None:
            raise ValueError("rumor_checked_at is required for rumor state")
        if official_state != "none" and announced_at is None:
            raise ValueError("announced_at is required for official state")
        if official_state == "none" and values.get("official_label") is not None:
            raise ValueError("official_label requires official state")
        if (
            announced_at is not None
            and release_at is not None
            and announced_at > release_at
        ):
            raise ValueError("announced_at cannot follow release_at")
        effective_until = _optional_timestamp(
            values.get("effective_until"), "effective_until"
        )
        if (
            release_at is not None
            and effective_until is not None
            and effective_until <= release_at
        ):
            raise ValueError("effective_until must follow release_at")
        supporting_checks = tuple(
            value
            for value in (official_checked_at, rumor_checked_at)
            if value is not None
        )
        if supporting_checks and fresh_until <= max(supporting_checks):
            raise ValueError("fresh_until must follow supporting checks")
        return cls(
            version_slot_id=_text(
                values.get("version_slot_id"),
                "version_slot_id",
                maximum=128,
            ),
            game_entity_id=_text(
                values.get("game_entity_id"),
                "game_entity_id",
                maximum=128,
            ),
            official_label=_text(
                values.get("official_label"),
                "official_label",
                maximum=80,
                optional=True,
            ),
            region=_text(values.get("region"), "region", maximum=48),
            platform=_text(
                values.get("platform"), "platform", maximum=48
            ),
            release_state=release_state,
            official_state=official_state,
            rumor_state=rumor_state,
            announced_at=announced_at,
            release_at=release_at,
            effective_until=effective_until,
            official_checked_at=official_checked_at,
            rumor_checked_at=rumor_checked_at,
            fresh_until=fresh_until,
            status=status,
            revision=_positive_revision(values.get("revision")),
        )


@dataclass(frozen=True)
class NegativeSearchSnapshot:
    snapshot_id: str
    game_entity_id: str
    query_intent: str
    probe_status: str
    covered_source_ids: tuple[str, ...]
    required_source_ids: tuple[str, ...]
    region: str | None
    platform: str | None
    checked_at: int
    expires_at: int
    version_state_revision: int
    status: str
    diagnostic_code: str

    @classmethod
    def create(cls, **values: object) -> "NegativeSearchSnapshot":
        probe_status = str(values.get("probe_status") or "")
        covered = _texts(
            values.get("covered_source_ids", ()),
            "covered_source_ids",
            limit=16,
        )
        required = _texts(
            values.get("required_source_ids", ()),
            "required_source_ids",
            limit=16,
        )
        if (
            probe_status != "complete"
            or not required
            or not set(required).issubset(covered)
        ):
            raise ValueError(
                "negative snapshot requires complete covered probe"
            )
        checked_at = _timestamp(values.get("checked_at"), "checked_at")
        expires_at = _timestamp(values.get("expires_at"), "expires_at")
        if expires_at <= checked_at:
            raise ValueError("expires_at must follow checked_at")
        return cls(
            snapshot_id=_text(
                values.get("snapshot_id"), "snapshot_id", maximum=128
            ),
            game_entity_id=_text(
                values.get("game_entity_id"),
                "game_entity_id",
                maximum=128,
            ),
            query_intent=_text(
                values.get("query_intent"), "query_intent", maximum=64
            ),
            probe_status=probe_status,
            covered_source_ids=covered,
            required_source_ids=required,
            region=_text(
                values.get("region"), "region", maximum=48, optional=True
            ),
            platform=_text(
                values.get("platform"),
                "platform",
                maximum=48,
                optional=True,
            ),
            checked_at=checked_at,
            expires_at=expires_at,
            version_state_revision=_positive_revision(
                values.get("version_state_revision"),
                "version_state_revision",
            ),
            status="active",
            diagnostic_code="official_no_matching_update",
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
    "KnowledgeClaimCandidate",
    "KnowledgeObservation",
    "KnowledgeResolverPort",
    "KnowledgeScope",
    "ObservationStatus",
    "OriginClass",
    "NegativeSearchSnapshot",
    "ResolvedEntity",
    "ResolvedTerm",
    "RiskClass",
    "SourceClass",
    "SourceEvidence",
    "TopicUnderstandingFrame",
    "VersionReference",
    "VersionSlot",
    "canonical_source_url",
)
