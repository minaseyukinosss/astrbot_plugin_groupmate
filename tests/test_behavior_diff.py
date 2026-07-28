import json

import pytest

from eval.behavior_diff import (
    PrivacyViolation,
    assert_shareable_report,
    build_diff_report,
    render_markdown,
    write_json_report,
    write_markdown_report,
)
from eval.shadow_models import (
    AssociationConfidence,
    ExportSummary,
    ReferenceLabel,
    ShadowProjection,
)
from groupmate.core.response_act import ResponseAct
from groupmate.models import InteractionScene
from tests.test_reference_labeler import example


def label(scene, act):
    return ReferenceLabel(scene, act, AssociationConfidence.HIGH, ("test",))


def projection(
    sample_id, *, reply=True, scene=InteractionScene.DIRECT_ADDRESS,
    act=ResponseAct.ACKNOWLEDGE, quote=True, decorative=False,
    capability=False, ambiguous=False, owner_count=1, completion=False,
):
    return ShadowProjection(
        sample_id, "groupmate", reply, "alias_direct", scene, act,
        quote, decorative, capability, ambiguous, owner_count, completion,
        ("test",),
    )


def _sample(item, sample_id, replied=True):
    return item.__class__(
        sample_id,
        item.source,
        item.context,
        (item.response_run if replied else None),
        replied,
        False,
        "",
    )


def test_report_groups_conditional_mismatches_and_invariants():
    first = _sample(example("小维"), "sample-a")
    second = _sample(example("小维，谢谢你"), "sample-b")
    boundary = _sample(example("小维，叫你老婆行吗"), "sample-c")
    report = build_diff_report(
        ExportSummary(3, 3, 2, 0, 0, 0, 1),
        (first, second, boundary),
        {
            "sample-a": label(
                InteractionScene.DIRECT_ADDRESS, ResponseAct.ACKNOWLEDGE
            ),
            "sample-b": label(
                InteractionScene.SOCIAL_RESPONSE, ResponseAct.RECIPROCATE
            ),
            "sample-c": label(
                InteractionScene.DIRECT_ADDRESS, ResponseAct.BOUNDARY
            ),
        },
        {
            "sample-a": projection("sample-a"),
            "sample-b": projection(
                "sample-b", reply=False,
                scene=InteractionScene.DIRECT_ADDRESS, act=None, quote=False,
            ),
            "sample-c": projection(
                "sample-c", act=ResponseAct.BOUNDARY, decorative=True,
                ambiguous=True, owner_count=2, completion=True,
            ),
        },
        review_count=1,
        configuration={"pipeline_version": "phase3-v1"},
    )

    assert report["reply_confusion"]["target_reply_projected_silence"] == 1
    assert report["scene_confusion"]["social_response"]["direct_address"] == 1
    assert report["mismatches"]["reply"] == ["sample-b"]
    assert report["violations"] == {
        "ambiguous_media": 1,
        "boundary_media": 1,
        "false_completion_eligibility": 1,
        "multiple_owner": 1,
    }
    assert report["mismatches"]["boundary_media"] == ["sample-c"]
    assert report["quote"]["by_scene"]["direct_address"] == {
        "target_unquoted_projected_quote": 2,
    }
    assert report["media"]["by_scene_act"]["direct_address"][
        "acknowledge"
    ]["target_text_only_projected_text_only"] == 1
    assert report["media"]["by_scene_act"]["direct_address"][
        "boundary"
    ]["target_text_only_projected_media"] == 1
    assert "runtime_probability" not in json.dumps(report)


def test_act_quote_and_media_compare_only_when_both_reply():
    first = _sample(example("小维"), "sample-a")
    silent = _sample(example("小维", replied=False), "sample-b", replied=False)
    report = build_diff_report(
        ExportSummary(2, 2, 1, 0, 0, 0, 1),
        (first, silent),
        {
            "sample-a": label(
                InteractionScene.DIRECT_ADDRESS, ResponseAct.ACKNOWLEDGE
            ),
            "sample-b": label(InteractionScene.DIRECT_ADDRESS, None),
        },
        {
            "sample-a": projection(
                "sample-a", act=ResponseAct.ANSWER, quote=True,
                decorative=True,
            ),
            "sample-b": projection("sample-b", reply=True),
        },
        review_count=0,
        configuration={"pipeline_version": "phase3-v1"},
    )
    assert report["mismatches"]["act"] == ["sample-a"]
    assert report["mismatches"]["quote"] == ["sample-a"]
    assert report["mismatches"]["media"] == ["sample-a"]
    assert "sample-b" not in report["mismatches"]["act"]


def test_run_diagnostics_use_fixed_buckets():
    item = example("小维", response_text="x" * 25)
    item = _sample(item, "sample-a")
    report = build_diff_report(
        ExportSummary(1, 1, 1, 0, 0, 0, 1),
        (item,),
        {"sample-a": label(InteractionScene.DIRECT_ADDRESS, ResponseAct.ACKNOWLEDGE)},
        {"sample-a": projection("sample-a")},
        review_count=0,
        configuration={"pipeline_version": "phase3-v1"},
    )
    assert report["run_diagnostics"]["message_count"]["1"] == 1
    assert report["run_diagnostics"]["reply_chars"]["21-60"] == 1
    assert report["run_diagnostics"]["latency"]["0-2s"] == 1


def test_shareable_report_rejects_sensitive_keys_values_and_media():
    with pytest.raises(PrivacyViolation):
        assert_shareable_report(
            {"target_uin": "900000001"}, ("900000001",), ()
        )
    with pytest.raises(PrivacyViolation):
        assert_shareable_report(
            {"summary": "原始聊天长句泄漏"}, (), ("原始聊天长句泄漏",)
        )
    with pytest.raises(PrivacyViolation):
        assert_shareable_report(
            {"summary": "https://private.invalid/image.png"}, (), ()
        )
    with pytest.raises(PrivacyViolation):
        assert_shareable_report(
            {"safe": "synthetic-user-id"}, ("synthetic-user-id",), ()
        )


def test_shareable_report_does_not_treat_schema_keys_as_export_values():
    assert_shareable_report({"1": 3}, ("1",), ())
    with pytest.raises(PrivacyViolation):
        assert_shareable_report({"bucket": "1"}, ("1",), ())


def test_json_and_markdown_are_deterministic(tmp_path):
    report = {
        "schema_version": 1,
        "configuration": {"pipeline_version": "phase3-v1"},
        "counts": {"examples": 2},
        "reply_confusion": {"target_reply_projected_silence": 1},
        "scene_confusion": {},
        "act_confusion": {},
        "quote": {},
        "media": {},
        "run_diagnostics": {},
        "violations": {},
        "mismatches": {"reply": ["sample-b"]},
    }
    json_path = tmp_path / "report.json"
    markdown_path = tmp_path / "report.md"
    write_json_report(report, json_path)
    first = json_path.read_text(encoding="utf-8")
    write_json_report(report, json_path)
    write_markdown_report(report, markdown_path)
    assert json_path.read_text(encoding="utf-8") == first
    assert render_markdown(report) == markdown_path.read_text(encoding="utf-8")
    assert "sample-b" in render_markdown(report)
