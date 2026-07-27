"""Deterministic scene-to-response-act planning."""

from dataclasses import FrozenInstanceError

import pytest

from groupmate.core.response_act import (
    ResponseAct,
    ResponseActPlan,
    plan_response_act,
)
from groupmate.models import InteractionScene, ReplyMode


def _plan(scene, **overrides):
    values = {
        "reply_mode": ReplyMode.SHORT_SOCIAL,
        "text": "普通消息",
    }
    values.update(overrides)
    return plan_response_act(scene, **values)


def test_response_act_defines_required_vocabulary():
    assert {act.name for act in ResponseAct} >= {
        "ACKNOWLEDGE",
        "ANSWER",
        "CLARIFY",
        "RECIPROCATE",
        "PLAYFUL_REPLY",
        "BOUNDARY",
        "TASK_HANDOFF",
        "TASK_UNSUPPORTED",
        "VISUAL_REACTION",
    }


def test_response_act_plan_is_immutable_and_explains_its_choice():
    plan = _plan(InteractionScene.DIRECT_ADDRESS, text="Nova")

    assert plan.scene is InteractionScene.DIRECT_ADDRESS
    assert plan.reason_codes
    assert plan.required_information == ()
    with pytest.raises(FrozenInstanceError):
        plan.act = ResponseAct.ANSWER


def test_response_act_plan_copies_mutable_sequences():
    reasons = ["scene:direct_address"]
    missing = ["目标"]
    plan = ResponseActPlan(
        ResponseAct.CLARIFY,
        InteractionScene.DIRECT_ADDRESS,
        reasons,
        missing,
    )

    reasons.append("changed")
    missing.append("changed")

    assert plan.reason_codes == ("scene:direct_address",)
    assert plan.required_information == ("目标",)
    assert hash(plan)


@pytest.mark.parametrize(
    ("scene", "text", "expected_name"),
    (
        (InteractionScene.DIRECT_ADDRESS, "这个怎么做？", "ANSWER"),
        (InteractionScene.SOCIAL_RESPONSE, "谢谢你", "RECIPROCATE"),
        (InteractionScene.SOCIAL_RESPONSE, "来比比谁更快", "PLAYFUL_REPLY"),
    ),
)
def test_non_task_scenes_map_input_signals_to_acts(scene, text, expected_name):
    plan = _plan(scene, text=text)

    assert plan.act.name == expected_name


def test_visual_input_maps_to_visual_reaction():
    plan = _plan(
        InteractionScene.DIRECT_ADDRESS,
        text="",
        has_visual=True,
    )

    assert plan.act.name == "VISUAL_REACTION"


def test_boundary_has_priority_over_task_handling():
    plan = _plan(
        InteractionScene.TASK_REQUEST,
        text="帮我做这个",
        boundary_required=True,
        task_supported=True,
        required_information=("目标",),
    )

    assert plan.act.name == "BOUNDARY"
    assert "boundary_required" in plan.reason_codes
    assert plan.required_information == ()


@pytest.mark.parametrize(
    ("task_supported", "required_information", "expected_name"),
    (
        (True, ("目标语言",), "CLARIFY"),
        (True, (), "TASK_HANDOFF"),
        (False, (), "TASK_UNSUPPORTED"),
    ),
)
def test_task_scene_uses_information_then_capability_priority(
    task_supported, required_information, expected_name
):
    plan = _plan(
        InteractionScene.TASK_REQUEST,
        text="帮我处理一下",
        task_supported=task_supported,
        required_information=required_information,
    )

    assert plan.act.name == expected_name
    assert plan.required_information == required_information


def test_same_inputs_always_produce_the_same_plan():
    kwargs = {
        "reply_mode": ReplyMode.SHORT_SOCIAL,
        "text": "谢谢你",
        "aliases": ("Nova",),
    }

    plans = {
        plan_response_act(InteractionScene.SOCIAL_RESPONSE, **kwargs)
        for _ in range(50)
    }

    assert len(plans) == 1


def test_bare_name_acknowledgement_uses_only_caller_supplied_aliases():
    injected = plan_response_act(
        InteractionScene.DIRECT_ADDRESS,
        reply_mode=ReplyMode.SHORT_SOCIAL,
        text="Nova！",
        aliases=("Nova",),
    )
    not_injected = plan_response_act(
        InteractionScene.DIRECT_ADDRESS,
        reply_mode=ReplyMode.SHORT_SOCIAL,
        text="Nova！",
    )

    assert injected.act.name == "ACKNOWLEDGE"
    assert not_injected.act.name == "ANSWER"
