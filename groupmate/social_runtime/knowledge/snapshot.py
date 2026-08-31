"""Deterministic construction of immutable, bounded knowledge snapshots."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable

from .contracts import (
    ClaimStatus,
    KnowledgeFact,
    KnowledgeNeed,
    KnowledgeSnapshot,
    RiskClass,
    StrictFactFragment,
    TopicUnderstandingFrame,
)


class KnowledgeSnapshotBuilder:
    def build(
        self,
        frame: TopicUnderstandingFrame,
        need: KnowledgeNeed,
        hits: Iterable[KnowledgeFact],
        now: int,
        *,
        version_slot_id: str | None = None,
        region: str | None = None,
        platform: str | None = None,
        version_state_revision: int = 0,
    ) -> KnowledgeSnapshot:
        timestamp = self._integer(now, "now")
        if timestamp < 0 or need.expires_at <= timestamp:
            raise ValueError("knowledge need has expired")
        revision = self._integer(
            version_state_revision, "version_state_revision"
        )
        if revision < 0:
            raise ValueError("version_state_revision must not be negative")
        reference = frame.version_reference
        expected_region = region or (reference.region if reference else None)
        expected_platform = platform or (
            reference.platform if reference else None
        )
        accepted: list[KnowledgeFact] = []
        accepted_sources: list[str] = []
        seen_ids: set[str] = set()
        unique_hits: dict[str, KnowledgeFact] = {}
        for fact in tuple(hits):
            if not isinstance(fact, KnowledgeFact):
                raise ValueError("hits must contain KnowledgeFact objects")
            previous = unique_hits.get(fact.knowledge_id)
            if previous is not None and previous != fact:
                raise ValueError("conflicting duplicate knowledge ID")
            unique_hits[fact.knowledge_id] = fact
        for fact in sorted(
            unique_hits.values(), key=lambda item: item.knowledge_id
        ):
            if fact.knowledge_id in seen_ids or not self._matches(
                fact,
                frame=frame,
                now=timestamp,
                version_slot_id=version_slot_id,
                region=expected_region,
                platform=expected_platform,
                version_state_revision=revision,
            ):
                continue
            next_sources = list(accepted_sources)
            for source_id in fact.source_ids:
                if source_id not in next_sources:
                    next_sources.append(source_id)
            if len(next_sources) > 2:
                continue
            accepted.append(fact)
            accepted_sources = next_sources
            seen_ids.add(fact.knowledge_id)
            if len(accepted) == 8:
                break

        fragments = tuple(
            StrictFactFragment.create(
                fragment_id=self._digest(
                    {
                        "knowledge_id": fact.knowledge_id,
                        "risk_class": fact.risk_class.value,
                        "text": fact.safe_summary,
                    },
                    prefix="knowledge-fragment",
                ),
                knowledge_id=fact.knowledge_id,
                text=fact.safe_summary,
                risk_class=fact.risk_class,
            )
            for fact in accepted
            if fact.risk_class is not RiskClass.STABLE_SEMANTIC
        )
        expires_at = min(
            (need.expires_at, *(fact.expires_at for fact in accepted))
        )
        identity = {
            "topic_frame_id": frame.frame_id,
            "facts": [self._fact_payload(fact) for fact in accepted],
            "fragments": [
                {
                    "fragment_id": item.fragment_id,
                    "knowledge_id": item.knowledge_id,
                    "text": item.text,
                    "risk_class": item.risk_class.value,
                }
                for item in fragments
            ],
            "source_ids": accepted_sources,
            "checked_at": timestamp,
            "expires_at": expires_at,
            "version_state_revision": revision,
        }
        return KnowledgeSnapshot.create(
            snapshot_id=self._digest(identity, prefix="knowledge-snapshot"),
            topic_frame_id=frame.frame_id,
            allowed_knowledge_facts=tuple(accepted),
            strict_fact_fragments=fragments,
            source_ids=tuple(accepted_sources),
            checked_at=timestamp,
            expires_at=expires_at,
            version_state_revision=revision,
        )

    @staticmethod
    def _matches(
        fact: KnowledgeFact,
        *,
        frame: TopicUnderstandingFrame,
        now: int,
        version_slot_id: str | None,
        region: str | None,
        platform: str | None,
        version_state_revision: int,
    ) -> bool:
        if (
            fact.status is not ClaimStatus.ACTIVE
            or fact.game_entity_id not in frame.game_ids
            or fact.checked_at > now
            or fact.expires_at <= now
        ):
            return False
        if fact.region is not None and fact.region != region:
            return False
        if fact.platform is not None and fact.platform != platform:
            return False
        if fact.version_slot_id is not None and (
            version_slot_id is None or fact.version_slot_id != version_slot_id
        ):
            return False
        if fact.risk_class is not RiskClass.STABLE_SEMANTIC and (
            version_state_revision < 1
            or fact.version_state_revision != version_state_revision
        ):
            return False
        return True

    @staticmethod
    def _fact_payload(fact: KnowledgeFact) -> dict[str, object]:
        return {
            "knowledge_id": fact.knowledge_id,
            "game_entity_id": fact.game_entity_id,
            "entity_id": fact.entity_id,
            "safe_summary": fact.safe_summary,
            "risk_class": fact.risk_class.value,
            "evidence_level": fact.evidence_level.value,
            "qualifier": fact.qualifier.value,
            "checked_at": fact.checked_at,
            "expires_at": fact.expires_at,
            "source_ids": fact.source_ids,
            "version_slot_id": fact.version_slot_id,
            "region": fact.region,
            "platform": fact.platform,
            "status": fact.status.value,
            "version_state_revision": fact.version_state_revision,
        }

    @staticmethod
    def _digest(payload: dict[str, object], *, prefix: str) -> str:
        packed = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return f"{prefix}:{hashlib.sha256(packed).hexdigest()}"

    @staticmethod
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


__all__ = ("KnowledgeSnapshotBuilder",)
