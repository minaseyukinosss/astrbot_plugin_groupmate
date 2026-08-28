"""Shared, persona-independent game knowledge domain."""

from .contracts import (
    ClaimKind,
    ClaimStatus,
    DiscourseReferent,
    EvidenceLevel,
    KnowledgeNeed,
    KnowledgeNeedOutcome,
    KnowledgeObservation,
    KnowledgeScope,
    ObservationStatus,
    OriginClass,
    ResolvedEntity,
    ResolvedTerm,
    RiskClass,
    TopicUnderstandingFrame,
    VersionReference,
)
from .observation import (
    KnowledgeObservationService,
    KnowledgeOriginClassifier,
    OriginDecision,
)


__all__ = (
    "ClaimKind",
    "ClaimStatus",
    "DiscourseReferent",
    "EvidenceLevel",
    "KnowledgeNeed",
    "KnowledgeNeedOutcome",
    "KnowledgeObservation",
    "KnowledgeObservationService",
    "KnowledgeOriginClassifier",
    "KnowledgeScope",
    "ObservationStatus",
    "OriginClass",
    "OriginDecision",
    "ResolvedEntity",
    "ResolvedTerm",
    "RiskClass",
    "TopicUnderstandingFrame",
    "VersionReference",
)
