"""Strict background extraction of profile candidates."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Mapping

from ...profile_vocabulary import (
    EDGE_DIRECTIONS,
    EPISODE_TYPES,
    MODEL_FACT_SOURCE_KINDS,
    normalize_profile_claim,
)
from .contracts import (
    ProfileEpisode,
    ProfileFact,
    ProfileFactCandidate,
    ProfileKnownCognition,
    ProfileObservation,
    SocialEdge,
    SocialEdgeCandidate,
)
from .policy import FACT_CATEGORIES, RELATION_TYPES, ProfileEvidencePolicy


class _CandidateInvalid(ValueError):
    """Expected model-contract rejection carrying a privacy-safe code."""

    def __init__(self, code: str) -> None:
        self.code = str(code)
        super().__init__(self.code)


@dataclass(frozen=True)
class ProfileExtractionResult:
    facts: tuple[ProfileFact, ...] = ()
    episodes: tuple[ProfileEpisode, ...] = ()
    edges: tuple[SocialEdge, ...] = ()
    stale_fact_ids: tuple[str, ...] = ()
    diagnostic_code: str | None = None
    diagnostic_codes: tuple[str, ...] = ()
    provider_latency_ms: int = 0
    request_bytes: int = 0
    backend: str = ""
    model: str = ""


def referenced_actor_ids(payload: Mapping[str, object] | object) -> tuple[str, ...]:
    """Return @ and reply targets copied from an observation payload."""

    if not isinstance(payload, Mapping):
        return ()
    values: list[str] = []
    mentions = payload.get("mentions")
    if isinstance(mentions, (list, tuple)):
        for item in mentions:
            if isinstance(item, Mapping):
                value = str(
                    item.get("actor_id")
                    or item.get("target_id")
                    or item.get("id")
                    or item.get("qq")
                    or ""
                ).strip()
            else:
                value = str(item or "").strip()
            if value:
                values.append(value)
    reply_to = str(payload.get("reply_to_actor_id") or "").strip()
    if reply_to:
        values.append(reply_to)
    return tuple(dict.fromkeys(values))


class ProfileExtractor:
    def __init__(self, client, policy: ProfileEvidencePolicy) -> None:
        self.client = client
        self.policy = policy

    async def extract(
        self,
        observations: tuple[ProfileObservation, ...],
        *,
        known: ProfileKnownCognition | None = None,
    ) -> ProfileExtractionResult:
        if not observations:
            return ProfileExtractionResult()
        cognition = known or ProfileKnownCognition()
        batch = self._batch(observations, known=cognition)
        response = await self.client.extract(batch)
        payload = response.payload
        if not self._top_level_valid(payload):
            return self._invalid(response)
        allowed_events = {item.event_id for item in observations}
        allowed_subjects = {item.actor_id for item in observations}
        for item in observations:
            allowed_subjects.update(referenced_actor_ids(item.payload))
        known_facts = {item.fact_id: item for item in cognition.facts}
        known_episodes = {item.episode_id: item for item in cognition.episodes}
        facts: list[ProfileFact] = []
        stale_fact_ids: list[str] = []
        episodes: list[ProfileEpisode] = []
        edges: list[SocialEdge] = []
        diagnostics: list[str] = []
        for raw in payload["facts"]:
            try:
                action, fact = self._fact(
                    raw,
                    observations=observations,
                    allowed_events=allowed_events,
                    allowed_subjects=allowed_subjects,
                    known_facts=known_facts,
                )
                if action == "stale":
                    stale_fact_ids.append(fact.fact_id)
                    continue
                facts.append(fact)
            except _CandidateInvalid as exc:
                diagnostics.append(exc.code)
            except (TypeError, ValueError):
                diagnostics.append("profile_fact_shape_invalid")
        for raw in payload["episodes"]:
            try:
                episodes.append(
                    self._episode(
                        raw,
                        observations=observations,
                        allowed_events=allowed_events,
                        allowed_subjects=allowed_subjects,
                        known_episodes=known_episodes,
                    )
                )
            except _CandidateInvalid as exc:
                diagnostics.append(exc.code)
            except (TypeError, ValueError):
                diagnostics.append("profile_episode_shape_invalid")
        for raw in payload["edges"]:
            try:
                edge = self._edge(
                    raw,
                    observations=observations,
                    allowed_events=allowed_events,
                    allowed_subjects=allowed_subjects,
                )
                if edge.status == "rejected":
                    raise _CandidateInvalid("profile_edge_policy_rejected")
                edges.append(edge)
            except _CandidateInvalid as exc:
                diagnostics.append(exc.code)
            except (TypeError, ValueError):
                diagnostics.append("profile_edge_shape_invalid")
        diagnostic_codes = tuple(dict.fromkeys(diagnostics))
        return ProfileExtractionResult(
            facts=tuple(facts),
            episodes=tuple(episodes),
            edges=tuple(edges),
            stale_fact_ids=tuple(dict.fromkeys(stale_fact_ids)),
            diagnostic_code=(diagnostic_codes[0] if diagnostic_codes else None),
            diagnostic_codes=diagnostic_codes,
            provider_latency_ms=int(response.latency_ms),
            request_bytes=int(response.request_bytes),
            backend=str(response.backend),
            model=str(response.model),
        )

    def _fact(
        self,
        raw: object,
        *,
        observations: tuple[ProfileObservation, ...],
        allowed_events: set[str],
        allowed_subjects: set[str],
        known_facts: Mapping[str, ProfileFact],
    ) -> tuple[str, ProfileFact]:
        if not isinstance(raw, Mapping):
            raise _CandidateInvalid("profile_fact_shape_invalid")
        category = str(raw.get("category") or "")
        source_kind = str(raw.get("source_kind") or "")
        subject_id = str(raw.get("subject_id") or "")
        source_actor_id = str(raw.get("source_actor_id") or "")
        evidence_ids = tuple(
            str(value) for value in raw.get("evidence_event_ids", ())
        )
        if category not in FACT_CATEGORIES:
            raise _CandidateInvalid("profile_fact_category_invalid")
        if source_kind not in MODEL_FACT_SOURCE_KINDS:
            raise _CandidateInvalid("profile_fact_source_kind_invalid")
        if subject_id not in allowed_subjects:
            raise _CandidateInvalid("profile_fact_subject_invalid")
        if source_actor_id not in allowed_subjects:
            raise _CandidateInvalid("profile_fact_source_actor_invalid")
        if not evidence_ids or not set(evidence_ids) <= allowed_events:
            raise _CandidateInvalid("profile_fact_evidence_invalid")
        existing = known_facts.get(str(raw.get("existing_id") or "").strip())
        action = str(raw.get("action") or ("reinforce" if existing is not None else "new"))
        if action not in {"new", "reinforce", "revise", "stale"}:
            action = "new"
        if existing is not None and (
            existing.subject_id != subject_id
            or existing.source_kind != source_kind
            or existing.category != category
        ):
            existing = None
            if action != "new":
                action = "new"
        candidate_id = (
            existing.fact_id
            if existing is not None and action in {"reinforce", "revise", "stale"}
            else self._candidate_id(raw, observations[0])
        )
        candidate = ProfileFactCandidate(
            candidate_id=candidate_id,
            persona_id=observations[0].persona_id,
            group_id=observations[0].group_id,
            subject_id=subject_id,
            category=category,
            summary=(
                existing.summary
                if existing is not None and action == "reinforce"
                else str(raw.get("summary") or "")
            ),
            source_kind=source_kind,
            source_actor_id=source_actor_id,
            source_event_ids=evidence_ids,
            confidence=float(raw.get("confidence")),
            evidence_count=len(set(evidence_ids)),
            observed_at=max(
                item.occurred_at
                for item in observations
                if item.event_id in set(evidence_ids)
            ),
        )
        fact = self.policy.decide(candidate, allowed_event_ids=allowed_events)
        if action == "stale":
            if existing is None:
                raise _CandidateInvalid("profile_fact_existing_id_invalid")
            return action, existing
        if fact.status == "rejected":
            raise _CandidateInvalid("profile_fact_policy_rejected")
        return action, fact

    def _episode(
        self,
        raw: object,
        *,
        observations: tuple[ProfileObservation, ...],
        allowed_events: set[str],
        allowed_subjects: set[str],
        known_episodes: Mapping[str, ProfileEpisode],
    ) -> ProfileEpisode:
        if not isinstance(raw, Mapping):
            raise _CandidateInvalid("profile_episode_shape_invalid")
        participants = tuple(
            dict.fromkeys(str(value) for value in raw.get("participants", ()))
        )
        evidence_ids = tuple(
            dict.fromkeys(
                str(value) for value in raw.get("evidence_event_ids", ())
            )
        )
        episode_type = str(raw.get("episode_type") or "")
        title = " ".join(str(raw.get("title") or "").split())
        summary = " ".join(str(raw.get("summary") or "").split())
        confidence = self._unit(raw.get("confidence"))
        importance = self._unit(raw.get("importance"))
        valence = float(raw.get("valence"))
        if episode_type not in EPISODE_TYPES:
            raise _CandidateInvalid("profile_episode_type_invalid")
        if not participants or not set(participants) <= allowed_subjects:
            raise _CandidateInvalid("profile_episode_participants_invalid")
        if not evidence_ids or not set(evidence_ids) <= allowed_events:
            raise _CandidateInvalid("profile_episode_evidence_invalid")
        if (
            not title
            or len(title) > 80
            or not summary
            or len(summary) > 240
            or not -1.0 <= valence <= 1.0
        ):
            raise _CandidateInvalid("profile_episode_shape_invalid")
        occurred_at = min(
            item.occurred_at
            for item in observations
            if item.event_id in set(evidence_ids)
        )
        reinforced_at = max(
            item.occurred_at
            for item in observations
            if item.event_id in set(evidence_ids)
        )
        existing = known_episodes.get(str(raw.get("existing_id") or "").strip())
        if existing is not None and set(existing.participants) != set(participants):
            existing = None
        episode_id = (
            existing.episode_id
            if existing is not None
            else self._stable_id(
                "profile-episode",
                observations[0],
                {
                    "title": normalize_profile_claim(title),
                    "participants": tuple(sorted(participants)),
                    "episode_type": episode_type,
                },
            )
        )
        merged_evidence = tuple(
            dict.fromkeys(
                (
                    *(existing.source_event_ids if existing is not None else ()),
                    *evidence_ids,
                )
            )
        )
        confidence = max(existing.confidence, confidence) if existing is not None else confidence
        return ProfileEpisode(
            episode_id=episode_id,
            persona_id=observations[0].persona_id,
            group_id=observations[0].group_id,
            title=existing.title if existing is not None else title,
            summary=existing.summary if existing is not None else summary,
            participants=participants,
            source_event_ids=merged_evidence if existing is not None else evidence_ids,
            episode_type=existing.episode_type if existing is not None else episode_type,
            valence=valence,
            importance=importance,
            confidence=confidence,
            status=(
                "confirmed"
                if len(set(merged_evidence if existing is not None else evidence_ids)) >= 2
                and confidence >= 0.88
                else "proposed"
            ),
            occurred_at=(
                min(existing.occurred_at, occurred_at)
                if existing is not None
                else occurred_at
            ),
            last_reinforced_at=(
                max(existing.last_reinforced_at, reinforced_at)
                if existing is not None
                else reinforced_at
            ),
        )

    def _edge(
        self,
        raw: object,
        *,
        observations: tuple[ProfileObservation, ...],
        allowed_events: set[str],
        allowed_subjects: set[str],
    ) -> SocialEdge:
        if not isinstance(raw, Mapping):
            raise _CandidateInvalid("profile_edge_shape_invalid")
        source_id = str(raw.get("source_member_id") or "")
        target_id = str(raw.get("target_member_id") or "")
        direction = str(raw.get("direction") or "")
        evidence_ids = tuple(
            dict.fromkeys(
                str(value) for value in raw.get("evidence_event_ids", ())
            )
        )
        relation_type = str(raw.get("relation_type") or "")
        if relation_type not in RELATION_TYPES:
            raise _CandidateInvalid("profile_edge_relation_type_invalid")
        if direction not in EDGE_DIRECTIONS:
            raise _CandidateInvalid("profile_edge_direction_invalid")
        if (
            source_id not in allowed_subjects
            or target_id not in allowed_subjects
            or source_id == target_id
        ):
            raise _CandidateInvalid("profile_edge_members_invalid")
        if not evidence_ids or not set(evidence_ids) <= allowed_events:
            raise _CandidateInvalid("profile_edge_evidence_invalid")
        candidate = SocialEdgeCandidate(
            candidate_id=self._stable_id(
                "social-edge",
                observations[0],
                {
                    "source": source_id,
                    "target": target_id,
                    "relation_type": raw.get("relation_type"),
                    "direction": direction,
                },
            ),
            persona_id=observations[0].persona_id,
            group_id=observations[0].group_id,
            source_member_id=source_id,
            target_member_id=target_id,
            relation_type=relation_type,
            direction=direction,
            strength=self._unit(raw.get("strength")),
            confidence=self._unit(raw.get("confidence")),
            source_event_ids=evidence_ids,
            observed_at=max(
                item.occurred_at
                for item in observations
                if item.event_id in set(evidence_ids)
            ),
        )
        return self.policy.decide_edge(
            candidate, allowed_event_ids=allowed_events
        )

    @staticmethod
    def _unit(value: object) -> float:
        normalized = float(value)
        if not 0.0 <= normalized <= 1.0:
            raise ValueError
        return normalized

    @staticmethod
    def _top_level_valid(payload: Mapping[str, object]) -> bool:
        return all(
            isinstance(payload.get(name), list)
            for name in ("facts", "episodes", "edges")
        )

    @staticmethod
    def _batch(
        observations: tuple[ProfileObservation, ...],
        *,
        known: ProfileKnownCognition,
    ) -> dict[str, object]:
        return {
            "events": [
                {
                    "event_id": item.event_id,
                    "actor_id": item.actor_id,
                    "actor_name": str(
                        (
                            item.payload.get("sender")
                            if isinstance(item.payload.get("sender"), Mapping)
                            else {}
                        ).get("name")
                        or ""
                    )[:48],
                    "text": str(item.payload.get("text") or "")[:400],
                    "mentions": list(item.payload.get("mentions") or ())[:8],
                    "reply_to_actor_id": item.payload.get(
                        "reply_to_actor_id"
                    ),
                }
                for item in observations[-20:]
            ],
            "known_facts": [
                {
                    "fact_id": item.fact_id,
                    "subject_id": item.subject_id,
                    "category": item.category,
                    "summary": item.summary,
                    "source_kind": item.source_kind,
                    "status": item.status,
                    "evidence_count": item.evidence_count,
                }
                for item in known.facts[:20]
            ],
            "known_episodes": [
                {
                    "episode_id": item.episode_id,
                    "title": item.title,
                    "participants": list(item.participants),
                    "episode_type": item.episode_type,
                    "status": item.status,
                }
                for item in known.episodes[:8]
            ],
            "known_edges": [
                {
                    "edge_id": item.edge_id,
                    "source_member_id": item.source_member_id,
                    "target_member_id": item.target_member_id,
                    "relation_type": item.relation_type,
                    "direction": item.direction,
                    "status": item.status,
                }
                for item in known.edges[:8]
            ],
        }

    @staticmethod
    def _candidate_id(
        raw: Mapping[str, object], observation: ProfileObservation
    ) -> str:
        stable = {
            "persona_id": observation.persona_id,
            "group_id": observation.group_id,
            "subject_id": raw.get("subject_id"),
            "category": raw.get("category"),
            "source_kind": raw.get("source_kind"),
            "summary": normalize_profile_claim(raw.get("summary")),
        }
        digest = hashlib.sha256(
            json.dumps(
                stable,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        return f"profile-fact:{digest}"

    @staticmethod
    def _stable_id(
        prefix: str,
        observation: ProfileObservation,
        payload: Mapping[str, object],
    ) -> str:
        stable = {
            "persona_id": observation.persona_id,
            "group_id": observation.group_id,
            **dict(payload),
        }
        digest = hashlib.sha256(
            json.dumps(
                stable,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        return f"{prefix}:{digest}"

    @staticmethod
    def _invalid(response) -> ProfileExtractionResult:
        return ProfileExtractionResult(
            diagnostic_code="profile_output_invalid",
            diagnostic_codes=("profile_output_invalid",),
            provider_latency_ms=int(response.latency_ms),
            request_bytes=int(response.request_bytes),
            backend=str(response.backend),
            model=str(response.model),
        )


__all__ = (
    "ProfileExtractionResult",
    "ProfileExtractor",
    "referenced_actor_ids",
)
