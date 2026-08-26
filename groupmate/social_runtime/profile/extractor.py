"""Strict background extraction of profile candidates."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Mapping

from .contracts import ProfileFact, ProfileFactCandidate, ProfileObservation
from .policy import FACT_CATEGORIES, ProfileEvidencePolicy


@dataclass(frozen=True)
class ProfileExtractionResult:
    facts: tuple[ProfileFact, ...] = ()
    episodes: tuple[object, ...] = ()
    edges: tuple[object, ...] = ()
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
        return ProfileExtractionResult(
            facts=tuple(facts),
            diagnostic_code="profile_output_invalid" if invalid else None,
            provider_latency_ms=int(response.latency_ms),
            request_bytes=int(response.request_bytes),
            backend=str(response.backend),
            model=str(response.model),
        )

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
    def _invalid(response) -> ProfileExtractionResult:
        return ProfileExtractionResult(
            diagnostic_code="profile_output_invalid",
            provider_latency_ms=int(response.latency_ms),
            request_bytes=int(response.request_bytes),
            backend=str(response.backend),
            model=str(response.model),
        )


__all__ = ("ProfileExtractionResult", "ProfileExtractor")
