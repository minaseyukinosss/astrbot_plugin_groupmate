"""Strict background extraction of profile candidates."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Mapping

from .contracts import (
    ProfileEpisode,
    ProfileFact,
    ProfileFactCandidate,
    ProfileObservation,
    SocialEdge,
    SocialEdgeCandidate,
)
from .policy import FACT_CATEGORIES, ProfileEvidencePolicy


_EPISODE_TYPES = frozenset(
    {
        "shared_achievement",
        "conflict",
        "support",
        "running_joke",
        "milestone",
        "notable_interaction",
        "custom",
    }
)
_EDGE_DIRECTIONS = frozenset({"directed", "bidirectional"})


@dataclass(frozen=True)
class ProfileExtractionResult:
    facts: tuple[ProfileFact, ...] = ()
    episodes: tuple[ProfileEpisode, ...] = ()
    edges: tuple[SocialEdge, ...] = ()
    diagnostic_code: str | None = None
    provider_latency_ms: int = 0
    request_bytes: int = 0
    backend: str = ""
    model: str = ""


class ProfileExtractor:
    def __init__(self, client, policy: ProfileEvidencePolicy) -> None:
        self.client = client
        self.policy = policy

    async def extract(
        self, observations: tuple[ProfileObservation, ...]
    ) -> ProfileExtractionResult:
        if not observations:
            return ProfileExtractionResult()
        batch = self._batch(observations)
        response = await self.client.extract(batch)
        payload = response.payload
        if not self._top_level_valid(payload):
            return self._invalid(response)
        allowed_events = {item.event_id for item in observations}
        allowed_subjects = {item.actor_id for item in observations}
        facts: list[ProfileFact] = []
        episodes: list[ProfileEpisode] = []
        edges: list[SocialEdge] = []
        invalid = False
        for raw in payload["facts"]:
            try:
                if not isinstance(raw, Mapping):
                    raise ValueError
                category = str(raw.get("category") or "")
                subject_id = str(raw.get("subject_id") or "")
                source_actor_id = str(raw.get("source_actor_id") or "")
                evidence_ids = tuple(
                    str(value)
                    for value in raw.get("evidence_event_ids", ())
                )
                if (
                    category not in FACT_CATEGORIES
                    or subject_id not in allowed_subjects
                    or source_actor_id not in allowed_subjects
                    or not evidence_ids
                    or not set(evidence_ids) <= allowed_events
                ):
                    raise ValueError
                candidate = ProfileFactCandidate(
                    candidate_id=self._candidate_id(raw, observations[0]),
                    persona_id=observations[0].persona_id,
                    group_id=observations[0].group_id,
                    subject_id=subject_id,
                    category=category,
                    summary=str(raw.get("summary") or ""),
                    source_kind=str(raw.get("source_kind") or ""),
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
                fact = self.policy.decide(
                    candidate, allowed_event_ids=allowed_events
                )
                if fact.status == "rejected":
                    raise ValueError
                facts.append(fact)
            except (TypeError, ValueError):
                invalid = True
        for raw in payload["episodes"]:
            try:
                episodes.append(
                    self._episode(
                        raw,
                        observations=observations,
                        allowed_events=allowed_events,
                        allowed_subjects=allowed_subjects,
                    )
                )
            except (TypeError, ValueError):
                invalid = True
        for raw in payload["edges"]:
            try:
                edge = self._edge(
                    raw,
                    observations=observations,
                    allowed_events=allowed_events,
                    allowed_subjects=allowed_subjects,
                )
                if edge.status == "rejected":
                    raise ValueError
                edges.append(edge)
            except (TypeError, ValueError):
                invalid = True
        return ProfileExtractionResult(
            facts=tuple(facts),
            episodes=tuple(episodes),
            edges=tuple(edges),
            diagnostic_code="profile_output_invalid" if invalid else None,
            provider_latency_ms=int(response.latency_ms),
            request_bytes=int(response.request_bytes),
            backend=str(response.backend),
            model=str(response.model),
        )

    def _episode(
        self,
        raw: object,
        *,
        observations: tuple[ProfileObservation, ...],
        allowed_events: set[str],
        allowed_subjects: set[str],
    ) -> ProfileEpisode:
        if not isinstance(raw, Mapping):
            raise ValueError
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
        if (
            not participants
            or not set(participants) <= allowed_subjects
            or not evidence_ids
            or not set(evidence_ids) <= allowed_events
            or episode_type not in _EPISODE_TYPES
            or not title
            or len(title) > 80
            or not summary
            or len(summary) > 240
            or not -1.0 <= valence <= 1.0
        ):
            raise ValueError
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
        return ProfileEpisode(
            episode_id=self._stable_id(
                "profile-episode",
                observations[0],
                {
                    "title": title,
                    "participants": participants,
                    "evidence": evidence_ids,
                },
            ),
            persona_id=observations[0].persona_id,
            group_id=observations[0].group_id,
            title=title,
            summary=summary,
            participants=participants,
            source_event_ids=evidence_ids,
            episode_type=episode_type,
            valence=valence,
            importance=importance,
            confidence=confidence,
            status=(
                "confirmed"
                if len(set(evidence_ids)) >= 2 and confidence >= 0.88
                else "proposed"
            ),
            occurred_at=occurred_at,
            last_reinforced_at=reinforced_at,
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
            raise ValueError
        source_id = str(raw.get("source_member_id") or "")
        target_id = str(raw.get("target_member_id") or "")
        direction = str(raw.get("direction") or "")
        evidence_ids = tuple(
            dict.fromkeys(
                str(value) for value in raw.get("evidence_event_ids", ())
            )
        )
        if (
            source_id not in allowed_subjects
            or target_id not in allowed_subjects
            or source_id == target_id
            or direction not in _EDGE_DIRECTIONS
            or not evidence_ids
            or not set(evidence_ids) <= allowed_events
        ):
            raise ValueError
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
            relation_type=str(raw.get("relation_type") or ""),
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
        observations: tuple[ProfileObservation, ...]
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
            ]
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
            "summary": raw.get("summary"),
            "evidence": raw.get("evidence_event_ids"),
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
            provider_latency_ms=int(response.latency_ms),
            request_bytes=int(response.request_bytes),
            backend=str(response.backend),
            model=str(response.model),
        )


__all__ = ("ProfileExtractionResult", "ProfileExtractor")
