import inspect

import pytest

from eval.shadow_extract import LocalIdHasher
from eval.shadow_projector import ShadowProjector
from groupmate.core.response_act import ResponseAct
from groupmate.models import GroupPolicy, InteractionScene
from tests.test_reference_labeler import example


@pytest.fixture
def projector():
    return ShadowProjector(
        GroupPolicy(
            aliases=("爱弥斯",),
            spontaneous_cooldown_seconds=0,
            humanize_delay_enabled=False,
        ),
        LocalIdHasher(b"a" * 32),
        target_uin="20002",
        target_alias="小维",
        current_alias="爱弥斯",
    )


def test_direct_social_boundary_and_vision_task_projection(projector):
    direct = projector.project(example("小维"))
    social = projector.project(example("小维，谢谢你"))
    boundary = projector.project(example("小维，叫你老婆行吗"))
    visual_task = projector.project(example("小维，帮我看看这张图", media=True))

    assert direct.trigger == "alias_direct"
    assert direct.act is ResponseAct.ACKNOWLEDGE
    assert direct.quote_allowed is True
    assert social.scene is InteractionScene.SOCIAL_RESPONSE
    assert social.decorative_media_allowed is True
    assert boundary.act is ResponseAct.BOUNDARY
    assert boundary.decorative_media_allowed is False
    assert visual_task.scene is InteractionScene.TASK_REQUEST
    assert visual_task.act is ResponseAct.TASK_HANDOFF
    assert visual_task.capability_media_allowed is False


def test_external_knowledge_native_wake_has_one_agent_owner(projector):
    projected = projector.project(
        example("搜索今天发布的公告", mentions_target=True)
    )
    assert projected.owner == "astrbot_agent"
    assert projected.owner_count == 1
    assert projected.would_reply is True
    assert projected.completion_claim_allowed is False
    assert "external_handoff" in projected.reason_codes


def test_alias_external_request_remains_groupmate_owned(projector):
    projected = projector.project(example("小维，搜索今天发布的公告"))
    assert projected.owner == "groupmate"
    assert projected.act is ResponseAct.ANSWER
    assert "external_knowledge_groupmate_owned" in projected.reason_codes


def test_ordinary_ambient_message_projects_current_candidate_mechanics(projector):
    projected = projector.project(example("今天天气还行", replied=False))
    assert projected.trigger == "candidate"
    assert projected.scene is InteractionScene.AMBIENT_CONTRIBUTION
    assert projected.owner in ("groupmate", "observe_only")
    assert projected.owner_count == 1


def test_reply_and_mentions_are_mapped_without_export_media_locators(projector):
    item = example("你觉得呢？")
    source = item.source.__class__(
        message_id=item.source.message_id,
        seq=item.source.seq,
        timestamp_ms=item.source.timestamp_ms,
        sender_key=item.source.sender_key,
        sender_uin=item.source.sender_uin,
        sender_name=item.source.sender_name,
        message_type=item.source.message_type,
        text=item.source.text,
        element_types=item.source.element_types,
        reply_to_message_id="old-target-message",
        reply_to_sender_uin="20002",
        mentions=("20002",),
        has_media=True,
    )
    item = item.__class__(
        item.sample_id, source, (source,), item.response_run, True, False, ""
    )
    topic = projector._topic(item)
    latest = topic.latest
    assert latest.reply_to_bot is True
    assert latest.mentions_bot is True
    assert latest.image_urls == ("shadow://media",)
    assert latest.sender_id.startswith("u-")
    assert "10001" not in latest.sender_id


def test_projector_source_excludes_effectful_workflow_and_provider_modules():
    import eval.shadow_projector as module

    source = inspect.getsource(module)
    for forbidden in (
        "CognitiveWorkflow",
        "eval.providers",
        "capability_executor",
        "delivery_queue",
        "memory_store",
    ):
        assert forbidden not in source
