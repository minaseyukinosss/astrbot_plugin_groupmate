import json

import pytest

from eval.reference_labeler import (
    ReferenceLabeler,
    apply_overrides,
    collect_label_reviews,
    load_overrides,
)
from eval.shadow_models import (
    AssociationConfidence,
    BehaviorExample,
    ExportEvent,
    ReferenceLabel,
    ResponseRun,
)
from groupmate.core.response_act import ResponseAct
from groupmate.models import InteractionScene


def event(**overrides):
    values = {
        "message_id": "m1",
        "seq": 1,
        "timestamp_ms": 1000,
        "sender_key": "synthetic-sender",
        "sender_uin": "10001",
        "sender_name": "Synthetic User",
        "message_type": "text",
        "text": "测试消息",
        "element_types": ("text",),
    }
    values.update(overrides)
    return ExportEvent(**values)


def example(
    text, *, media=False, replied=True, response_text="收到",
    mentions_target=False,
):
    source = event(
        text=text,
        has_media=media,
        mentions=(("20002",) if mentions_target else ()),
    )
    run = None
    if replied:
        response = event(
            message_id="bot-1",
            sender_key="synthetic-target",
            sender_uin="20002",
            text=response_text,
            timestamp_ms=2000,
        )
        run = ResponseRun(
            "run-1",
            (response,),
            source.message_id,
            AssociationConfidence.HIGH,
            ("explicit_reply",),
        )
    return BehaviorExample(
        "sample-1", source, (source,), run, replied, False, ""
    )


@pytest.mark.parametrize(
    "text,scene,act",
    (
        ("小维", InteractionScene.DIRECT_ADDRESS, ResponseAct.ACKNOWLEDGE),
        ("小维，在吗", InteractionScene.DIRECT_ADDRESS, ResponseAct.ACKNOWLEDGE),
        ("小维，谢谢你", InteractionScene.SOCIAL_RESPONSE, ResponseAct.RECIPROCATE),
        ("小维，来比比", InteractionScene.DIRECT_ADDRESS, ResponseAct.PLAYFUL_REPLY),
        ("小维，叫你老婆行吗", InteractionScene.DIRECT_ADDRESS, ResponseAct.BOUNDARY),
        ("小维，你怎么看？", InteractionScene.DIRECT_ADDRESS, ResponseAct.ANSWER),
    ),
)
def test_high_confidence_reference_rules(text, scene, act):
    label = ReferenceLabeler("小维", "20002").label(example(text))
    assert label.scene is scene
    assert label.act is act
    assert label.confidence is AssociationConfidence.HIGH


def test_ambiguous_task_status_is_sent_to_review():
    label = ReferenceLabeler("小维", "20002").label(
        example("小维，帮我执行这个操作", response_text="我看看")
    )
    assert label.confidence is AssociationConfidence.REVIEW
    assert "task_status_ambiguous" in label.reason_codes


def test_visual_missing_object_and_explicit_unsupported_are_conservative():
    labeler = ReferenceLabeler("小维", "20002")
    visual = labeler.label(example("小维，看看这个", media=True))
    missing = labeler.label(
        example("小维，帮我翻译一下", response_text="要翻译哪一句？")
    )
    unsupported = labeler.label(
        example("小维，帮我导出这个", response_text="这个做不了")
    )
    assert visual.act is ResponseAct.VISUAL_REACTION
    assert missing.act is ResponseAct.CLARIFY
    assert unsupported.act is ResponseAct.TASK_UNSUPPORTED


def test_observed_silence_has_no_reference_act():
    label = ReferenceLabeler("小维", "20002").label(
        example("小维，在吗", replied=False)
    )
    assert label.scene is InteractionScene.DIRECT_ADDRESS
    assert label.act is None
    assert label.confidence is AssociationConfidence.HIGH
    assert "observed_silence" in label.reason_codes


def test_ordinary_undirected_silence_is_high_confidence_ambient():
    label = ReferenceLabeler("小维", "20002").label(
        example("今天群里挺热闹", replied=False)
    )
    assert label.scene is InteractionScene.AMBIENT_CONTRIBUTION
    assert label.act is None
    assert label.confidence is AssociationConfidence.HIGH
    assert label.reason_codes == ("ambient_observed_silence",)


def test_covered_association_is_not_ground_truth():
    item = example("小维，在吗", replied=False)
    item = BehaviorExample(
        item.sample_id, item.source, item.context, None, False, True,
        "multiple_source_candidates",
    )
    label = ReferenceLabeler("小维", "20002").label(item)
    assert label.confidence is AssociationConfidence.REVIEW
    assert label.act is None


def test_reply_to_target_and_native_mention_are_directed():
    labeler = ReferenceLabeler("小维", "20002")
    reply = example("你觉得呢？")
    reply = BehaviorExample(
        reply.sample_id,
        event(text="你觉得呢？", reply_to_sender_uin="20002"),
        reply.context,
        reply.response_run,
        True,
        False,
        "",
    )
    mentioned = labeler.label(example("谢谢", mentions_target=True))
    assert labeler.label(reply).act is ResponseAct.ANSWER
    assert mentioned.act is ResponseAct.RECIPROCATE


def test_overrides_validate_and_apply_high_confidence(tmp_path):
    path = tmp_path / "overrides.jsonl"
    path.write_text(
        json.dumps({
            "sample_id": "sample-1",
            "scene": "task_request",
            "act": "task_unsupported",
        }) + "\n",
        encoding="utf-8",
    )
    labels = {
        "sample-1": ReferenceLabeler("小维", "20002").label(example("未知"))
    }
    applied = apply_overrides(labels, load_overrides(path))
    assert applied["sample-1"] == ReferenceLabel(
        InteractionScene.TASK_REQUEST,
        ResponseAct.TASK_UNSUPPORTED,
        AssociationConfidence.HIGH,
        ("human_override",),
    )


@pytest.mark.parametrize(
    "rows,match",
    (
        ([{"sample_id": "sample-1", "scene": "bad", "act": None}], "scene"),
        ([{"sample_id": "sample-1", "scene": "direct_address", "act": "bad"}], "act"),
        ([{"sample_id": "sample-1", "scene": "direct_address", "act": None, "extra": 1}], "keys"),
        ([{"sample_id": "sample-1", "scene": "direct_address", "act": None}] * 2, "duplicate"),
    ),
)
def test_invalid_override_files_fail_closed(tmp_path, rows, match):
    path = tmp_path / "overrides.jsonl"
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    with pytest.raises(ValueError, match=match):
        load_overrides(path)


def test_override_rejects_unknown_sample_and_act_on_silence(tmp_path):
    labeler = ReferenceLabeler("小维", "20002")
    silent = labeler.label(example("小维", replied=False))
    with pytest.raises(ValueError, match="absent"):
        apply_overrides(
            {"sample-1": silent},
            load_overrides(_write_override(tmp_path, "unknown", None)),
        )
    with pytest.raises(ValueError, match="silence"):
        apply_overrides(
            {"sample-1": silent},
            load_overrides(_write_override(tmp_path, "sample-1", "answer")),
        )


def _write_override(tmp_path, sample_id, act):
    path = tmp_path / "{}.jsonl".format(sample_id)
    path.write_text(json.dumps({
        "sample_id": sample_id,
        "scene": "direct_address",
        "act": act,
    }) + "\n", encoding="utf-8")
    return path


def test_review_labels_become_local_review_items():
    item = example("小维，帮我执行这个操作", response_text="我看看")
    labels = {item.sample_id: ReferenceLabeler("小维", "20002").label(item)}
    reviews = collect_label_reviews((item,), labels)
    assert len(reviews) == 1
    assert reviews[0].sample_id == item.sample_id
    assert reviews[0].reason == "task_status_ambiguous"
    assert reviews[0].source_events == (item.source,)
    assert reviews[0].response_events == item.response_run.events
