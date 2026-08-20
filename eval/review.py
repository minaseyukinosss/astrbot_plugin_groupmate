"""Governed suggestion and human-review materialization for evaluation labels."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from .schema import EvaluationLabel


SCENE_CATEGORIES = frozenset(
    {
        "direct_interaction",
        "multi_message_completion",
        "parallel_topics",
        "public_help",
        "riff",
        "care",
        "shared_experience",
        "media_reaction",
        "task_progress",
        "boundary",
        "sleep_wake",
        "autonomous_initiation",
        "opportunity_expiry",
        "task_topic_change",
        "ambiguous_target",
        "correct_silence",
        "external_plugin_compatibility",
    }
)
_PROTECTED_REVIEW_FIELDS = frozenset(
    {"chain_of_thought", "cot", "reasoning", "prompt", "model_identity"}
)


@dataclass(frozen=True)
class SuggestionSummary:
    suggestion_count: int
    output_path: Path


@dataclass(frozen=True)
class MaterializationSummary:
    calibration_count: int
    holdout_count: int
    calibration_path: Path
    holdout_path: Path


def _read_jsonl(path: str | Path) -> tuple[dict[str, object], ...]:
    source = Path(path)
    values = []
    with source.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"JSONL record must be an object at line {line_number}")
            values.append(value)
    return tuple(values)


def _write_jsonl(path: Path, values: Iterable[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for value in values:
            handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")


def _suggested_categories(record: Mapping[str, object]) -> list[str]:
    if record.get("evaluation_lane") == "EXTERNAL_PLUGIN_COMPATIBILITY":
        return ["external_plugin_compatibility"]
    tags = set(record.get("observable_tags") or ())
    categories = []
    if tags & {"direct_mention", "reply_context"}:
        categories.append("direct_interaction")
    if "media" in tags:
        categories.append("media_reaction")
    if record.get("selection_signal") == "historical_silence":
        categories.append("correct_silence")
    return categories


def _seed_label(record: Mapping[str, object]) -> EvaluationLabel:
    if record.get("evaluation_lane") == "EXTERNAL_PLUGIN_COMPATIBILITY":
        return EvaluationLabel.create(
            attention=False,
            action=False,
            target=None,
            acceptable_intents=(),
            unacceptable_intents=("duplicate_external_response",),
            modalities=(),
            sensitivity="group",
            expires_after_ms=0,
        )
    action = record.get("selection_signal") == "historical_bot_action"
    context = record.get("context")
    if not isinstance(context, list) or not context or not isinstance(context[-1], Mapping):
        raise ValueError("review queue context must contain its focus event")
    target = str(context[-1].get("actor_id") or "").strip() if action else None
    modalities = ["text"] if action else []
    if action and "media" in set(record.get("observable_tags") or ()):
        modalities.append("media")
    return EvaluationLabel.create(
        attention=True,
        action=action,
        target=target,
        acceptable_intents=("respond",) if action else (),
        unacceptable_intents=("interrupt", "misaddress"),
        modalities=modalities,
        sensitivity="group",
        expires_after_ms=60_000 if action else 0,
    )


def build_label_suggestions(
    review_queue_path: str | Path,
    *,
    output_path: str | Path,
) -> SuggestionSummary:
    """Create low-confidence seeds that always require a human decision."""

    suggestions = []
    for record in _read_jsonl(review_queue_path):
        if record.get("status") != "needs_human_review" or record.get("label") is not None:
            raise ValueError("review queue record is not awaiting human review")
        signal = record.get("selection_signal")
        if signal not in {"historical_bot_action", "historical_silence"}:
            raise ValueError("review queue selection signal is unsupported")
        scenario_id = str(record.get("scenario_id") or "").strip()
        if not scenario_id:
            raise ValueError("review scenario_id must not be empty")
        suggestions.append(
            {
                "scenario_id": scenario_id,
                "status": "suggestion",
                "reviewer_id": None,
                "reviewer_kind": None,
                "requires_human_review": True,
                "confidence": 0.2,
                "evaluation_lane": record.get(
                    "evaluation_lane", "SOCIAL_CONVERSATION"
                ),
                "source": {
                    "kind": "historical_sampling_signal",
                    "version": 1,
                },
                "suggested_categories": _suggested_categories(record),
                "label": _seed_label(record).to_dict(),
            }
        )
    output = Path(output_path)
    _write_jsonl(output, suggestions)
    return SuggestionSummary(len(suggestions), output)


def _review_decisions(
    values: tuple[dict[str, object], ...],
) -> dict[str, dict[str, object]]:
    decisions = {}
    for value in values:
        if _PROTECTED_REVIEW_FIELDS & value.keys():
            raise ValueError("review decision contains protected reasoning fields")
        scenario_id = str(value.get("scenario_id") or "").strip()
        if not scenario_id or scenario_id in decisions:
            raise ValueError("review decisions require unique scenario IDs")
        decisions[scenario_id] = value
    return decisions


def _human_label(value: Mapping[str, object]) -> tuple[list[str], EvaluationLabel]:
    if value.get("decision") == "insufficient_evidence":
        raise ValueError("replacement review scenario required for insufficient evidence")
    if value.get("reviewer_kind") != "human":
        raise ValueError("human review decision required")
    if value.get("decision") not in {"approved", "corrected"}:
        raise ValueError("human review decision required")
    reviewer_id = str(value.get("reviewer_id") or "").strip()
    if not reviewer_id:
        raise ValueError("human reviewer_id must not be empty")
    raw_categories = value.get("categories")
    if not isinstance(raw_categories, list) or not raw_categories:
        raise ValueError("human review categories must not be empty")
    categories = [str(item or "").strip() for item in raw_categories]
    if any(not item or item not in SCENE_CATEGORIES for item in categories):
        raise ValueError("human review category is unsupported")
    if len(categories) != len(set(categories)):
        raise ValueError("human review categories must not contain duplicates")
    raw_label = value.get("label")
    if not isinstance(raw_label, Mapping):
        raise ValueError("human review label must be an object")
    return categories, EvaluationLabel.from_dict(raw_label)


def _clean_context(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list) or not value:
        raise ValueError("review context must not be empty")
    result = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError("review context event must be an object")
        cleaned = dict(item)
        cleaned.pop("evidence_ref", None)
        result.append(cleaned)
    return result


def materialize_reviewed_corpora(
    review_queue_path: str | Path,
    decisions_path: str | Path,
    *,
    calibration_path: str | Path,
    holdout_path: str | Path,
    expected_per_split: int = 200,
) -> MaterializationSummary:
    """Write final corpora only from explicit human review decisions."""

    if type(expected_per_split) is not int or expected_per_split <= 0:
        raise ValueError("expected_per_split must be a positive integer")
    queue = _read_jsonl(review_queue_path)
    decisions = _review_decisions(_read_jsonl(decisions_path))
    output = {"calibration": [], "holdout": []}
    queue_ids = set()
    for record in queue:
        scenario_id = str(record.get("scenario_id") or "").strip()
        split = str(record.get("split") or "")
        if not scenario_id or scenario_id in queue_ids or split not in output:
            raise ValueError("review queue identity or split is invalid")
        queue_ids.add(scenario_id)
        decision = decisions.get(scenario_id)
        if decision is None:
            raise ValueError("human review decision required")
        categories, label = _human_label(decision)
        if record.get("evaluation_lane") == "EXTERNAL_PLUGIN_COMPATIBILITY":
            # Preserve the original human decision as audit evidence while
            # enforcing the later administrator-approved ownership boundary.
            categories = ["external_plugin_compatibility"]
            label = _seed_label(record)
        output[split].append(
            {
                "scenario_id": scenario_id,
                "split": split,
                "evaluation_lane": record.get(
                    "evaluation_lane", "SOCIAL_CONVERSATION"
                ),
                "core_social_eligible": bool(
                    record.get("core_social_eligible", True)
                ),
                "categories": categories,
                "group_id": record.get("group_id"),
                "focus_event_id": record.get("focus_event_id"),
                "context": _clean_context(record.get("context")),
                "label": label.to_dict(),
            }
        )
    if set(decisions) != queue_ids:
        raise ValueError("review decisions do not match the fixed queue")
    if any(len(values) != expected_per_split for values in output.values()):
        raise ValueError("reviewed corpus split size is incomplete")

    calibration = Path(calibration_path)
    holdout = Path(holdout_path)
    _write_jsonl(calibration, output["calibration"])
    _write_jsonl(holdout, output["holdout"])
    return MaterializationSummary(
        calibration_count=len(output["calibration"]),
        holdout_count=len(output["holdout"]),
        calibration_path=calibration,
        holdout_path=holdout,
    )


__all__ = (
    "MaterializationSummary",
    "SCENE_CATEGORIES",
    "SuggestionSummary",
    "build_label_suggestions",
    "materialize_reviewed_corpora",
)
