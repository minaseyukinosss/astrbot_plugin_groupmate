"""Deterministic safety validation for ActionPlan declarations."""

from __future__ import annotations

from typing import List, Set, Tuple

from .actions.contracts import ActionPlan, PlanContext, PlanValidation


class ActionPlanValidator:
    def validate(self, plan: ActionPlan, context: PlanContext) -> PlanValidation:
        errors: List[str] = []
        self._validate_plan_scope(plan, context, errors)
        self._validate_nodes(plan, context, errors)
        self._validate_edges_and_dag(plan, errors)
        self._validate_visible_owner(plan, errors)
        if errors:
            return PlanValidation(
                accepted=False,
                errors=tuple(errors),
                reduced_plan=None,
                disposition=self._disposition(errors),
            )
        return PlanValidation(True, (), None)

    @staticmethod
    def _validate_plan_scope(
        plan: ActionPlan, context: PlanContext, errors: List[str]
    ) -> None:
        if plan.scene_version != context.scene_version:
            errors.append("stale_scene")
        if plan.expires_at <= context.now:
            errors.append("plan_expired")
        elif plan.expires_at > context.now + context.max_plan_duration:
            errors.append("plan_duration_exceeded")
        if len(plan.nodes) > context.max_nodes:
            errors.append("node_limit_exceeded")
        if any(audience not in context.allowed_audience_ids for audience in plan.audience):
            errors.append("unknown_audience")

    @staticmethod
    def _validate_nodes(
        plan: ActionPlan, context: PlanContext, errors: List[str]
    ) -> None:
        node_ids: Set[str] = set()
        followups = 0
        for node in plan.nodes:
            if not node.node_id or node.node_id in node_ids:
                ActionPlanValidator._append_once(errors, "duplicate_node_id")
            node_ids.add(node.node_id)
            if node.kind not in context.supported_node_kinds:
                ActionPlanValidator._append_once(errors, "unsupported_node_kind")
            if not node.owner_id:
                ActionPlanValidator._append_once(errors, "node_owner_missing")
            if node.retry_limit < 0 or node.retry_limit > context.max_retries:
                ActionPlanValidator._append_once(errors, "retry_limit_exceeded")
            if node.deadline_at is None:
                ActionPlanValidator._append_once(errors, "node_deadline_missing")
            elif node.deadline_at > plan.expires_at:
                ActionPlanValidator._append_once(errors, "node_deadline_exceeded")
            if node.permission and node.permission not in context.permissions:
                ActionPlanValidator._append_once(errors, "missing_permission")
            if node.autonomous_followup:
                followups += 1
        if followups > context.max_autonomous_followups:
            errors.append("autonomous_followup_limit_exceeded")

    @staticmethod
    def _validate_edges_and_dag(plan: ActionPlan, errors: List[str]) -> None:
        node_ids = {node.node_id for node in plan.nodes}
        adjacency = {node_id: set() for node_id in node_ids}
        indegree = {node_id: 0 for node_id in node_ids}
        has_unknown_reference = False
        for edge in plan.edges:
            if edge.source_node_id not in node_ids or edge.target_node_id not in node_ids:
                has_unknown_reference = True
                continue
            if edge.target_node_id not in adjacency[edge.source_node_id]:
                adjacency[edge.source_node_id].add(edge.target_node_id)
                indegree[edge.target_node_id] += 1
        if has_unknown_reference:
            errors.append("unknown_edge_node")
            return
        if len(ActionPlanValidator._kahn_order(adjacency, indegree)) != len(node_ids):
            errors.append("plan_cycle")

    @staticmethod
    def _kahn_order(adjacency: dict, indegree: dict) -> Tuple[str, ...]:
        ready = sorted(node_id for node_id, degree in indegree.items() if degree == 0)
        order = []
        while ready:
            node_id = ready.pop(0)
            order.append(node_id)
            for target_id in sorted(adjacency[node_id]):
                indegree[target_id] -= 1
                if indegree[target_id] == 0:
                    ready.append(target_id)
                    ready.sort()
        return tuple(order)

    @staticmethod
    def _validate_visible_owner(plan: ActionPlan, errors: List[str]) -> None:
        visible_owners = {node.owner_id for node in plan.nodes if node.visible}
        if len(visible_owners) > 1:
            errors.append("multiple_visible_owners")

    @staticmethod
    def _disposition(errors: List[str]) -> str:
        if "stale_scene" in errors:
            return "REPLAN"
        if "plan_expired" in errors:
            return "ABANDON"
        if "missing_permission" in errors:
            return "CLARIFY"
        return "REDUCE"

    @staticmethod
    def _append_once(errors: List[str], error: str) -> None:
        if error not in errors:
            errors.append(error)


__all__ = ("ActionPlanValidator",)
