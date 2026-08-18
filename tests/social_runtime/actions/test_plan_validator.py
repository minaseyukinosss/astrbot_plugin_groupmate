from __future__ import annotations

import pytest

from groupmate.social_runtime.actions.contracts import (
    ActionEdge,
    ActionNode,
    ActionPlan,
    PlanContext,
)
from groupmate.social_runtime.governor import GovernorResult
from groupmate.social_runtime.planner import ActionPlanner
from groupmate.social_runtime.validator import ActionPlanValidator


def _context(**overrides):
    values = {
        "now": 100,
        "group_id": "group-1",
        "persona_id": "persona-1",
        "scene_version": 3,
        "config_version": 7,
        "persona_version": 11,
        "constitution_version": 13,
        "relationship_version": 17,
        "state_version": 19,
        "requester_permissions": ("send_message", "generate_text"),
        "supported_node_kinds": ("GENERATE_TEXT", "SEND_BUNDLE"),
        "allowed_audience_ids": ("user-1", "user-2"),
        "allowed_owner_ids": (
            "generator",
            "delivery",
            "delivery-a",
            "delivery-b",
            "text_generator",
            "bundle_delivery",
        ),
        "max_nodes": 24,
        "max_plan_duration": 30,
        "max_retries": 2,
        "max_autonomous_followups": 0,
        "constitution_allowed": True,
        "relationship_allowed": True,
        "state_allowed": True,
        "max_risk_score": 5,
        "allowed_media_references": ("media-1",),
        "max_budget_cost": 10,
        "max_concurrency": 2,
        "confirmed_ids": ("confirm-1",),
    }
    values.update(overrides)
    return PlanContext(**values)


def _plan(**overrides):
    values = {
        "plan_id": "plan-1",
        "correlation_id": "corr-1",
        "group_id": "group-1",
        "persona_id": "persona-1",
        "scene_version": 3,
        "config_version": 7,
        "persona_version": 11,
        "constitution_version": 13,
        "relationship_version": 17,
        "state_version": 19,
        "intention_ids": ("intention-1",),
        "audience": ("user-1",),
        "topic_id": "topic-1",
        "origin": "governor",
        "nodes": (
            ActionNode("generate", "GENERATE_TEXT", "generator", 0, 115),
            ActionNode(
                "send",
                "SEND_BUNDLE",
                "delivery",
                0,
                119,
                permission="send_message",
                visible=True,
            ),
        ),
        "edges": (ActionEdge("generate", "send"),),
        "constraints": (),
        "constitution_approved": True,
        "relationship_approved": True,
        "state_approved": True,
        "risk_score": 0,
        "media_references": (),
        "budget_cost": 0,
        "concurrency": 1,
        "confirmation_ids": (),
        "expires_at": 120,
    }
    values.update(overrides)
    return ActionPlan(**values)


@pytest.mark.parametrize(
    ("plan", "error"),
    [
        (
            _plan(
                edges=(
                    ActionEdge("generate", "send"),
                    ActionEdge("send", "generate"),
                )
            ),
            "plan_cycle",
        ),
        (
            _plan(
                nodes=tuple(
                    ActionNode(f"node-{index}", "GENERATE_TEXT", "generator", 0, 115)
                    for index in range(25)
                ),
                edges=(),
            ),
            "node_limit_exceeded",
        ),
        (
            _plan(
                nodes=(
                    ActionNode("first", "SEND_BUNDLE", "delivery-a", 0, 119, visible=True),
                    ActionNode("second", "SEND_BUNDLE", "delivery-b", 0, 119, visible=True),
                ),
                edges=(),
            ),
            "multiple_visible_owners",
        ),
        (_plan(scene_version=2), "stale_scene"),
        (
            _plan(
                expires_at=100,
                nodes=(
                    ActionNode("generate", "GENERATE_TEXT", "generator", 0, 99),
                    ActionNode(
                        "send",
                        "SEND_BUNDLE",
                        "delivery",
                        0,
                        100,
                        permission="send_message",
                        visible=True,
                    ),
                ),
            ),
            "plan_expired",
        ),
        (
            _plan(
                nodes=(
                    ActionNode(
                        "generate",
                        "GENERATE_TEXT",
                        "generator",
                        0,
                        115,
                        permission="use_capability",
                    ),
                ),
                edges=(),
            ),
            "missing_permission",
        ),
    ],
)
def test_validator_rejects_invalid_plans(plan, error):
    validation = ActionPlanValidator().validate(plan, _context())

    assert validation.accepted is False
    assert validation.errors == (error,)
    assert validation.reduced_plan is None
    assert validation.disposition in {"REDUCE", "REPLAN", "DEFER", "CLARIFY", "ABANDON"}


def test_validator_rejects_edges_with_unknown_node_references():
    validation = ActionPlanValidator().validate(
        _plan(edges=(ActionEdge("generate", "missing"),)), _context()
    )

    assert validation.errors == ("unknown_edge_node",)


def test_validator_rejects_unbounded_node_deadlines_and_retries():
    validation = ActionPlanValidator().validate(
        _plan(nodes=(ActionNode("generate", "GENERATE_TEXT", "generator", 3, None),), edges=()),
        _context(),
    )

    assert validation.errors == ("retry_limit_exceeded", "node_deadline_missing")


def _governor_result(outcome="ACT", selected=("intention-1",)):
    return GovernorResult(
        outcome=outcome,
        selected_intention_ids=selected if outcome == "ACT" else (),
        rejected=(),
        reason_codes=(),
        reconsider_at=110 if outcome == "DEFER" else None,
        constraints=("hard_gate_v1",),
    )


def test_planner_requires_governor_result_and_uses_an_act_selection():
    planner = ActionPlanner()
    text_intention = {
        "intention_id": "intention-1",
        "kind": "HELP",
        "target_id": "user-1",
        "topic_id": "topic-1",
    }

    with pytest.raises(TypeError):
        planner.plan(text_intention, _context())

    plan = planner.plan(text_intention, _context(), _governor_result())

    assert plan.node_kinds() == ("GENERATE_TEXT", "SEND_BUNDLE")
    assert plan.group_id == "group-1"
    assert plan.persona_id == "persona-1"
    assert ActionPlanValidator().validate(plan, _context()).accepted is True


@pytest.mark.parametrize("field", ("group_id", "persona_id"))
def test_planner_refuses_context_without_a_real_group_or_persona_identity(field):
    with pytest.raises(ValueError, match=field):
        ActionPlanner().plan(
            {"intention_id": "intention-1", "target_id": "user-1"},
            _context(**{field: ""}),
            _governor_result(),
        )


@pytest.mark.parametrize(
    ("result", "message"),
    [
        (_governor_result("OBSERVE"), "only an ACT"),
        (_governor_result("ACT", ("another-intention",)), "not selected"),
    ],
)
def test_planner_rejects_governor_results_that_do_not_authorize_the_intention(
    result, message
):
    with pytest.raises(ValueError, match=message):
        ActionPlanner().plan(
            {"intention_id": "intention-1", "target_id": "user-1"},
            _context(),
            result,
        )


@pytest.mark.parametrize(
    ("plan", "context", "error"),
    [
        (
            _plan(
                nodes=tuple(
                    ActionNode("node-{0}".format(index), "GENERATE_TEXT", "generator", 0, 115)
                    for index in range(25)
                ),
                edges=(),
            ),
            _context(max_nodes=100),
            "node_limit_exceeded",
        ),
        (_plan(expires_at=100 + 86401), _context(max_plan_duration=100000), "plan_duration_exceeded"),
        (
            _plan(nodes=(ActionNode("generate", "GENERATE_TEXT", "generator", 3, 115),), edges=()),
            _context(max_retries=10),
            "retry_limit_exceeded",
        ),
        (
            _plan(
                nodes=(
                    ActionNode("first", "GENERATE_TEXT", "generator", 0, 115, autonomous_followup=True),
                    ActionNode("second", "GENERATE_TEXT", "generator", 0, 115, autonomous_followup=True),
                ),
                edges=(),
            ),
            _context(max_autonomous_followups=10),
            "autonomous_followup_limit_exceeded",
        ),
    ],
)
def test_global_plan_limits_cannot_be_weakened_by_context(plan, context, error):
    assert ActionPlanValidator().validate(plan, context).errors == (error,)


@pytest.mark.parametrize(
    ("plan", "error"),
    [
        (_plan(group_id="other-group"), "wrong_group"),
        (_plan(persona_id="other-persona"), "wrong_persona"),
        (_plan(config_version=6), "stale_config"),
        (_plan(persona_version=10), "stale_persona"),
        (_plan(constitution_version=12), "stale_constitution"),
        (_plan(relationship_version=16), "stale_relationship"),
        (_plan(state_version=18), "stale_state"),
    ],
)
def test_validator_rejects_identity_and_frozen_version_mismatches(plan, error):
    assert ActionPlanValidator().validate(plan, _context()).errors == (error,)


@pytest.mark.parametrize(
    ("plan", "context", "error"),
    [
        (_plan(constitution_approved=False), _context(), "constitution_rejected"),
        (_plan(relationship_approved=False), _context(), "relationship_rejected"),
        (_plan(state_approved=False), _context(), "state_rejected"),
        (_plan(risk_score=6), _context(), "risk_limit_exceeded"),
        (_plan(media_references=("media-2",)), _context(), "media_not_allowed"),
        (_plan(budget_cost=11), _context(), "budget_limit_exceeded"),
        (_plan(concurrency=3), _context(), "concurrency_limit_exceeded"),
        (_plan(confirmation_ids=("confirm-2",)), _context(), "confirmation_missing"),
        (
            _plan(
                nodes=(
                    ActionNode(
                        "generate",
                        "GENERATE_TEXT",
                        "generator",
                        0,
                        115,
                        permission="admin_only",
                    ),
                ),
                edges=(),
            ),
            _context(relationship_allowed=True),
            "missing_permission",
        ),
    ],
)
def test_validator_deterministically_enforces_frozen_safety_decisions(
    plan, context, error
):
    assert ActionPlanValidator().validate(plan, context).errors == (error,)


def test_validator_rejects_expired_deadlines_and_unknown_owners():
    expired = _plan(nodes=(ActionNode("generate", "GENERATE_TEXT", "generator", 0, 100),), edges=())
    unknown_owner = _plan(nodes=(ActionNode("generate", "GENERATE_TEXT", "other", 0, 115),), edges=())

    assert ActionPlanValidator().validate(expired, _context()).errors == ("node_deadline_expired",)
    assert ActionPlanValidator().validate(unknown_owner, _context()).errors == ("owner_not_allowed",)
