"""Structured response composition stays scene-safe and ordered."""

from groupmate.capabilities.contracts import (
    CapabilityResult,
    CapabilityStatus,
    MediaCandidate,
)
from groupmate.core.response_act import ResponseAct, ResponseActPlan
from groupmate.engine.composer import ResponseComposer
from groupmate.models import InteractionScene, OutboundKind


def _act(act, scene=InteractionScene.SOCIAL_RESPONSE, capability_name=""):
    return ResponseActPlan(
        act=act,
        scene=scene,
        reason_codes=("test",),
        capability_name=capability_name,
    )


def _media(
    media_id,
    locator,
    *,
    source="capability",
    purpose="task_result",
    safety_label="provider_approved",
    semantic_label="result",
):
    return MediaCandidate(
        media_id=media_id,
        source=source,
        locator=locator,
        media_kind="image",
        semantic_label=semantic_label,
        purpose=purpose,
        safety_label=safety_label,
    )


def test_composer_keeps_normal_dialogue_as_text():
    draft = ResponseComposer().compose(
        text="谢谢你呀",
        act_plan=_act(ResponseAct.RECIPROCATE),
        quote_message_id="m1",
    )

    assert [item.kind for item in draft.segments] == [OutboundKind.TEXT]
    assert draft.segments[0].text == "谢谢你呀"
    assert draft.quote_message_id == "m1"
    assert draft.response_act is ResponseAct.RECIPROCATE


def test_task_success_media_is_kept_and_boundary_stays_text_only(tmp_path):
    task_image = tmp_path / "result.png"
    task_image.write_bytes(b"image")
    capability = CapabilityResult(
        CapabilityStatus.SUCCESS,
        "vision",
        facts=("结果已生成",),
        media_candidates=(_media("result-1", str(task_image)),),
    )
    task_draft = ResponseComposer().compose(
        text="结果在这里",
        act_plan=_act(
            ResponseAct.TASK_HANDOFF,
            InteractionScene.TASK_REQUEST,
            capability_name="vision",
        ),
        quote_message_id="m2",
        capability_result=capability,
    )
    boundary_draft = ResponseComposer().compose(
        text="不行。",
        act_plan=_act(ResponseAct.BOUNDARY),
        quote_message_id="m3",
    )

    assert [
        item.media_id
        for item in task_draft.segments
        if item.kind is OutboundKind.IMAGE
    ] == ["result-1"]
    assert [item.kind for item in boundary_draft.segments] == [OutboundKind.TEXT]


def test_composer_drops_untrusted_capability_media(tmp_path):
    image = tmp_path / "result.png"
    image.write_bytes(b"image")
    untrusted = CapabilityResult(
        CapabilityStatus.SUCCESS,
        "vision",
        facts=("描述",),
        media_candidates=(
            _media("unsafe-1", str(image), safety_label="untrusted"),
        ),
    )

    draft = ResponseComposer().compose(
        text="我看到了。",
        act_plan=_act(ResponseAct.ANSWER, InteractionScene.DIRECT_ADDRESS),
        quote_message_id=None,
        capability_result=untrusted,
    )

    assert [item.kind for item in draft.segments] == [OutboundKind.TEXT]


def test_composer_drops_approved_media_with_unsafe_locator():
    unsafe_locator = CapabilityResult(
        CapabilityStatus.SUCCESS,
        "vision",
        facts=("描述",),
        media_candidates=(
            _media("unsafe-ref", "../relative.png"),
        ),
    )

    draft = ResponseComposer().compose(
        text="我看到了。",
        act_plan=_act(ResponseAct.ANSWER, InteractionScene.DIRECT_ADDRESS),
        quote_message_id=None,
        capability_result=unsafe_locator,
    )

    assert [item.kind for item in draft.segments] == [OutboundKind.TEXT]


def test_composer_allows_safe_capability_image_only_draft(tmp_path):
    image = tmp_path / "result.png"
    image.write_bytes(b"image")
    capability = CapabilityResult(
        CapabilityStatus.SUCCESS,
        "vision",
        facts=("结果",),
        media_candidates=(_media("result-1", str(image)),),
    )

    draft = ResponseComposer().compose(
        text="",
        act_plan=_act(ResponseAct.VISUAL_REACTION, InteractionScene.DIRECT_ADDRESS),
        quote_message_id="m4",
        capability_result=capability,
    )

    assert [item.kind for item in draft.segments] == [OutboundKind.IMAGE]


def test_composer_has_no_local_reaction_argument():
    from inspect import signature

    assert "reaction" not in signature(ResponseComposer.compose).parameters
