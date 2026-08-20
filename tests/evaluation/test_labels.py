from __future__ import annotations

import json
import stat

import pytest

from eval.review import build_label_suggestions, materialize_reviewed_corpora
from eval.ownership import ReferenceTriggerPolicy, annotate_review_queue
from eval.review_cli import ReviewSession
from eval.schema import EvaluationLabel


def test_label_round_trips_the_phase_e_contract():
    label = EvaluationLabel.create(
        attention=True,
        action=True,
        target="member:42",
        acceptable_intents=("answer", "support"),
        unacceptable_intents=("interrupt",),
        modalities=("text", "media"),
        sensitivity="group",
        expires_after_ms=30_000,
    )

    assert EvaluationLabel.from_dict(label.to_dict()) == label
    assert label.to_dict() == {
        "attention": True,
        "action": True,
        "target": "member:42",
        "acceptable_intents": ["answer", "support"],
        "unacceptable_intents": ["interrupt"],
        "modalities": ["text", "media"],
        "sensitivity": "group",
        "expires_after_ms": 30_000,
    }


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"attention": "yes"}, "attention must be a boolean"),
        ({"action": 1}, "action must be a boolean"),
        ({"expires_after_ms": -1}, "expires_after_ms must not be negative"),
        (
            {"acceptable_intents": ("answer",), "unacceptable_intents": ("answer",)},
            "acceptable and unacceptable intents must be disjoint",
        ),
        ({"modalities": ("text", "text")}, "modalities must not contain duplicates"),
        ({"sensitivity": "  "}, "sensitivity must not be empty"),
    ],
)
def test_label_rejects_ambiguous_or_invalid_ground_truth(override, message):
    values = {
        "attention": False,
        "action": False,
        "target": None,
        "acceptable_intents": (),
        "unacceptable_intents": (),
        "modalities": (),
        "sensitivity": "group",
        "expires_after_ms": 0,
    }
    values.update(override)

    with pytest.raises(ValueError, match=message):
        EvaluationLabel.create(**values)


def _queue_record(scenario_id, split, signal, actor_id="member:001"):
    return {
        "scenario_id": scenario_id,
        "split": split,
        "status": "needs_human_review",
        "selection_signal": signal,
        "observable_tags": ["text"],
        "group_id": "group:000001",
        "focus_event_id": f"message:{scenario_id}",
        "context": [
            {
                "event_id": f"message:{scenario_id}",
                "group_id": "group:000001",
                "actor_id": actor_id,
                "text": "去标识上下文",
                "evidence_ref": f"evidence:{scenario_id}",
            }
        ],
        "label": None,
        "evaluation_lane": "SOCIAL_CONVERSATION",
        "core_social_eligible": True,
    }


def _write_jsonl(path, values):
    path.write_text(
        "".join(json.dumps(value, ensure_ascii=False) + "\n" for value in values),
        encoding="utf-8",
    )


def _human_decision(scenario_id, *, action):
    return {
        "scenario_id": scenario_id,
        "reviewer_id": "admin:local",
        "reviewer_kind": "human",
        "decision": "approved",
        "categories": ["direct_interaction" if action else "correct_silence"],
        "label": {
            "attention": True,
            "action": action,
            "target": "member:001" if action else None,
            "acceptable_intents": ["answer"] if action else [],
            "unacceptable_intents": ["interrupt"],
            "modalities": ["text"] if action else [],
            "sensitivity": "group",
            "expires_after_ms": 30_000 if action else 0,
        },
    }


def test_seed_suggestions_are_low_confidence_and_not_human_truth(tmp_path):
    queue = tmp_path / "queue.jsonl"
    output = tmp_path / "suggestions.jsonl"
    _write_jsonl(
        queue,
        [
            _queue_record(
                "001", "calibration", "historical_bot_action"
            ),
            _queue_record("002", "holdout", "historical_silence"),
        ],
    )

    summary = build_label_suggestions(queue, output_path=output)

    suggestions = [json.loads(line) for line in output.read_text().splitlines()]
    assert summary.suggestion_count == 2
    assert all(item["status"] == "suggestion" for item in suggestions)
    assert all(item["reviewer_kind"] is None for item in suggestions)
    assert all(item["requires_human_review"] is True for item in suggestions)
    assert all(item["confidence"] <= 0.25 for item in suggestions)
    assert all(
        item["source"] == {"kind": "historical_sampling_signal", "version": 1}
        for item in suggestions
    )
    assert suggestions[0]["label"]["action"] is True
    assert suggestions[1]["label"]["action"] is False


def test_reference_annotation_routes_external_focus_without_requiring_re_review(
    tmp_path,
):
    queue = tmp_path / "queue.jsonl"
    record = _queue_record("001", "calibration", "historical_bot_action")
    record["context"][0]["text"] = "xw帮助"
    _write_jsonl(queue, [record])

    summary = annotate_review_queue(
        queue,
        policy=ReferenceTriggerPolicy.create(
            command_prefixes={"xw": "reference:waves"}
        ),
    )

    annotated = json.loads(queue.read_text())
    assert summary.total_count == 1
    assert summary.social_count == 0
    assert summary.compatibility_count == 1
    assert annotated["evaluation_lane"] == "EXTERNAL_PLUGIN_COMPATIBILITY"
    assert annotated["core_social_eligible"] is False
    assert annotated["context"][0]["reference_interaction_origin"] == (
        "REFERENCE_EXTERNAL_TRIGGER"
    )
    assert annotated["context"][0]["reference_capability_hint"] == (
        "reference:waves"
    )
    assert "does_not_imply_target_installation" in annotated["ownership_note"]


def test_external_compatibility_suggestion_requires_groupmate_silence(tmp_path):
    queue = tmp_path / "queue.jsonl"
    output = tmp_path / "suggestions.jsonl"
    record = _queue_record("001", "calibration", "historical_bot_action")
    record.update(
        {
            "evaluation_lane": "EXTERNAL_PLUGIN_COMPATIBILITY",
            "core_social_eligible": False,
        }
    )
    _write_jsonl(queue, [record])

    build_label_suggestions(queue, output_path=output)

    suggestion = json.loads(output.read_text())
    assert suggestion["evaluation_lane"] == "EXTERNAL_PLUGIN_COMPATIBILITY"
    assert suggestion["suggested_categories"] == [
        "external_plugin_compatibility"
    ]
    assert suggestion["label"] == {
        "attention": False,
        "action": False,
        "target": None,
        "acceptable_intents": [],
        "unacceptable_intents": ["duplicate_external_response"],
        "modalities": [],
        "sensitivity": "group",
        "expires_after_ms": 0,
    }


def test_materializer_rejects_suggestions_without_human_decisions(tmp_path):
    queue = tmp_path / "queue.jsonl"
    suggestions = tmp_path / "suggestions.jsonl"
    _write_jsonl(
        queue,
        [
            _queue_record("001", "calibration", "historical_bot_action"),
            _queue_record("002", "holdout", "historical_silence"),
        ],
    )
    build_label_suggestions(queue, output_path=suggestions)

    with pytest.raises(ValueError, match="human review decision required"):
        materialize_reviewed_corpora(
            queue,
            suggestions,
            calibration_path=tmp_path / "calibration.jsonl",
            holdout_path=tmp_path / "holdout.jsonl",
            expected_per_split=1,
        )


def test_human_decisions_materialize_clean_split_corpora(tmp_path):
    queue = tmp_path / "queue.jsonl"
    decisions = tmp_path / "decisions.jsonl"
    calibration = tmp_path / "calibration.jsonl"
    holdout = tmp_path / "holdout.jsonl"
    _write_jsonl(
        queue,
        [
            _queue_record("001", "calibration", "historical_bot_action"),
            _queue_record("002", "holdout", "historical_silence"),
        ],
    )
    _write_jsonl(
        decisions,
        [
            _human_decision("001", action=True),
            _human_decision("002", action=False),
        ],
    )

    summary = materialize_reviewed_corpora(
        queue,
        decisions,
        calibration_path=calibration,
        holdout_path=holdout,
        expected_per_split=1,
    )

    calibration_value = json.loads(calibration.read_text())
    holdout_value = json.loads(holdout.read_text())
    assert summary.calibration_count == 1
    assert summary.holdout_count == 1
    assert calibration_value["label"]["action"] is True
    assert holdout_value["label"]["action"] is False
    for value in (calibration_value, holdout_value):
        assert value["evaluation_lane"] == "SOCIAL_CONVERSATION"
        assert "selection_signal" not in value
        assert "status" not in value
        assert "evidence_ref" not in value["context"][0]


def test_materializer_preserves_old_review_but_enforces_external_lane_ownership(
    tmp_path,
):
    queue = tmp_path / "queue.jsonl"
    decisions = tmp_path / "decisions.jsonl"
    calibration = tmp_path / "calibration.jsonl"
    holdout = tmp_path / "holdout.jsonl"
    social = _queue_record("001", "calibration", "historical_silence")
    external = _queue_record("002", "holdout", "historical_bot_action")
    external.update(
        {
            "evaluation_lane": "EXTERNAL_PLUGIN_COMPATIBILITY",
            "core_social_eligible": False,
        }
    )
    _write_jsonl(queue, [social, external])
    _write_jsonl(
        decisions,
        [
            _human_decision("001", action=False),
            # This old decision predates the ownership ruling. It remains in
            # the audit file but can no longer make Groupmate answer a command.
            _human_decision("002", action=True),
        ],
    )

    materialize_reviewed_corpora(
        queue,
        decisions,
        calibration_path=calibration,
        holdout_path=holdout,
        expected_per_split=1,
    )

    external_value = json.loads(holdout.read_text())
    assert external_value["evaluation_lane"] == (
        "EXTERNAL_PLUGIN_COMPATIBILITY"
    )
    assert external_value["core_social_eligible"] is False
    assert external_value["categories"] == ["external_plugin_compatibility"]
    assert external_value["label"]["attention"] is False
    assert external_value["label"]["action"] is False


def test_review_session_requires_exact_confirmation_and_resumes(tmp_path):
    queue = tmp_path / "queue.jsonl"
    suggestions = tmp_path / "suggestions.jsonl"
    decisions = tmp_path / "private" / "decisions.jsonl"
    record = _queue_record("001", "calibration", "historical_bot_action")
    record["observable_tags"] = ["text", "direct_mention"]
    _write_jsonl(queue, [record])
    build_label_suggestions(queue, output_path=suggestions)
    session = ReviewSession(
        queue,
        suggestions,
        decisions_path=decisions,
        reviewer_id="admin:local",
    )

    item = session.next_pending()

    assert item is not None
    assert item["scenario_id"] == "001"
    assert item["focus_event_id"] == "message:001"
    assert "selection_signal" not in item
    assert "evidence_ref" not in json.dumps(item, ensure_ascii=False)
    with pytest.raises(ValueError, match="scenario confirmation does not match"):
        session.approve_suggestion("001", confirmation="wrong")
    session.approve_suggestion("001", confirmation="001")

    restarted = ReviewSession(
        queue,
        suggestions,
        decisions_path=decisions,
        reviewer_id="admin:local",
    )
    decision = json.loads(decisions.read_text())
    assert restarted.next_pending() is None
    assert decision["reviewer_kind"] == "human"
    assert decision["decision"] == "approved"
    assert decision["categories"] == ["direct_interaction"]
    assert stat.S_IMODE(decisions.stat().st_mode) == 0o600


def test_review_session_records_an_explicit_human_correction(tmp_path):
    queue = tmp_path / "queue.jsonl"
    suggestions = tmp_path / "suggestions.jsonl"
    decisions = tmp_path / "private" / "decisions.jsonl"
    _write_jsonl(
        queue,
        [_queue_record("001", "calibration", "historical_silence")],
    )
    build_label_suggestions(queue, output_path=suggestions)
    session = ReviewSession(
        queue,
        suggestions,
        decisions_path=decisions,
        reviewer_id="admin:local",
    )
    corrected = _human_decision("001", action=True)["label"]

    session.record_correction(
        "001",
        confirmation="001",
        categories=["public_help"],
        label=corrected,
    )

    decision = json.loads(decisions.read_text())
    assert decision["decision"] == "corrected"
    assert decision["categories"] == ["public_help"]
    assert decision["label"]["action"] is True


def test_review_session_records_insufficient_evidence_without_inventing_a_label(
    tmp_path,
):
    queue = tmp_path / "queue.jsonl"
    suggestions = tmp_path / "suggestions.jsonl"
    decisions = tmp_path / "private" / "decisions.jsonl"
    _write_jsonl(
        queue,
        [_queue_record("001", "calibration", "historical_silence")],
    )
    build_label_suggestions(queue, output_path=suggestions)
    session = ReviewSession(
        queue,
        suggestions,
        decisions_path=decisions,
        reviewer_id="admin:local",
        clock=lambda: 1_723_456_789,
    )

    with pytest.raises(ValueError, match="scenario confirmation does not match"):
        session.record_insufficient_evidence("001", confirmation="wrong")
    session.record_insufficient_evidence("001", confirmation="001")

    decision = json.loads(decisions.read_text())
    assert decision == {
        "categories": [],
        "decision": "insufficient_evidence",
        "label": None,
        "reason": "scene_context_insufficient",
        "reviewed_at": 1_723_456_789,
        "reviewer_id": "admin:local",
        "reviewer_kind": "human",
        "scenario_id": "001",
    }
    assert session.progress() == {
        "completed": 1,
        "insufficient": 1,
        "remaining": 0,
        "total": 1,
        "usable": 0,
    }
    assert session.next_pending() is None
    assert stat.S_IMODE(decisions.stat().st_mode) == 0o600


def test_materializer_requires_replacement_for_insufficient_evidence(tmp_path):
    queue = tmp_path / "queue.jsonl"
    decisions = tmp_path / "decisions.jsonl"
    _write_jsonl(
        queue,
        [_queue_record("001", "calibration", "historical_silence")],
    )
    _write_jsonl(
        decisions,
        [
            {
                "scenario_id": "001",
                "reviewer_id": "admin:local",
                "reviewer_kind": "human",
                "decision": "insufficient_evidence",
                "categories": [],
                "label": None,
                "reason": "scene_context_insufficient",
            }
        ],
    )

    with pytest.raises(ValueError, match="replacement review scenario required"):
        materialize_reviewed_corpora(
            queue,
            decisions,
            calibration_path=tmp_path / "calibration.jsonl",
            holdout_path=tmp_path / "holdout.jsonl",
            expected_per_split=1,
        )
