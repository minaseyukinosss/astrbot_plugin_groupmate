"""Shared, persona-independent game knowledge domain."""

from .contracts import (
    ClaimKind,
    ClaimStatus,
    DiscourseReferent,
    EvidenceLevel,
    KnowledgeNeed,
    KnowledgeNeedOutcome,
    KnowledgeObservation,
    KnowledgeResolverPort,
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
from .resolver import KnowledgeEntityResolver
from .retrieval import (
    KnowledgeHit,
    KnowledgeNeedAssessor,
    KnowledgeRetriever,
)


__all__ = (
    "ClaimKind",
    "ClaimStatus",
    "DiscourseReferent",
    "EvidenceLevel",
    "KnowledgeNeed",
    "KnowledgeNeedAssessor",
    "KnowledgeNeedOutcome",
    "KnowledgeObservation",
    "KnowledgeObservationService",
    "KnowledgeOriginClassifier",
    "KnowledgeEntityResolver",
    "KnowledgeHit",
    "KnowledgeRetriever",
    "KnowledgeResolverPort",
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
