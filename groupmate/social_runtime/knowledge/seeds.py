"""Validated, versioned stable-semantic seeds for supported games."""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit

from .repository import (
    KnowledgeRepository,
    SeedVersionConflict,
    SeedVersionOrderConflict,
)


_ASSET_DIRECTORY = Path(__file__).with_name("assets")
_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_BANNED_KEYS = frozenset(
    {
        "current_banner",
        "current_version",
        "latest_numbers",
        "tier_list",
        "leak_content",
    }
)
_ALIAS_KINDS = frozenset(
    {"official", "translation", "abbreviation", "community"}
)
_AMBIGUITY_LEVELS = frozenset({"none", "contextual", "high"})


class SeedValidationError(ValueError):
    """Raised when a bundled or operator-provided seed is unsafe."""


class SeedHashConflict(RuntimeError):
    """Raised when a published seed version is silently rewritten."""


class SeedVersionOrderError(RuntimeError):
    """Raised when an older absent seed would displace a newer seed."""


@dataclass(frozen=True)
class SeedAlias:
    text: str
    kind: str
    ambiguity_level: str
    requires_any_context: tuple[str, ...]


@dataclass(frozen=True)
class SeedGame:
    entity_id: str
    canonical_name: str
    english_name: str
    aliases: tuple[SeedAlias, ...]


@dataclass(frozen=True)
class SeedEntity:
    entity_id: str
    entity_type: str
    canonical_name: str


@dataclass(frozen=True)
class SeedTerm:
    term_id: str
    text: str
    meaning_summary: str
    term_kind: str
    ambiguity_level: str
    requires_any_context: tuple[str, ...]


@dataclass(frozen=True)
class StableRelation:
    relation_id: str
    subject_entity_id: str
    predicate: str
    object_entity_id: str
    safe_summary: str


@dataclass(frozen=True)
class OfficialSourceSeed:
    source_id: str
    publisher: str
    domain: str
    url: str
    required: bool


@dataclass(frozen=True)
class GameSemanticSeed:
    seed_id: str
    seed_version: int
    game: SeedGame
    entity_types: tuple[str, ...]
    entities: tuple[SeedEntity, ...]
    terms: tuple[SeedTerm, ...]
    stable_relations: tuple[StableRelation, ...]
    discussion_patterns: tuple[str, ...]
    official_sources: tuple[OfficialSourceSeed, ...]
    content_hash: str
    manifest: Mapping[str, Any]


@dataclass(frozen=True)
class SeedImportReport:
    imported_seed_ids: tuple[str, ...] = ()
    unchanged_seed_ids: tuple[str, ...] = ()


def _text(value: object, field: str, *, maximum: int = 500) -> str:
    normalized = " ".join(str(value or "").split())
    if not normalized:
        raise SeedValidationError("{} must not be empty".format(field))
    if len(normalized) > maximum:
        raise SeedValidationError("{} is too long".format(field))
    return normalized


def _sequence(value: object, field: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise SeedValidationError("{} must be a list".format(field))
    return value


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise SeedValidationError("{} must be an object".format(field))
    return value


def _reject_banned_keys(value: object, location: str = "manifest") -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in _BANNED_KEYS:
                raise SeedValidationError(
                    "{} contains banned temporal field {}".format(location, key)
                )
            _reject_banned_keys(nested, "{}.{}".format(location, key))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _reject_banned_keys(nested, "{}[{}]".format(location, index))


def _canonical_hash(manifest: Mapping[str, Any]) -> str:
    canonical = dict(manifest)
    canonical.pop("content_hash", None)
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _alias(value: object, field: str) -> SeedAlias:
    data = _mapping(value, field)
    ambiguity = _text(data.get("ambiguity_level"), field + ".ambiguity_level")
    if ambiguity not in _AMBIGUITY_LEVELS:
        raise SeedValidationError("{} has invalid ambiguity_level".format(field))
    context = tuple(
        _text(item, field + ".requires_any_context", maximum=48)
        for item in _sequence(
            data.get("requires_any_context"), field + ".requires_any_context"
        )
    )
    if ambiguity == "high" and not context:
        raise SeedValidationError(
            "{}.requires_any_context is required for high ambiguity".format(
                field
            )
        )
    kind = _text(data.get("kind"), field + ".kind")
    if kind not in _ALIAS_KINDS:
        raise SeedValidationError("{} has invalid alias kind".format(field))
    return SeedAlias(
        text=_text(data.get("text"), field + ".text", maximum=48),
        kind=kind,
        ambiguity_level=ambiguity,
        requires_any_context=context,
    )


def load_seed_asset(path: Path) -> GameSemanticSeed:
    asset_path = Path(path)
    try:
        manifest = json.loads(asset_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SeedValidationError(
            "cannot read seed asset {}: {}".format(asset_path, error)
        ) from error
    manifest = _mapping(manifest, "manifest")
    _reject_banned_keys(manifest)

    seed_id = _text(manifest.get("seed_id"), "seed_id", maximum=128)
    seed_version = manifest.get("seed_version")
    if isinstance(seed_version, bool) or not isinstance(seed_version, int):
        raise SeedValidationError("seed_version must be a positive integer")
    if seed_version <= 0:
        raise SeedValidationError("seed_version must be a positive integer")
    supplied_hash = _text(manifest.get("content_hash"), "content_hash")
    expected_hash = _canonical_hash(manifest)
    if not _HASH_PATTERN.fullmatch(supplied_hash):
        raise SeedValidationError("content_hash must be 64 lowercase hex digits")
    if supplied_hash != expected_hash:
        raise SeedValidationError("content_hash does not match seed content")

    game_data = _mapping(manifest.get("game"), "game")
    aliases = tuple(
        _alias(item, "game.aliases[{}]".format(index))
        for index, item in enumerate(
            _sequence(game_data.get("aliases"), "game.aliases")
        )
    )
    if not aliases:
        raise SeedValidationError("game.aliases must not be empty")
    game = SeedGame(
        entity_id=_text(game_data.get("entity_id"), "game.entity_id"),
        canonical_name=_text(
            game_data.get("canonical_name"), "game.canonical_name", maximum=80
        ),
        english_name=_text(
            game_data.get("english_name"), "game.english_name", maximum=80
        ),
        aliases=aliases,
    )

    entity_types = tuple(
        _text(item, "entity_types", maximum=48)
        for item in _sequence(manifest.get("entity_types"), "entity_types")
    )
    if len(set(entity_types)) < 5:
        raise SeedValidationError("entity_types must contain at least five types")
    if "game" not in entity_types:
        raise SeedValidationError("entity_types must include game")

    entities = tuple(
        SeedEntity(
            entity_id=_text(item.get("entity_id"), "entity.entity_id"),
            entity_type=_text(
                item.get("entity_type"), "entity.entity_type", maximum=48
            ),
            canonical_name=_text(
                item.get("canonical_name"),
                "entity.canonical_name",
                maximum=80,
            ),
        )
        for item in (
            _mapping(raw, "entities[{}]".format(index))
            for index, raw in enumerate(
                _sequence(manifest.get("entities"), "entities")
            )
        )
    )
    for entity in entities:
        if entity.entity_type not in entity_types:
            raise SeedValidationError(
                "entity type {} is not declared".format(entity.entity_type)
            )

    terms = []
    for index, raw in enumerate(_sequence(manifest.get("terms"), "terms")):
        item = _mapping(raw, "terms[{}]".format(index))
        ambiguity = _text(
            item.get("ambiguity_level"), "term.ambiguity_level"
        )
        if ambiguity not in _AMBIGUITY_LEVELS:
            raise SeedValidationError("term has invalid ambiguity_level")
        contexts = tuple(
            _text(context, "term.requires_any_context", maximum=48)
            for context in _sequence(
                item.get("requires_any_context"), "term.requires_any_context"
            )
        )
        if ambiguity == "high" and not contexts:
            raise SeedValidationError(
                "term.requires_any_context is required for high ambiguity"
            )
        kind = _text(item.get("term_kind"), "term.term_kind")
        if kind not in _ALIAS_KINDS:
            raise SeedValidationError("term has invalid term_kind")
        terms.append(
            SeedTerm(
                term_id=_text(item.get("term_id"), "term.term_id"),
                text=_text(item.get("text"), "term.text", maximum=48),
                meaning_summary=_text(
                    item.get("meaning_summary"),
                    "term.meaning_summary",
                    maximum=500,
                ),
                term_kind=kind,
                ambiguity_level=ambiguity,
                requires_any_context=contexts,
            )
        )
    patterns = tuple(
        _text(item, "discussion_patterns", maximum=80)
        for item in _sequence(
            manifest.get("discussion_patterns"), "discussion_patterns"
        )
    )
    if len(terms) + len(patterns) < 20:
        raise SeedValidationError(
            "terms and discussion_patterns must contain at least 20 entries"
        )

    known_entity_ids = {
        game.entity_id,
        *(entity.entity_id for entity in entities),
        *(term.term_id for term in terms),
    }
    relations = []
    for index, raw in enumerate(
        _sequence(manifest.get("stable_relations"), "stable_relations")
    ):
        item = _mapping(raw, "stable_relations[{}]".format(index))
        relation = StableRelation(
            relation_id=_text(
                item.get("relation_id"), "relation.relation_id"
            ),
            subject_entity_id=_text(
                item.get("subject_entity_id"), "relation.subject_entity_id"
            ),
            predicate=_text(
                item.get("predicate"), "relation.predicate", maximum=80
            ),
            object_entity_id=_text(
                item.get("object_entity_id"), "relation.object_entity_id"
            ),
            safe_summary=_text(
                item.get("safe_summary"),
                "relation.safe_summary",
                maximum=500,
            ),
        )
        if (
            relation.subject_entity_id not in known_entity_ids
            or relation.object_entity_id not in known_entity_ids
        ):
            raise SeedValidationError(
                "stable relation must reference entities in the same seed"
            )
        relations.append(relation)
    if len(relations) < 3:
        raise SeedValidationError(
            "stable_relations must contain at least three entries"
        )
    if len({relation.relation_id for relation in relations}) != len(relations):
        raise SeedValidationError("stable relation IDs must be unique")

    sources = []
    for index, raw in enumerate(
        _sequence(manifest.get("official_sources"), "official_sources")
    ):
        item = _mapping(raw, "official_sources[{}]".format(index))
        url = _text(item.get("url"), "official source url", maximum=500)
        domain = _text(
            item.get("domain"), "official source domain", maximum=253
        ).casefold()
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname != domain:
            raise SeedValidationError(
                "official source must use HTTPS on its declared domain"
            )
        required = item.get("required")
        if not isinstance(required, bool):
            raise SeedValidationError("official source required must be boolean")
        sources.append(
            OfficialSourceSeed(
                source_id=_text(item.get("source_id"), "source.source_id"),
                publisher=_text(
                    item.get("publisher"), "source.publisher", maximum=80
                ),
                domain=domain,
                url=url,
                required=required,
            )
        )
    if not sources:
        raise SeedValidationError("official_sources must not be empty")

    return GameSemanticSeed(
        seed_id=seed_id,
        seed_version=seed_version,
        game=game,
        entity_types=entity_types,
        entities=entities,
        terms=tuple(terms),
        stable_relations=tuple(relations),
        discussion_patterns=patterns,
        official_sources=tuple(sources),
        content_hash=supplied_hash,
        manifest=dict(manifest),
    )


def load_bundled_seeds() -> tuple[GameSemanticSeed, ...]:
    paths = sorted(_ASSET_DIRECTORY.glob("*.json"))
    if not paths:
        raise SeedValidationError("no bundled game semantic seeds found")
    seeds = tuple(load_seed_asset(path) for path in paths)
    identities = [(seed.seed_id, seed.seed_version) for seed in seeds]
    if len(identities) != len(set(identities)):
        raise SeedValidationError("bundled seed identities must be unique")
    return seeds


class SeedImporter:
    def __init__(
        self,
        repository: KnowledgeRepository,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._repository = repository
        self._clock = clock

    def import_all(
        self, seeds: Sequence[GameSemanticSeed]
    ) -> SeedImportReport:
        imported = []
        unchanged = []
        for seed in seeds:
            try:
                outcome = self._repository.import_seed_manifest(
                    seed.manifest, imported_at=int(self._clock())
                )
            except SeedVersionConflict as error:
                raise SeedHashConflict(
                    "seed {} has an immutable-version hash conflict".format(
                        seed.seed_id
                    )
                ) from error
            except SeedVersionOrderConflict as error:
                raise SeedVersionOrderError(
                    "seed {} cannot replace a newer active version".format(
                        seed.seed_id
                    )
                ) from error
            if outcome == "imported":
                imported.append(seed.seed_id)
            else:
                unchanged.append(seed.seed_id)
        return SeedImportReport(tuple(imported), tuple(unchanged))


__all__ = (
    "GameSemanticSeed",
    "OfficialSourceSeed",
    "SeedAlias",
    "SeedEntity",
    "SeedGame",
    "SeedHashConflict",
    "SeedImportReport",
    "SeedImporter",
    "SeedTerm",
    "SeedValidationError",
    "SeedVersionOrderError",
    "StableRelation",
    "load_bundled_seeds",
    "load_seed_asset",
)
