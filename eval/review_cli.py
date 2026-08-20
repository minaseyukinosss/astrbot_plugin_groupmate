"""Resumable local human-review session for de-identified evaluation labels."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Callable, Mapping

from .review import SCENE_CATEGORIES, _read_jsonl
from .review_presentation import present_review_label
from .schema import EvaluationLabel


_PRIVATE_REVIEW_FIELDS = frozenset({"evidence_ref", "selection_signal"})


def _public_review_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _public_review_value(item)
            for key, item in value.items()
            if key not in _PRIVATE_REVIEW_FIELDS
        }
    if isinstance(value, list):
        return [_public_review_value(item) for item in value]
    return value


class ReviewSession:
    def __init__(
        self,
        queue_path: str | Path,
        suggestions_path: str | Path,
        *,
        decisions_path: str | Path,
        reviewer_id: str,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.queue = _read_jsonl(queue_path)
        self.suggestions = {
            str(item.get("scenario_id") or ""): item
            for item in _read_jsonl(suggestions_path)
        }
        self.decisions_path = Path(decisions_path)
        self.reviewer_id = str(reviewer_id or "").strip()
        self.clock = clock
        if not self.reviewer_id:
            raise ValueError("reviewer_id must not be empty")
        queue_ids = {str(item.get("scenario_id") or "") for item in self.queue}
        if "" in queue_ids or set(self.suggestions) != queue_ids:
            raise ValueError("review queue and suggestions do not match")
        existing = (
            _read_jsonl(self.decisions_path)
            if self.decisions_path.exists()
            else ()
        )
        self.decisions = {
            str(item.get("scenario_id") or ""): item for item in existing
        }
        if "" in self.decisions or not set(self.decisions) <= queue_ids:
            raise ValueError("saved review decisions do not match the queue")

    def next_pending(self) -> dict[str, object] | None:
        for record in self.queue:
            scenario_id = str(record["scenario_id"])
            if scenario_id in self.decisions:
                continue
            suggestion = dict(self.suggestions[scenario_id])
            label = suggestion.get("label")
            if not isinstance(label, Mapping):
                raise ValueError("suggestion label must be an object")
            context = list(record.get("context") or ())
            focus_event_id = str(record.get("focus_event_id") or "").strip()
            if (
                not focus_event_id
                or not context
                or not isinstance(context[-1], Mapping)
                or context[-1].get("event_id") != focus_event_id
            ):
                raise ValueError("review context must end with its focus event")
            suggestion["presentation"] = present_review_label(label)
            return _public_review_value({
                "scenario_id": scenario_id,
                "split": record.get("split"),
                "observable_tags": list(record.get("observable_tags") or ()),
                "focus_event_id": focus_event_id,
                "scene": record.get("scene") or {
                    "boundary": "legacy_fixed_window",
                    "history_event_count": len(context) - 1,
                    "max_context_events": len(context),
                    "max_idle_gap_ms": None,
                },
                "context": context,
                "suggestion": suggestion,
            })
        return None

    def progress(self) -> dict[str, int]:
        completed = len(self.decisions)
        insufficient = sum(
            decision.get("decision") == "insufficient_evidence"
            for decision in self.decisions.values()
        )
        total = len(self.queue)
        return {
            "completed": completed,
            "insufficient": insufficient,
            "remaining": total - completed,
            "total": total,
            "usable": completed - insufficient,
        }

    def approve_suggestion(self, scenario_id: str, *, confirmation: str) -> None:
        normalized_id = str(scenario_id or "").strip()
        if str(confirmation or "").strip() != normalized_id:
            raise ValueError("scenario confirmation does not match")
        self._require_current(normalized_id)
        suggestion = self.suggestions.get(normalized_id)
        if suggestion is None:
            raise KeyError(normalized_id)
        categories = suggestion.get("suggested_categories")
        if not isinstance(categories, list) or not categories:
            raise ValueError("suggestion has no reviewable scene category")
        label = suggestion.get("label")
        if not isinstance(label, Mapping):
            raise ValueError("suggestion label must be an object")
        validated = EvaluationLabel.from_dict(label)
        self.decisions[normalized_id] = {
            "scenario_id": normalized_id,
            "reviewer_id": self.reviewer_id,
            "reviewer_kind": "human",
            "decision": "approved",
            "reviewed_at": int(self.clock()),
            "categories": list(categories),
            "label": validated.to_dict(),
        }
        self._save()

    def record_correction(
        self,
        scenario_id: str,
        *,
        confirmation: str,
        categories: list[str],
        label: Mapping[str, object],
    ) -> None:
        normalized_id = str(scenario_id or "").strip()
        if str(confirmation or "").strip() != normalized_id:
            raise ValueError("scenario confirmation does not match")
        self._require_current(normalized_id)
        if normalized_id not in self.suggestions:
            raise KeyError(normalized_id)
        normalized_categories = [str(item or "").strip() for item in categories]
        if (
            not normalized_categories
            or len(normalized_categories) != len(set(normalized_categories))
            or any(item not in SCENE_CATEGORIES for item in normalized_categories)
        ):
            raise ValueError("correction categories are invalid")
        validated = EvaluationLabel.from_dict(label)
        self.decisions[normalized_id] = {
            "scenario_id": normalized_id,
            "reviewer_id": self.reviewer_id,
            "reviewer_kind": "human",
            "decision": "corrected",
            "reviewed_at": int(self.clock()),
            "categories": normalized_categories,
            "label": validated.to_dict(),
        }
        self._save()

    def record_insufficient_evidence(
        self,
        scenario_id: str,
        *,
        confirmation: str,
    ) -> None:
        normalized_id = str(scenario_id or "").strip()
        if str(confirmation or "").strip() != normalized_id:
            raise ValueError("scenario confirmation does not match")
        self._require_current(normalized_id)
        if normalized_id not in self.suggestions:
            raise KeyError(normalized_id)
        self.decisions[normalized_id] = {
            "scenario_id": normalized_id,
            "reviewer_id": self.reviewer_id,
            "reviewer_kind": "human",
            "decision": "insufficient_evidence",
            "reviewed_at": int(self.clock()),
            "reason": "scene_context_insufficient",
            "categories": [],
            "label": None,
        }
        self._save()

    def _require_current(self, scenario_id: str) -> None:
        pending = self.next_pending()
        if pending is None or pending.get("scenario_id") != scenario_id:
            raise ValueError("scenario is not the current pending review")

    def _save(self) -> None:
        self.decisions_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.decisions_path.with_name(
            f".{self.decisions_path.name}.tmp"
        )
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                for record in self.queue:
                    scenario_id = str(record["scenario_id"])
                    decision = self.decisions.get(scenario_id)
                    if decision is not None:
                        handle.write(
                            json.dumps(decision, ensure_ascii=False, sort_keys=True)
                            + "\n"
                        )
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.decisions_path)
            os.chmod(self.decisions_path, 0o600)
        finally:
            if temporary.exists():
                temporary.unlink()


__all__ = ("ReviewSession",)
