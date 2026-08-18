"""Immutable contracts for deterministic, finite social action plans."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


MAX_ACTION_PLAN_NODES = 24
MAX_ACTION_PLAN_DURATION = 24 * 60 * 60
MAX_ACTION_NODE_RETRIES = 2
MAX_AUTONOMOUS_FOLLOWUPS = 1


@dataclass(frozen=True)
class ActionNode:
    node_id: str
    kind: str
    owner_id: str
    retry_limit: int
    deadline_at: Optional[int]
    permission: Optional[str] = None
    visible: bool = False
    autonomous_followup: bool = False


@dataclass(frozen=True)
class ActionEdge:
    source_node_id: str
    target_node_id: str


@dataclass(frozen=True)
class ActionPlan:
    plan_id: str
    correlation_id: str
    group_id: str
    persona_id: str
    scene_version: int
    config_version: int
    persona_version: int
    constitution_version: int
    relationship_version: int
    state_version: int
    intention_ids: Tuple[str, ...]
    audience: Tuple[str, ...]
    topic_id: Optional[str]
    origin: str
    nodes: Tuple[ActionNode, ...]
    edges: Tuple[ActionEdge, ...]
    constraints: Tuple[str, ...]
    constitution_approved: bool
    relationship_approved: bool
    state_approved: bool
    risk_score: int
    media_references: Tuple[str, ...]
    budget_cost: int
    concurrency: int
    confirmation_ids: Tuple[str, ...]
    expires_at: int

    def node_kinds(self) -> Tuple[str, ...]:
        return tuple(node.kind for node in self.nodes)


@dataclass(frozen=True)
class PlanContext:
    """Frozen inputs against which a plan is both built and validated."""

    now: int
    group_id: str
    persona_id: str
    scene_version: int
    config_version: int
    persona_version: int
    constitution_version: int
    relationship_version: int
    state_version: int
    requester_permissions: Tuple[str, ...]
    supported_node_kinds: Tuple[str, ...]
    allowed_audience_ids: Tuple[str, ...]
    allowed_owner_ids: Tuple[str, ...]
    max_nodes: int
    max_plan_duration: int
    max_retries: int
    max_autonomous_followups: int
    constitution_allowed: bool
    relationship_allowed: bool
    state_allowed: bool
    max_risk_score: int
    allowed_media_references: Tuple[str, ...]
    max_budget_cost: int
    max_concurrency: int
    confirmed_ids: Tuple[str, ...]


_INVALID_DISPOSITIONS = frozenset(
    {"REDUCE", "REPLAN", "DEFER", "CLARIFY", "ABANDON"}
)


@dataclass(frozen=True)
class PlanValidation:
    accepted: bool
    errors: Tuple[str, ...]
    reduced_plan: Optional[ActionPlan]
    disposition: Optional[str] = None

    def __post_init__(self) -> None:
        if self.accepted:
            if self.errors or self.reduced_plan is not None or self.disposition is not None:
                raise ValueError("accepted validation cannot carry a disposition or reduction")
            return
        if self.disposition not in _INVALID_DISPOSITIONS:
            raise ValueError("invalid plan requires a governance disposition")


__all__ = (
    "ActionEdge",
    "ActionNode",
    "ActionPlan",
    "MAX_ACTION_NODE_RETRIES",
    "MAX_ACTION_PLAN_DURATION",
    "MAX_ACTION_PLAN_NODES",
    "MAX_AUTONOMOUS_FOLLOWUPS",
    "PlanContext",
    "PlanValidation",
)
