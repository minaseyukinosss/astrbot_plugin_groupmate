from __future__ import annotations

import pytest

from groupmate.social_runtime.actions.contracts import (
    ActionEdge,
    ActionNode,
    ActionPlan,
    PlanContext,
)
from groupmate.social_runtime.planner import ActionPlanner
from groupmate.social_runtime.validator import ActionPlanValidator


def _context(**overrides):
    values = {
        "now": 100,
        "scene_version": 3,
        "config_version": 7,
        "persona_version": 11,
        "permissions": ("send_message", "generate_text"),
        "supported_node_kinds": ("GENERATE_TEXT", "SEND_BUNDLE"),
        "allowed_audience_ids": ("user-1", "user-2"),
        "max_nodes": 24,
        "max_plan_duration": 30,
        "max_retries": 2,
        "max_autonomous_followups": 0,
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


def test_planner_builds_text_bundle_after_an_act_governor_result():
    planner = ActionPlanner()
    text_intention = {
        "intention_id": "intention-1",
        "kind": "HELP",
        "target_id": "user-1",
        "topic_id": "topic-1",
    }

    plan = planner.plan(text_intention, _context())

    assert plan.node_kinds() == ("GENERATE_TEXT", "SEND_BUNDLE")
    assert ActionPlanValidator().validate(plan, _context()).accepted is True
