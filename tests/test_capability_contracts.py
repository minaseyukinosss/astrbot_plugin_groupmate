"""Value contracts for controlled capabilities."""

from dataclasses import FrozenInstanceError, fields

import pytest

from groupmate.capabilities.contracts import (
    CapabilityRequest,
    CapabilityResult,
    CapabilityStatus,
    MediaCandidate,
)


def test_capability_request_is_immutable_and_copies_input_sequences():
    facts = ["  source language: Chinese  ", "target\nlanguage: English"]
    media = ["https://example.test/image.png"]
    request = CapabilityRequest(
        capability_name="translator",
        message_text="  translate this  ",
        input_facts=facts,
        media_locators=media,
        group_id="group-1",
        actor_id="user-1",
        message_id="message-1",
    )

    facts.append("changed")
    media.append("changed")

    assert request.capability_name == "translator"
    assert request.message_text == "translate this"
    assert request.input_facts == (
        "source language: Chinese",
        "target language: English",
    )
    assert request.media_locators == ("https://example.test/image.png",)
    assert hash(request)
    with pytest.raises(FrozenInstanceError):
        request.message_text = "changed"


def test_capability_request_contains_only_data_not_privileged_dependencies():
    field_names = {field.name for field in fields(CapabilityRequest)}

    assert field_names == {
        "capability_name",
        "message_text",
        "input_facts",
        "media_locators",
        "group_id",
        "actor_id",
        "message_id",
    }
    assert not field_names.intersection(
        {"platform", "memory_store", "social_state", "workflow"}
    )


def test_media_candidate_is_safe_immutable_metadata():
    candidate = MediaCandidate(
        source="vision-provider",
        locator="asset://image/42",
        media_kind="image",
        semantic_label="source image",
        purpose="reply attachment",
        safety_label="reviewed",
    )

    assert candidate.locator == "asset://image/42"
    assert {field.name for field in fields(MediaCandidate)} == {
        "source",
        "locator",
        "media_kind",
        "semantic_label",
        "purpose",
        "safety_label",
    }
    with pytest.raises(FrozenInstanceError):
        candidate.locator = "changed"


def test_success_result_copies_facts_and_media_candidates():
    candidate = MediaCandidate(
        source="generated",
        locator="asset://preview/1",
        media_kind="image",
        semantic_label="preview",
        purpose="user review",
        safety_label="untrusted",
    )
    facts = ["translation completed"]
    media = [candidate]
    result = CapabilityResult(
        status=CapabilityStatus.SUCCESS,
        capability_name="translator",
        facts=facts,
        user_text="translation completed",
        media_candidates=media,
    )

    facts.append("changed")
    media.clear()

    assert result.facts == ("translation completed",)
    assert result.media_candidates == (candidate,)
    assert hash(result)


@pytest.mark.parametrize(
    "status",
    (
        CapabilityStatus.UNSUPPORTED,
        CapabilityStatus.FAILED,
        CapabilityStatus.HANDOFF,
        CapabilityStatus.TIMEOUT,
    ),
)
def test_non_success_result_rejects_completed_facts_and_media(status):
    candidate = MediaCandidate(
        source="generated",
        locator="asset://preview/1",
        media_kind="image",
        semantic_label="preview",
        purpose="user review",
        safety_label="untrusted",
    )

    with pytest.raises(ValueError, match="non-success"):
        CapabilityResult(
            status=status,
            capability_name="translator",
            facts=("task completed",),
        )
    with pytest.raises(ValueError, match="non-success"):
        CapabilityResult(
            status=status,
            capability_name="translator",
            media_candidates=(candidate,),
        )


def test_capability_names_are_explicit_stable_identifiers():
    with pytest.raises(ValueError, match="capability_name"):
        CapabilityRequest(capability_name="../../dynamic import")
