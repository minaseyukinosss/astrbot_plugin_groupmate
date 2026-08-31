"""Group-scoped long-tail game discovery and stable semantic admission."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Callable

from .admission import KnowledgeEvidenceAdmission
from .contracts import KnowledgeClaimCandidate, SourceEvidence
from .repository import (
    KnowledgeJobRecord,
    KnowledgeRepository,
    StoredKnowledgeObservation,
    TopicAffinity,
)
from .search import DiscoverySearchPort, SearchRequest


LEARNING_WINDOW_SECONDS = 7 * 24 * 60 * 60
_UNKNOWN_GAME_MENTION = re.compile(
    r"(?:在玩|想玩|玩过|玩一下|游戏叫|手游叫|端游叫)\s*"
    r"[《〈\"“']?(?P<name>[\u4e00-\u9fffA-Za-z0-9·・:_+()（）\- ]{2,48})"
)
_SAFE_HINT = re.compile(r"[\w\u3400-\u9fff·・.：:+()（）《》\- ]{2,48}\Z")
_GENRES = (
    "动作角色扮演",
    "第一人称射击",
    "战术射击",
    "模拟经营",
    "开放世界",
    "角色扮演",
    "射击",
    "策略",
    "卡牌",
    "生存",
    "MOBA",
)
_GENERIC_HINTS = {"这个游戏", "那个游戏", "新游戏", "手游", "端游"}


def extract_unknown_game_hint(text: object) -> str | None:
    normalized = " ".join(
        unicodedata.normalize("NFKC", str(text or "")).split()
    )
    match = _UNKNOWN_GAME_MENTION.search(normalized)
    if match is None:
        return None
    hint = match.group("name").strip(" 《〈\"“'》〉”。，,！？!?；;:")
    if hint in _GENERIC_HINTS or not _SAFE_HINT.fullmatch(hint):
        return None
    return hint


def candidate_game_id(entity_hint: object) -> str:
    hint = " ".join(
        unicodedata.normalize("NFKC", str(entity_hint or "")).casefold().split()
    )
    if not _SAFE_HINT.fullmatch(hint):
        raise ValueError("entity_hint is unsafe")
    return "game:learned:" + hashlib.sha256(hint.encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True)
class LearningDecision:
    should_enqueue: bool
    diagnostic_code: str


class KnowledgeLearningPolicy:
    """Turn scoped popularity into background work, never into a fact."""

    def evaluate(
        self,
        observation: StoredKnowledgeObservation,
        affinity: TopicAffinity | None,
        *,
        now: int,
        admin_confirmed: bool = False,
    ) -> LearningDecision:
        if not isinstance(observation, StoredKnowledgeObservation):
            raise TypeError("observation is invalid")
        timestamp = int(now)
        if timestamp < 0:
            raise ValueError("now must not be negative")
        if admin_confirmed:
            return LearningDecision(True, "admin_confirmed_warmup")
        if (
            affinity is None
            or affinity.last_seen_at < timestamp - LEARNING_WINDOW_SECONDS
        ):
            return LearningDecision(False, "learning_topic_not_recent")
        if affinity.qualified_mention_count < 8:
            return LearningDecision(False, "learning_mentions_insufficient")
        if affinity.distinct_actor_count < 3:
            return LearningDecision(False, "learning_actors_insufficient")
        if affinity.distinct_scene_count < 4:
            return LearningDecision(False, "learning_scenes_insufficient")
        return LearningDecision(True, "learning_threshold_reached")


@dataclass(frozen=True)
class LearningOutcome:
    status: str
    diagnostic_code: str | None


class GameKnowledgeLearningWorker:
    """Admit only an identity and a corroborated version-independent genre."""

    def __init__(
        self,
        repository: KnowledgeRepository,
        *,
        discovery_search: DiscoverySearchPort,
        clock: Callable[[], float],
    ) -> None:
        self._repository = repository
        self._discovery = discovery_search
        self._clock = clock
        self._evidence = KnowledgeEvidenceAdmission()

    async def learn(self, job: KnowledgeJobRecord) -> LearningOutcome:
        if job.job_kind != "unknown_entity_learning" or job.entity_id is None:
            raise ValueError("learning job is invalid")
        hint = str(job.request.get("entity_hint") or "")
        if candidate_game_id(hint) != job.entity_id:
            raise ValueError("learning job entity does not match its safe hint")
        entity = self._repository.entities((job.entity_id,))
        stable = self._repository.active_claims((job.entity_id,), limit=8)
        if (
            entity
            and entity[0].status == "active"
            and any(
                item.predicate == "has_stable_genre"
                and item.claim_kind == "stable_semantic"
                for item in stable
            )
            and any(
                entity_id == job.entity_id
                for entity_id, _alias in self._repository.game_alias_matches(hint)
            )
        ):
            return LearningOutcome("complete", None)
        now = int(self._clock())
        request = SearchRequest.create(
            request_id=job.job_id,
            game_entity_id=job.entity_id,
            game_name="未知游戏",
            entity_id=None,
            entity_name=None,
            query_intents=("unknown_entity_learning",),
            region=None,
            platform=None,
            max_results=4,
            deadline=now + 30,
            entity_hint=hint,
            now=now,
        )
        result = await self._discovery.search(request)
        if result.status != "complete":
            return LearningOutcome(
                "retry", result.diagnostic_code or "learning_search_incomplete"
            )
        evidence, genre = self._corroborated_evidence(hint, result.candidates)
        if genre is None:
            return LearningOutcome("retry", "learning_evidence_insufficient")

        admitted = self._evidence.commit(
            self._repository,
            game_entity_id=job.entity_id,
            evidence=evidence,
        )
        summary = f"{hint}是一款{genre}游戏。"
        claim_digest = hashlib.sha256(
            f"{job.entity_id}\0stable_genre\0{genre}".encode("utf-8")
        ).hexdigest()[:24]
        candidate = KnowledgeClaimCandidate.create(
            candidate_id=f"claim:learned:{claim_digest}",
            subject_entity_id=job.entity_id,
            predicate="has_stable_genre",
            safe_summary=summary,
            claim_kind="stable_semantic",
            evidence_level="corroborated",
            applies_to_version_slot_id=None,
            region=None,
            platform=None,
            valid_from=None,
            valid_until=None,
            checked_at=now,
        )
        self._repository.admit_claim(candidate, admitted.source_ids)
        alias_digest = hashlib.sha256(
            f"{job.entity_id}\0{hint.casefold()}".encode("utf-8")
        ).hexdigest()[:24]
        self._repository.put_alias(
            alias_id=f"alias:learned:{alias_digest}",
            entity_id=job.entity_id,
            normalized_alias=hint,
            alias_kind="community",
            ambiguity_level="none",
            source_id=admitted.source_ids[0],
            status="active",
        )
        # Activation is last: interrupted work cannot make an ungrounded game
        # visible to the resolver.
        self._repository.upsert_entity(
            entity_id=job.entity_id,
            entity_type="game",
            canonical_name=hint,
            canonical_game_id=job.entity_id,
            status="active",
            now=now,
        )
        return LearningOutcome("complete", None)

    @staticmethod
    def _corroborated_evidence(
        hint: str, values: tuple[SourceEvidence, ...]
    ) -> tuple[tuple[SourceEvidence, ...], str | None]:
        normalized_hint = "".join(
            unicodedata.normalize("NFKC", hint).casefold().split()
        )
        eligible: list[SourceEvidence] = []
        domains: set[str] = set()
        genre_domains: dict[str, set[str]] = {genre: set() for genre in _GENRES}
        for item in values:
            if item.source_class.value not in {"official", "secondary"}:
                continue
            text = "".join(
                unicodedata.normalize(
                    "NFKC", f"{item.title} {item.evidence_excerpt}"
                ).casefold().split()
            )
            if normalized_hint not in text or item.domain in domains:
                continue
            eligible.append(item)
            domains.add(item.domain)
            for genre in _GENRES:
                if genre.casefold() in text:
                    genre_domains[genre].add(item.domain)
                    break
        genre = next(
            (name for name in _GENRES if len(genre_domains[name]) >= 2),
            None,
        )
        if len(domains) < 2 or genre is None:
            return (), None
        return tuple(eligible), genre


__all__ = (
    "GameKnowledgeLearningWorker",
    "KnowledgeLearningPolicy",
    "LEARNING_WINDOW_SECONDS",
    "LearningDecision",
    "LearningOutcome",
    "candidate_game_id",
    "extract_unknown_game_hint",
)
