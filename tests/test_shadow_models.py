from dataclasses import FrozenInstanceError

import pytest

from eval.shadow_models import (
    AssociationConfidence,
    BehaviorExample,
    ExportEvent,
    ReferenceLabel,
    ResponseRun,
    ShadowProjection,
)
from groupmate.core.response_act import ResponseAct
from groupmate.models import InteractionScene


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


def test_export_event_is_frozen_content_eligible_and_validates_identity():
    item = event()

    assert item.content_eligible is True
    with pytest.raises(FrozenInstanceError):
        item.text = "changed"
    with pytest.raises(ValueError, match="message_id is required"):
        event(message_id="")


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
