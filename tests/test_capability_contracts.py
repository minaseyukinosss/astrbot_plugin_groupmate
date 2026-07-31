"""Value contracts for controlled capabilities."""

from dataclasses import FrozenInstanceError, fields

import pytest

from groupmate.capabilities.contracts import (
    CapabilityContext,
    CapabilityCostClass,
    CapabilityFailurePolicy,
    CapabilityLatencyClass,
    CapabilityManifest,
    CapabilityMediaPolicy,
    CapabilityPermission,
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
        media_id="asset-42",
        source="vision-provider",
        locator="asset://image/42",
        media_kind="image",
        semantic_label="source image",
        purpose="reply attachment",
        safety_label="reviewed",
    )

    assert candidate.locator == "asset://image/42"
    assert {field.name for field in fields(MediaCandidate)} == {
        "media_id",
        "source",
        "locator",
        "media_kind",
        "semantic_label",
        "purpose",
        "safety_label",
    }
    with pytest.raises(FrozenInstanceError):
        candidate.locator = "changed"


@pytest.mark.parametrize(
    ("purpose", "safety_label", "missing_field"),
    (
        ("", "reviewed", "purpose"),
        ("reply attachment", "", "safety_label"),
    ),
)
def test_media_candidate_requires_purpose_and_safety_label(
    purpose, safety_label, missing_field
):
    with pytest.raises(ValueError, match=missing_field):
        MediaCandidate(
            media_id="preview-1",
            source="generated",
            locator="asset://preview/1",
            media_kind="image",
            semantic_label="preview",
            purpose=purpose,
            safety_label=safety_label,
        )


def test_success_result_copies_facts_and_media_candidates():
    candidate = MediaCandidate(
        media_id="preview-1",
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
        media_id="preview-1",
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


def test_media_ids_are_stable_non_path_identifiers():
    with pytest.raises(ValueError, match="media_id"):
        MediaCandidate(
            media_id="../outside",
            source="generated",
            locator="/tmp/result.png",
            media_kind="image",
            semantic_label="result",
            purpose="task result",
            safety_label="provider_approved",
        )


def test_capability_manifest_is_immutable_and_declares_governance_fields():
    manifest = CapabilityManifest(
        name="vision",
        version="1.0.0",
        supported_intents=("image_understanding",),
        permission_profile=(CapabilityPermission.VISION_READ,),
        latency_class=CapabilityLatencyClass.INTERACTIVE,
        cost_class=CapabilityCostClass.METERED,
        failure_policy=CapabilityFailurePolicy.FAIL_CLOSED,
        max_result_size=512,
        default_timeout_seconds=3.5,
        max_concurrency=2,
    )

    assert manifest.name == "vision"
    assert manifest.version == "1.0.0"
    assert manifest.permission_profile == (CapabilityPermission.VISION_READ,)
    assert manifest.supported_intents == ("image_understanding",)
    assert manifest.max_result_size == 512
    assert manifest.default_timeout_seconds == 3.5
    assert manifest.max_concurrency == 2
    assert hash(manifest)

    with pytest.raises(FrozenInstanceError):
        manifest.name = "changed"


def test_capability_manifest_rejects_empty_permissions_and_bad_limits():
    with pytest.raises(ValueError, match="permission_profile"):
        CapabilityManifest(
            name="vision",
            version="1.0.0",
            permission_profile=(),
        )
    with pytest.raises(ValueError, match="max_result_size"):
        CapabilityManifest(
            name="vision",
            version="1.0.0",
            permission_profile=(CapabilityPermission.VISION_READ,),
            max_result_size=0,
        )
    with pytest.raises(ValueError, match="default_timeout_seconds"):
        CapabilityManifest(
            name="vision",
            version="1.0.0",
            permission_profile=(CapabilityPermission.VISION_READ,),
            default_timeout_seconds=0,
        )
    with pytest.raises(ValueError, match="max_concurrency"):
        CapabilityManifest(
            name="vision",
            version="1.0.0",
            permission_profile=(CapabilityPermission.VISION_READ,),
            max_concurrency=0,
        )


def test_capability_context_contains_only_safe_runtime_facts():
    context = CapabilityContext(
        persona_id="aemeath",
        group_id="g1",
        actor_id="u1",
        message_id="m1",
        trace_id="d1",
        deadline_at=123,
        allowed_permissions=(CapabilityPermission.VISION_READ,),
        media_policy=CapabilityMediaPolicy(capability_media_allowed=True),
    )
    field_names = {field.name for field in fields(CapabilityContext)}

    assert field_names == {
        "persona_id",
        "group_id",
        "actor_id",
        "message_id",
        "trace_id",
        "deadline_at",
        "allowed_permissions",
        "media_policy",
    }
    assert context.allowed_permissions == (CapabilityPermission.VISION_READ,)
    assert context.media_policy.capability_media_allowed is True
    assert not field_names.intersection(
        {
            "platform",
            "delivery_service",
            "memory",
            "memory_store",
            "workflow",
            "actor",
            "astrbot_context",
            "event",
        }
    )


def test_capability_media_policy_defaults_to_no_media():
    policy = CapabilityMediaPolicy()

    assert policy.capability_media_allowed is False
    assert policy.allowed_media_kinds == ()
    assert policy.allowed_safety_labels == ()
