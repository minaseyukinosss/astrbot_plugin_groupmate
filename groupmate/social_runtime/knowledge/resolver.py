"""Deterministic local entity, term, and relative-version resolution."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Mapping

from ..contracts import SocialEventEnvelope
from .contracts import (
    ResolvedEntity,
    ResolvedTerm,
    TopicUnderstandingFrame,
    VersionReference,
)
from .release_state import GameReleaseStateService, ResolvedVersionReference
from .repository import KnowledgeRepository


_VERSION_WORDS = (
    "新版本",
    "下版本",
    "刚更新",
    "前瞻",
    "爆料",
    "测试服",
    "这期",
)
_UNKNOWN_GAME = re.compile(
    r"(?:在玩|想玩|玩过|游戏|手游|端游)"
    r"[\u4e00-\u9fffA-Za-z0-9·:_\-\s]{2,40}"
)
_DATE_RISK = re.compile(
    r"(?:今天|明天|后天|昨天|最近|本周|下周|\d{1,2}[月/-]\d{1,2})"
)
_LIST_RISK = re.compile(r"(?:新角色|新武器|有哪些|名单|卡池|阵容|清单)")
_NUMERIC_RISK = re.compile(r"(?:多少|数值|伤害|倍率|概率|提升|削弱)")
_OFFICIAL_RISK = re.compile(r"(?:官方|公告|公布|官宣|确认了吗|实装)")
_RUMOR_RISK = re.compile(r"(?:爆料|内鬼|舅舅|测试服|网传|传闻)")


def _normalize(value: object) -> str:
    return " ".join(
        unicodedata.normalize("NFKC", str(value or "")).casefold().split()
    )


def _exact_text(value: str) -> str:
    return value.strip(" \t\r\n，。！？,.!?、")


@dataclass(frozen=True)
class _Alias:
    surface: str
    entity_id: str
    entity_type: str
    canonical_name: str
    game_id: str
    ambiguity_level: str
    contexts: tuple[str, ...]
    supporting_id: str
    term_kind: str | None = None
    meaning_summary: str | None = None


class KnowledgeEntityResolver:
    def __init__(
        self,
        repository: KnowledgeRepository,
        *,
        release_state_service: GameReleaseStateService | None = None,
    ) -> None:
        self.repository = repository
        self.release_state_service = (
            release_state_service or GameReleaseStateService(repository)
        )

    def resolve_reference(
        self,
        frame: TopicUnderstandingFrame,
        message_time: int,
        region: str | None,
        platform: str | None,
    ) -> ResolvedVersionReference:
        """Bind lexical version wording only through verified local slots."""
        return self.release_state_service.resolve_reference(
            frame, message_time, region, platform
        )

    def resolve(
        self,
        event: SocialEventEnvelope,
        context_events: Iterable[SocialEventEnvelope],
        group_id: str,
        now: int,
    ) -> TopicUnderstandingFrame:
        decision_now = int(now)
        if decision_now < 0:
            raise ValueError("now must not be negative")
        scope = str(group_id)
        current_text = _normalize(event.payload.get("text"))
        context = tuple(context_events)[-8:]
        context_text = " ".join(
            _normalize(item.payload.get("text")) for item in context
        )
        combined_context = " ".join((current_text, context_text)).strip()
        aliases, active_game_ids = self._seed_aliases()
        ambiguity_codes: list[str] = []
        supporting_ids: list[str] = []

        resolved_entities: list[ResolvedEntity] = []
        current_games = self._resolve_group_games(
            current_text, scope, active_game_ids
        )
        for entity, alias_id, matched_alias, confidence in current_games:
            resolved_entities.append(
                ResolvedEntity.create(
                    entity_id=entity.entity_id,
                    entity_type=entity.entity_type,
                    canonical_name=entity.canonical_name,
                    canonical_game_id=entity.canonical_game_id,
                    matched_alias=matched_alias,
                    confidence=confidence,
                    supporting_knowledge_ids=(alias_id,),
                )
            )
            supporting_ids.append(alias_id)

        global_current_games, game_ambiguity = self._resolve_seed_games(
            aliases, current_text, combined_context, scope, decision_now
        )
        if game_ambiguity:
            ambiguity_codes.append("ambiguous_entity")
        existing_entity_ids = {item.entity_id for item in resolved_entities}
        for item in global_current_games:
            if item.entity_id not in existing_entity_ids:
                resolved_entities.append(item)
                existing_entity_ids.add(item.entity_id)
                supporting_ids.extend(item.supporting_knowledge_ids)

        current_game_ids = tuple(
            dict.fromkeys(
                item.canonical_game_id
                for item in resolved_entities
                if item.entity_type == "game"
            )
        )
        discourse_game_ids: tuple[str, ...] = ()
        if not current_game_ids and context_text:
            discourse_games, _ambiguous = self._resolve_seed_games(
                aliases, context_text, context_text, scope, decision_now
            )
            discourse_game_ids = tuple(
                dict.fromkeys(item.canonical_game_id for item in discourse_games)
            )
        contextual_game_ids = current_game_ids or discourse_game_ids

        non_game_entities, entity_ambiguous = self._resolve_non_game_entities(
            aliases, current_text, contextual_game_ids
        )
        if entity_ambiguous and "ambiguous_entity" not in ambiguity_codes:
            ambiguity_codes.append("ambiguous_entity")
        for item in non_game_entities:
            if item.entity_id not in existing_entity_ids:
                resolved_entities.append(item)
                existing_entity_ids.add(item.entity_id)
                supporting_ids.extend(item.supporting_knowledge_ids)
        contextual_game_ids = tuple(
            dict.fromkeys(
                (
                    *contextual_game_ids,
                    *(item.canonical_game_id for item in non_game_entities),
                )
            )
        )

        resolved_terms, term_ambiguous = self._resolve_terms(
            aliases,
            current_text,
            combined_context,
            contextual_game_ids,
            scope,
        )
        if term_ambiguous:
            ambiguity_codes.append("ambiguous_term")
        supporting_ids.extend(
            knowledge_id
            for term in resolved_terms
            for knowledge_id in term.supporting_knowledge_ids
        )

        all_resolved_game_ids = tuple(
            dict.fromkeys(
                (
                    *(item.canonical_game_id for item in resolved_entities),
                    *(item.game_id for item in resolved_terms),
                )
            )
        )
        if (
            not all_resolved_game_ids
            and discourse_game_ids
            and resolved_terms
        ):
            all_resolved_game_ids = discourse_game_ids
        if len(all_resolved_game_ids) > 4:
            ambiguity_codes.append("too_many_games")
        resolved_game_ids = all_resolved_game_ids[:4]
        allowed_games = set(resolved_game_ids)
        resolved_entities = [
            item
            for item in resolved_entities
            if item.canonical_game_id in allowed_games
        ]
        resolved_terms = [
            item for item in resolved_terms if item.game_id in allowed_games
        ]

        version_signal = any(word in current_text for word in _VERSION_WORDS)
        raw_risk_codes = self._risk_codes(current_text)
        risk_codes = (
            raw_risk_codes if resolved_game_ids or version_signal else ()
        )
        for code in risk_codes:
            if code not in ambiguity_codes:
                ambiguity_codes.append(code)
        version_reference = None
        if version_signal:
            if len(all_resolved_game_ids) == 1:
                relative_kind, disclosure_kind = self._version_kinds(current_text)
                version_reference = VersionReference.create(
                    game_id=all_resolved_game_ids[0],
                    relative_kind=relative_kind,
                    disclosure_kind=disclosure_kind,
                    region=None,
                    platform=None,
                    confidence=0.9,
                )
            else:
                ambiguity_codes.append("ambiguous_game_for_version")
                if bool(event.payload.get("direct_address")):
                    ambiguity_codes.append("direct_unresolved")

        unknown_game = bool(
            not resolved_game_ids and _UNKNOWN_GAME.search(current_text)
        )
        if unknown_game:
            ambiguity_codes.append("unknown_game_entity")

        if version_signal:
            intent = "version_question"
        elif risk_codes:
            intent = "fresh_fact_request"
        elif resolved_game_ids or resolved_terms:
            intent = "stable_game_chat"
        elif unknown_game:
            intent = "knowledge_topic"
        else:
            intent = None

        confidence_values = [
            *(item.confidence for item in resolved_entities),
            *(item.confidence for item in resolved_terms),
        ]
        confidence = (
            sum(confidence_values) / len(confidence_values)
            if confidence_values
            else (0.4 if intent is not None else 0.0)
        )
        frame_id = "knowledge-frame:" + hashlib.sha256(
            event.event_id.encode("utf-8")
        ).hexdigest()[:24]
        return TopicUnderstandingFrame.create(
            frame_id=frame_id,
            game_ids=resolved_game_ids,
            resolved_entities=tuple(resolved_entities[:8]),
            resolved_terms=tuple(resolved_terms[:12]),
            discourse_referents=(),
            version_reference=version_reference,
            conversation_intent_hint=intent,
            ambiguity_codes=tuple(dict.fromkeys(ambiguity_codes)),
            confidence=confidence,
            supporting_knowledge_ids=tuple(dict.fromkeys(supporting_ids))[:16],
        )

    def _seed_aliases(self) -> tuple[tuple[_Alias, ...], set[str]]:
        aliases: list[_Alias] = []
        active_game_ids: set[str] = set()
        for manifest in self.repository.active_seed_manifests():
            seed_id = str(manifest["seed_id"])
            game = manifest["game"]
            game_id = str(game["entity_id"])
            active_game_ids.add(game_id)
            game_aliases = [
                *game["aliases"],
                {
                    "text": game["canonical_name"],
                    "ambiguity_level": "none",
                    "requires_any_context": [],
                },
                {
                    "text": game["english_name"],
                    "ambiguity_level": "contextual",
                    "requires_any_context": [],
                },
            ]
            for raw in game_aliases:
                aliases.append(
                    _Alias(
                        surface=_normalize(raw["text"]),
                        entity_id=game_id,
                        entity_type="game",
                        canonical_name=str(game["canonical_name"]),
                        game_id=game_id,
                        ambiguity_level=str(raw["ambiguity_level"]),
                        contexts=tuple(
                            _normalize(value)
                            for value in raw.get("requires_any_context", ())
                        ),
                        supporting_id=seed_id,
                    )
                )
            for entity in manifest["entities"]:
                aliases.append(
                    _Alias(
                        surface=_normalize(entity["canonical_name"]),
                        entity_id=str(entity["entity_id"]),
                        entity_type=str(entity["entity_type"]),
                        canonical_name=str(entity["canonical_name"]),
                        game_id=game_id,
                        ambiguity_level="contextual",
                        contexts=(),
                        supporting_id=seed_id,
                    )
                )
            for term in manifest["terms"]:
                aliases.append(
                    _Alias(
                        surface=_normalize(term["text"]),
                        entity_id=str(term["term_id"]),
                        entity_type="term",
                        canonical_name=str(term["text"]),
                        game_id=game_id,
                        ambiguity_level=str(term["ambiguity_level"]),
                        contexts=tuple(
                            _normalize(value)
                            for value in term.get("requires_any_context", ())
                        ),
                        supporting_id=seed_id,
                        term_kind=str(term["term_kind"]),
                        meaning_summary=str(term["meaning_summary"]),
                    )
                )
        learned_aliases = self.repository.active_global_game_aliases()
        learned_entities = {
            item.entity_id: item
            for item in self.repository.entities(
                alias.entity_id for alias in learned_aliases
            )
        }
        for alias in learned_aliases:
            entity = learned_entities.get(alias.entity_id)
            if entity is None or entity.status != "active":
                continue
            active_game_ids.add(entity.entity_id)
            aliases.append(
                _Alias(
                    surface=_normalize(alias.normalized_alias),
                    entity_id=entity.entity_id,
                    entity_type="game",
                    canonical_name=entity.canonical_name,
                    game_id=entity.entity_id,
                    ambiguity_level=alias.ambiguity_level,
                    contexts=(),
                    supporting_id=alias.alias_id,
                )
            )
        deduplicated = {
            (
                item.surface,
                item.entity_id,
                item.entity_type,
            ): item
            for item in aliases
        }
        return (
            tuple(
                sorted(
                    deduplicated.values(),
                    key=lambda item: (-len(item.surface), item.entity_id),
                )
            ),
            active_game_ids,
        )

    def _resolve_group_games(self, text: str, group_id: str, active_game_ids: set[str]):
        matches = []
        aliases = self.repository.active_group_aliases(group_id)
        entity_records = {
            item.entity_id: item
            for item in self.repository.entities(item.entity_id for item in aliases)
        }
        for alias in aliases:
            entity = entity_records.get(alias.entity_id)
            if (
                entity is None
                or entity.status != "active"
                or entity.canonical_game_id not in active_game_ids
                or alias.normalized_alias not in text
            ):
                continue
            matches.append(
                (
                    entity,
                    alias.alias_id,
                    alias.normalized_alias,
                    max(0.75, alias.confidence),
                )
            )
        return tuple(matches)

    def _resolve_seed_games(
        self,
        aliases: tuple[_Alias, ...],
        text: str,
        context_text: str,
        group_id: str,
        now: int,
    ) -> tuple[tuple[ResolvedEntity, ...], bool]:
        candidates: dict[str, tuple[_Alias, float]] = {}
        ambiguous = False
        for alias in aliases:
            if alias.entity_type != "game" or alias.surface not in text:
                continue
            context_hit = any(value in context_text for value in alias.contexts)
            exact = _exact_text(text) == alias.surface
            if alias.ambiguity_level == "high" and not context_hit:
                ambiguous = True
                continue
            score = 0.98 if alias.ambiguity_level == "none" else 0.86
            if exact:
                score += 0.04
            if context_hit:
                score += 0.04
            affinity = self.repository.topic_affinity(
                group_id, alias.game_id, now=now
            )
            if affinity is not None and context_hit:
                score += min(0.02, affinity.salience / 1000)
            prior = candidates.get(alias.entity_id)
            if prior is None or (score, len(alias.surface)) > (
                prior[1],
                len(prior[0].surface),
            ):
                candidates[alias.entity_id] = (alias, min(1.0, score))
        result = tuple(
            ResolvedEntity.create(
                entity_id=alias.entity_id,
                entity_type="game",
                canonical_name=alias.canonical_name,
                canonical_game_id=alias.game_id,
                matched_alias=alias.surface,
                confidence=score,
                supporting_knowledge_ids=(alias.supporting_id,),
            )
            for alias, score in sorted(
                candidates.values(), key=lambda item: (-item[1], item[0].entity_id)
            )
            if score >= 0.75
        )
        return result, ambiguous

    @staticmethod
    def _resolve_non_game_entities(
        aliases: tuple[_Alias, ...],
        text: str,
        contextual_game_ids: tuple[str, ...],
    ) -> tuple[tuple[ResolvedEntity, ...], bool]:
        by_surface: dict[str, list[_Alias]] = {}
        for alias in aliases:
            if alias.entity_type not in {"game", "term"} and alias.surface in text:
                by_surface.setdefault(alias.surface, []).append(alias)
        resolved = []
        ambiguous = False
        for surface, values in sorted(
            by_surface.items(), key=lambda item: (-len(item[0]), item[0])
        ):
            allowed = [
                item
                for item in values
                if item.game_id in contextual_game_ids
            ]
            if not allowed:
                unique_games = {item.game_id for item in values}
                if len(surface) >= 3 and len(unique_games) == 1:
                    allowed = values
            unique_entities = {item.entity_id for item in allowed}
            if len(unique_entities) != 1:
                ambiguous = True
                continue
            alias = allowed[0]
            resolved.append(
                ResolvedEntity.create(
                    entity_id=alias.entity_id,
                    entity_type=alias.entity_type,
                    canonical_name=alias.canonical_name,
                    canonical_game_id=alias.game_id,
                    matched_alias=alias.surface,
                    confidence=(0.9 if contextual_game_ids else 0.8),
                    supporting_knowledge_ids=(alias.supporting_id,),
                )
            )
        return tuple(resolved), ambiguous

    def _resolve_terms(
        self,
        aliases: tuple[_Alias, ...],
        text: str,
        context_text: str,
        contextual_game_ids: tuple[str, ...],
        group_id: str,
    ) -> tuple[tuple[ResolvedTerm, ...], bool]:
        del group_id
        by_surface: dict[str, list[_Alias]] = {}
        for alias in aliases:
            if alias.entity_type == "term" and alias.surface in text:
                by_surface.setdefault(alias.surface, []).append(alias)
        resolved: list[ResolvedTerm] = []
        ambiguous = False
        for surface, values in sorted(
            by_surface.items(), key=lambda item: (-len(item[0]), item[0])
        ):
            allowed = []
            for alias in values:
                context_hit = any(
                    value in context_text for value in alias.contexts
                )
                game_hit = alias.game_id in contextual_game_ids
                if alias.ambiguity_level in {"contextual", "high"} and not (
                    context_hit or game_hit
                ):
                    continue
                if contextual_game_ids and not game_hit and len(values) > 1:
                    continue
                allowed.append(alias)
            unique_games = {item.game_id for item in allowed}
            if len(unique_games) != 1:
                if len(values) > 1 or values[0].ambiguity_level != "none":
                    ambiguous = True
                continue
            for alias in allowed:
                score = 0.94 if alias.ambiguity_level == "none" else 0.86
                resolved.append(
                    ResolvedTerm.create(
                        term_id=alias.entity_id,
                        canonical_text=alias.canonical_name,
                        meaning_summary=alias.meaning_summary,
                        game_id=alias.game_id,
                        term_kind=alias.term_kind,
                        confidence=score,
                        supporting_knowledge_ids=(alias.supporting_id,),
                    )
                )
        deduplicated = {item.term_id: item for item in resolved}
        return tuple(deduplicated.values()), ambiguous

    @staticmethod
    def _risk_codes(text: str) -> tuple[str, ...]:
        values = []
        if any(word in text for word in _VERSION_WORDS) or "版本" in text:
            values.append("risk:version_state")
        if _DATE_RISK.search(text):
            values.append("risk:date_time")
        if _LIST_RISK.search(text):
            values.append("risk:entity_list")
        if _NUMERIC_RISK.search(text):
            values.append("risk:numeric")
        if _OFFICIAL_RISK.search(text):
            values.append("risk:official_status")
        if _RUMOR_RISK.search(text):
            values.append("risk:rumor_status")
        return tuple(values)

    @staticmethod
    def _version_kinds(text: str) -> tuple[str, str]:
        if "下版本" in text:
            relative = "next"
        elif "新版本" in text:
            relative = "new"
        elif "刚更新" in text:
            relative = "recent_update"
        elif "这期" in text:
            relative = "current"
        elif "前瞻" in text:
            relative = "next"
        else:
            relative = "unspecified"
        if "测试服" in text:
            disclosure = "test_server"
        elif "爆料" in text:
            disclosure = "rumor"
        elif "前瞻" in text:
            disclosure = "preview"
        elif "刚更新" in text:
            disclosure = "release"
        else:
            disclosure = "none"
        return relative, disclosure


__all__ = ("KnowledgeEntityResolver",)
