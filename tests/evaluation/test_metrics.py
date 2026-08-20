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

    assert metrics["attention"] == {"tp": 4, "fp": 0, "fn": 0, "precision": 1.0, "recall": 1.0}
    assert metrics["action"] == {"tp": 2, "fp": 1, "fn": 1, "precision": 2 / 3, "recall": 2 / 3}
    assert metrics["target"] == {"tp": 1, "fp": 1, "fn": 1, "precision": 0.5, "recall": 0.5}
    assert metrics["open_participation"]["precision"] == 1.0
    assert metrics["miss_rate"] == 1 / 3
    assert metrics["interrupt_rate"] == 1 / 3
    assert metrics["repetition_rate"] == 1 / 3
    assert metrics["target_concentration"] == 0.5
    assert metrics["autonomy"] == {"count": 1, "mean_value": 0.75, "expiry_correct": 1.0}
    assert metrics["quality"] == {
        "persona": 1.0,
        "relationship": 1.0,
        "culture": 1.0,
        "task": 1.0,
        "delivery": 1.0,
        "recovery": 1.0,
        "style": 1.0,
        "media": 1.0,
    }
