from dataclasses import FrozenInstanceError

import pytest

from eval.shadow_models import (
    AssociationConfidence,
    BehaviorExample,
    ExportEvent,
    ExportSummary,
    IngestResult,
    LocalReviewItem,
    ReferenceLabel,
    ResponseRun,
    ShadowProjection,
)
from groupmate.core.response_act import ResponseAct
from groupmate.models import InteractionScene
from tests.shadow_fixtures import write_export


def event(**overrides):
    values = {
        "message_id": "m1",
        "seq": 1,
        "timestamp_ms": 1000,
        "sender_key": "sender-a",
        "sender_uin": "10001",
        "sender_name": "Synthetic User",
        "message_type": "text",
        "text": "test",
        "element_types": ("text",),
    }
    values.update(overrides)
    return ExportEvent(**values)


def summary():
    return ExportSummary(
        manifest_records=3,
        observed_records=3,
        target_records=1,
        excluded_system=0,
        excluded_recalled=0,
        duplicate_records=0,
        chunk_count=1,
    )


def test_export_event_is_frozen_content_eligible_and_validates_identity():
    item = event()

    assert item.content_eligible is True
    with pytest.raises(FrozenInstanceError):
        item.text = "changed"
    with pytest.raises(ValueError, match="message_id is required"):
        event(message_id="")


def test_export_event_normalizes_sequences_and_rejects_invalid_values():
    element_types = [" text "]
    mentions = [" 10002 "]
    item = event(
        seq="2",
        timestamp_ms="2000",
        element_types=element_types,
        mentions=mentions,
    )
    element_types.clear()
    mentions.clear()

    assert item.seq == 2
    assert item.timestamp_ms == 2000
    assert item.element_types == ("text",)
    assert item.mentions == ("10002",)
    with pytest.raises(ValueError, match="timestamp_ms must be non-negative"):
        event(timestamp_ms=-1)
    with pytest.raises(TypeError, match="element_types must be a sequence"):
        event(element_types="text")
    with pytest.raises(TypeError, match="mentions must be a sequence"):
        event(mentions=b"10002")
    with pytest.raises(TypeError, match="element_types must contain strings"):
        event(element_types=(1,))


def test_export_event_content_eligibility_excludes_system_and_recalled_events():
    assert event(system=True).content_eligible is False
    assert event(recalled=True).content_eligible is False
    assert event(text="", element_types=()).content_eligible is False
    assert event(text="", element_types=(), has_media=True).content_eligible is True


def test_response_run_exposes_derived_reply_mechanics():
    run = ResponseRun(
        run_id="run-a",
        events=(
            event(text="test"),
            event(
                message_id="m2",
                text="ok",
                has_media=True,
                reply_to_message_id="m-source",
            ),
        ),
        anchor_message_id="m-source",
        confidence=AssociationConfidence.HIGH,
        reason_codes=("explicit_reply",),
    )

    assert run.message_count == 2
    assert run.reply_chars == 6
    assert run.has_media is True
    assert run.quoted is True

    empty = ResponseRun(
        run_id="run-empty",
        events=(),
        anchor_message_id="",
        confidence=AssociationConfidence.REVIEW,
        reason_codes=(),
    )
    assert empty.message_count == 0
    assert empty.reply_chars == 0
    assert empty.has_media is False
    assert empty.quoted is False


def test_reference_label_and_projection_retain_domain_and_invariant_fields():
    label = ReferenceLabel(
        scene=InteractionScene.DIRECT_ADDRESS,
        act=ResponseAct.ACKNOWLEDGE,
        confidence=AssociationConfidence.HIGH,
        reason_codes=("bare_alias",),
    )
    projection = ShadowProjection(
        sample_id="sample-a",
        owner="groupmate",
        would_reply=True,
        trigger="alias_direct",
        scene=InteractionScene.DIRECT_ADDRESS,
        act=ResponseAct.ACKNOWLEDGE,
        quote_allowed=True,
        decorative_media_allowed=False,
        capability_media_allowed=False,
        ambiguous_target=False,
        owner_count=1,
        completion_claim_allowed=False,
        reason_codes=("hard_trigger",),
    )

    assert label.scene is InteractionScene.DIRECT_ADDRESS
    assert label.act is ResponseAct.ACKNOWLEDGE
    assert label.confidence is AssociationConfidence.HIGH
    assert projection.scene is InteractionScene.DIRECT_ADDRESS
    assert projection.act is ResponseAct.ACKNOWLEDGE
    assert projection.owner == "groupmate"
    assert projection.owner_count == 1
    assert projection.completion_claim_allowed is False


def test_behavior_example_retains_in_memory_context_and_reply_state():
    source = event()
    example = BehaviorExample(
        sample_id="sample-a",
        source=source,
        context=(source,),
        response_run=None,
        observed_replied=False,
        covered_context=False,
        review_reason="",
    )

    assert example.source is source
    assert example.context == (source,)
    assert example.observed_replied is False


def test_tuple_contracts_copy_caller_sequences_and_retain_summary_data():
    source = event()
    event_values = [source]
    reason_values = ["synthetic_reason"]
    ingest = IngestResult(event_values, summary(), "20002")
    run = ResponseRun(
        "run-a",
        event_values,
        "m1",
        AssociationConfidence.HIGH,
        reason_values,
    )
    example = BehaviorExample(
        "sample-a", source, event_values, run, True, True, ""
    )
    review = LocalReviewItem(
        "sample-a", "synthetic_review", event_values, event_values
    )
    label = ReferenceLabel(
        InteractionScene.DIRECT_ADDRESS,
        ResponseAct.ACKNOWLEDGE,
        AssociationConfidence.HIGH,
        reason_values,
    )
    projection = ShadowProjection(
        "sample-a",
        "groupmate",
        True,
        "alias_direct",
        InteractionScene.DIRECT_ADDRESS,
        ResponseAct.ACKNOWLEDGE,
        True,
        False,
        False,
        False,
        1,
        False,
        reason_values,
    )
    event_values.clear()
    reason_values.clear()

    assert ingest.events == (source,)
    assert ingest.summary.manifest_records == 3
    assert run.events == (source,)
    assert run.reason_codes == ("synthetic_reason",)
    assert example.context == (source,)
    assert review.source_events == (source,)
    assert review.response_events == (source,)
    assert label.reason_codes == ("synthetic_reason",)
    assert projection.reason_codes == ("synthetic_reason",)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: IngestResult("events", summary(), "20002"),
        lambda: IngestResult(False, summary(), "20002"),
        lambda: ResponseRun(
            "run-a", "events", "m1", AssociationConfidence.HIGH, ()
        ),
        lambda: ResponseRun(
            "run-a", (), "m1", AssociationConfidence.HIGH, "reason"
        ),
        lambda: BehaviorExample(
            "sample-a", event(), "context", None, False, False, ""
        ),
        lambda: LocalReviewItem("sample-a", "reason", b"events", ()),
        lambda: LocalReviewItem("sample-a", "reason", (), "events"),
        lambda: ReferenceLabel(
            InteractionScene.DIRECT_ADDRESS,
            None,
            AssociationConfidence.HIGH,
            "reason",
        ),
        lambda: ShadowProjection(
            "sample-a",
            "groupmate",
            True,
            "alias_direct",
            InteractionScene.DIRECT_ADDRESS,
            None,
            True,
            False,
            False,
            False,
            1,
            False,
            b"reason",
        ),
    ],
)
def test_tuple_contracts_reject_scalar_strings_and_bytes(factory):
    with pytest.raises(TypeError, match="must be a sequence"):
        factory()


@pytest.mark.parametrize(
    "factory, message",
    [
        (lambda: IngestResult((object(),), summary(), "20002"), "ExportEvent"),
        (
            lambda: ResponseRun(
                "run-a", (object(),), "m1", AssociationConfidence.HIGH, ()
            ),
            "ExportEvent",
        ),
        (
            lambda: BehaviorExample(
                "sample-a", event(), (object(),), None, False, False, ""
            ),
            "ExportEvent",
        ),
        (
            lambda: LocalReviewItem("sample-a", "reason", (), (object(),)),
            "ExportEvent",
        ),
        (
            lambda: ReferenceLabel(
                InteractionScene.DIRECT_ADDRESS,
                None,
                AssociationConfidence.HIGH,
                (1,),
            ),
            "strings",
        ),
        (
            lambda: ShadowProjection(
                "sample-a",
                "groupmate",
                True,
                "alias_direct",
                InteractionScene.DIRECT_ADDRESS,
                None,
                True,
                False,
                False,
                False,
                1,
                False,
                (1,),
            ),
            "strings",
        ),
    ],
)
def test_tuple_contracts_validate_member_types(factory, message):
    with pytest.raises(TypeError, match=message):
        factory()


@pytest.mark.parametrize(
    "factory, field",
    [
        (
            lambda: ResponseRun("run-a", (), "", "high", ()),
            "confidence",
        ),
        (
            lambda: ReferenceLabel(
                "direct_address", None, AssociationConfidence.HIGH, ()
            ),
            "scene",
        ),
        (
            lambda: ReferenceLabel(
                InteractionScene.DIRECT_ADDRESS,
                "acknowledge",
                AssociationConfidence.HIGH,
                (),
            ),
            "act",
        ),
        (
            lambda: ReferenceLabel(
                InteractionScene.DIRECT_ADDRESS, None, "high", ()
            ),
            "confidence",
        ),
        (
            lambda: ShadowProjection(
                "sample-a",
                "groupmate",
                True,
                "alias_direct",
                "direct_address",
                None,
                True,
                False,
                False,
                False,
                1,
                False,
                (),
            ),
            "scene",
        ),
        (
            lambda: ShadowProjection(
                "sample-a",
                "groupmate",
                True,
                "alias_direct",
                InteractionScene.DIRECT_ADDRESS,
                "acknowledge",
                True,
                False,
                False,
                False,
                1,
                False,
                (),
            ),
            "act",
        ),
    ],
)
def test_contracts_require_domain_enum_instances(factory, field):
    with pytest.raises(TypeError, match=field):
        factory()


@pytest.mark.parametrize("chunk_size", [True, False, 0, -1, 1.5, "3", None])
def test_write_export_rejects_invalid_chunk_sizes(tmp_path, chunk_size):
    with pytest.raises(ValueError, match="chunk_size must be a positive integer"):
        write_export(tmp_path / "export", [], chunk_size=chunk_size)
