from __future__ import annotations

from eval.metrics import collect_metrics
from eval.schema import EvaluationLabel


def _label(*, attention=True, action=True, target="member:001"):
    return EvaluationLabel.create(
        attention=attention,
        action=action,
        target=target,
        acceptable_intents=("respond",) if action else (),
        unacceptable_intents=("interrupt", "misaddress"),
        modalities=("text",) if action else (),
        sensitivity="group",
        expires_after_ms=60_000 if action else 0,
    ).to_dict()


def test_metrics_count_social_outcomes_without_deriving_truth_from_predictions():
    # A wrong action branch must change action/target PR, interrupt, and miss rate.
    records = (
        {
            "label": _label(),
            "prediction": {
                "attention": True,
                "action": True,
                "target": "member:001",
                "intent": "respond",
                "modalities": ("text",),
                "autonomous": True,
                "autonomy_value": 0.75,
                "expired": False,
                "decision_offset_ms": 75_000,
                "persona_ok": True,
                "relationship_ok": True,
                "culture_ok": True,
                "task_ok": True,
                "delivery_ok": True,
                "recovery_ok": True,
                "style_ok": True,
                "media_ok": True,
                "text": "我来看看",
            },
        },
        {
            "label": _label(action=False, target=None),
            "prediction": {
                "attention": True,
                "action": True,
                "target": "member:002",
                "intent": "interrupt",
                "modalities": ("text",),
                "text": "我来看看",
            },
        },
        {
            "label": _label(),
            "prediction": {"attention": True, "action": False, "target": None},
        },
        {
            "label": _label(target=None),
            "prediction": {
                "attention": True,
                "action": True,
                "target": None,
                "open_participation": True,
                "intent": "respond",
                "modalities": ("text",),
                "text": "换个话题也可以",
            },
        },
    )

    metrics = collect_metrics(records).to_dict()

    assert metrics["attention"] == {"tp": 4, "fp": 0, "fn": 0, "tn": 0, "support": 4, "precision": 1.0, "recall": 1.0}
    assert metrics["action"] == {"tp": 2, "fp": 1, "fn": 1, "tn": 0, "support": 4, "precision": 2 / 3, "recall": 2 / 3}
    assert metrics["target"] == {"tp": 1, "fp": 1, "fn": 1, "tn": 1, "support": 4, "precision": 0.5, "recall": 0.5}
    assert metrics["open_participation"]["precision"] == 1.0
    assert metrics["miss_rate"] == 1 / 3
    assert metrics["interrupt_rate"] == 1 / 3
    assert metrics["repetition_rate"] == 1 / 3
    assert metrics["target_concentration"] == 0.5
    assert metrics["autonomy"] == {"count": 0, "mean_value": None, "expiry_correct": None}
    assert metrics["quality"] == {
        "persona": None,
        "relationship": None,
        "culture": None,
        "task": None,
        "delivery": None,
        "recovery": None,
        "style": None,
        "media": None,
    }


def test_metrics_use_only_frozen_truth_and_keep_monopoly_separate_from_target_concentration():
    records = (
        {
            "label": _label(attention=False, action=False, target=None),
            "prediction": {"attention": False, "action": False, "target": None},
        },
        {
            "label": _label(),
            "prediction": {"attention": True, "action": True, "target": "member:001", "autonomous": True},
            "conversation": {"groupmate_action_count": 1, "member_action_count": 3},
            "frozen_truth": {"persona": True, "autonomy": True, "autonomy_value": 0.5},
        },
        {
            "label": _label(),
            "prediction": {"attention": True, "action": True, "target": "member:001"},
            "conversation": {"groupmate_action_count": 0, "member_action_count": 1},
        },
    )

    metrics = collect_metrics(records).to_dict()

    assert metrics["attention"]["tn"] == 1
    assert metrics["attention"]["support"] == 3
    assert metrics["target_concentration"] == 1.0
    assert metrics["monopoly_rate"] == 1 / 5
    assert metrics["quality"]["persona"] == 1.0
    assert metrics["autonomy"]["mean_value"] == 0.5


def test_autonomy_expiry_uses_frozen_truth_and_artifact_timestamps_not_candidate_claims():
    candidate_claim = {
        "label": _label(),
        "prediction": {
            "attention": True, "action": True, "target": "member:001",
            "autonomous": True, "decision_offset_ms": 1,
        },
    }
    frozen_artifact = {
        "label": _label(),
        "prediction": {"attention": True, "action": True, "target": "member:001"},
        "frozen_truth": {
            "autonomy": True,
            "autonomy_value": 0.5,
            "autonomy_evidence_event_id": "event:decision",
        },
        "artifacts": {
            "focus_event_id": "event:focus",
            "events": [
                {"event_id": "event:focus", "occurred_at_ms": 1_000},
                {"event_id": "event:decision", "occurred_at_ms": 61_001},
            ],
        },
    }

    metrics = collect_metrics((candidate_claim, frozen_artifact)).to_dict()

    assert metrics["autonomy"] == {"count": 1, "mean_value": 0.5, "expiry_correct": 0.0}


def test_autonomy_expiry_rejects_a_frozen_decision_before_its_focus_event():
    record = {
        "label": _label(),
        "prediction": {"attention": True, "action": True, "target": "member:001"},
        "frozen_truth": {"autonomy": True, "autonomy_evidence_event_id": "event:decision"},
        "artifacts": {
            "focus_event_id": "event:focus",
            "events": [
                {"event_id": "event:focus", "occurred_at_ms": 10_000},
                {"event_id": "event:decision", "occurred_at_ms": 9_000},
            ],
        },
    }

    assert collect_metrics((record,)).to_dict()["autonomy"]["expiry_correct"] == 0.0
