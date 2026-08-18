from __future__ import annotations

import pytest

from groupmate.social_runtime.actions.contracts import (
    ActionEdge,
    ActionNode,
    ActionPlan,
)


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
        "nodes": (),
        "edges": (),
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


def test_plan_exposes_node_kinds_in_declared_order():
    plan = _plan(
        nodes=(
            ActionNode(
                "generate",
                "GENERATE_TEXT",
                "generator",
                0,
                115,
                permission="generate_text",
            ),
            ActionNode(
                "send",
                "SEND_BUNDLE",
                "delivery",
                0,
                119,
                permission="send_message",
                visible=True,
            ),
        )
    )

    assert plan.node_kinds() == ("GENERATE_TEXT", "SEND_BUNDLE")


def test_callers_cannot_mark_an_action_plan_validated():
    with pytest.raises(TypeError, match="validated"):
        _plan(validated=True)


def test_action_edge_is_an_explicit_node_reference():
    edge = ActionEdge("generate", "send")

    assert edge.source_node_id == "generate"
    assert edge.target_node_id == "send"
