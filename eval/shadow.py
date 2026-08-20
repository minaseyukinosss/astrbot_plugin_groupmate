"""Installed SHADOW capture, human review, freezing, and calibration governance.

This module consumes results from the authoritative SocialRuntimeManager.  It does
not create a runtime, open a delivery transport, or execute a capability.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping

if "." in (__package__ or ""):
    from ..groupmate.social_runtime.contracts import SocialEventEnvelope
    from ..groupmate.social_runtime.control.config_versions import (
        ConfigVersionRepository,
    )
    from ..groupmate.social_runtime.persistence.schema import (
        connect_database,
        initialize_database,
    )
else:  # Repository-local offline evaluation entry point.
    from groupmate.social_runtime.contracts import SocialEventEnvelope
    from groupmate.social_runtime.control.config_versions import (
        ConfigVersionRepository,
    )
    from groupmate.social_runtime.persistence.schema import (
        connect_database,
        initialize_database,
    )

from .runner import EvaluationRunner, frozen_artifact_digest
from .schema import EvaluationLabel


SCENE_CATEGORIES = (
    "direct_interaction",
    "consecutive_messages",
    "parallel_topics",
    "public_help",
    "humor",
    "care",
    "shared_experience",
    "media_reaction",
    "task_progress",
    "boundary",
    "sleep_wake",
    "autonomous_initiation",
    "expired_opportunity",
    "task_topic_change",
    "ambiguous_target",
    "correct_silence",
)
_SCENE_CATEGORY_SET = frozenset(SCENE_CATEGORIES)
_REVIEW_DECISIONS = frozenset({"reasonable", "unreasonable", "insufficient"})
_CALIBRATABLE_FIELDS = frozenset(
    {
        "attention_window_ms",
        "reply_length_tendency",
        "media_preference",
        "participation_weights",
    }
)
_URL = re.compile(r"https?://\S+", re.IGNORECASE)
_LONG_NUMBER = re.compile(r"(?<![A-Za-z0-9])\d{5,}(?![A-Za-z0-9])")
_PROTECTED = re.compile(
    r"chain[_ -]?of[_ -]?thought|system[_ -]?prompt|\bprompt\b|api[_ -]?key|auth[_ -]?code",
    re.IGNORECASE,
)


class FrozenHoldoutError(ValueError):
    """Raised before a frozen holdout label can be changed."""


class ShadowCalibrationRejected(ValueError):
    """Raised before a rejected or stale calibration can create a config."""


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _required_text(value: object, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{label} must not be empty")
    return normalized


def _safe_summary(value: object, *, limit: int = 240) -> str:
    text = str(value or "").strip()
    text = _URL.sub("[link]", text)
    text = _LONG_NUMBER.sub("[number]", text)
    text = _PROTECTED.sub("[protected]", text)
    return text[:limit]


def _alias(value: object, prefix: str = "member") -> str | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    digest = hashlib.sha256(normalized.encode()).hexdigest()[:10]
    return f"{prefix}:{digest}"


def _mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return json.loads(_canonical(dict(value)))


def _sequence_of_mappings(value: object, label: str) -> tuple[dict[str, object], ...]:
    if not isinstance(value, (tuple, list)):
        raise ValueError(f"{label} must be a sequence")
    return tuple(_mapping(item, label) for item in value)


@dataclass(frozen=True)
class ShadowDecisionCapture:
    persona_id: str
    group_id: str
    frame_id: str
    source_event_id: str
    correlation_id: str
    occurred_at: int
    config_version: int
    history: tuple[dict[str, object], ...]
    focus: dict[str, object]
    attention: dict[str, object]
    target: str | None
    candidate_response: str | None
    candidate_actions: tuple[dict[str, object], ...]
    governor: dict[str, object]
    expires_at: int
    prediction: dict[str, object]
    suggested_categories: tuple[str, ...]
    evaluation_lane: str
    ownership: str
    installed: bool
    runtime_mode: str

    @classmethod
    def create(cls, **values: object) -> "ShadowDecisionCapture":
        normalized = dict(values)
        for name in (
            "persona_id",
            "group_id",
            "frame_id",
            "source_event_id",
            "correlation_id",
        ):
            normalized[name] = _required_text(normalized.get(name), name)
        for name in ("occurred_at", "config_version", "expires_at"):
            value = normalized.get(name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        normalized["history"] = _sequence_of_mappings(
            normalized.get("history", ()), "history"
        )
        normalized["focus"] = _mapping(normalized.get("focus"), "focus")
        normalized["attention"] = _mapping(
            normalized.get("attention"), "attention"
        )
        normalized["candidate_actions"] = _sequence_of_mappings(
            normalized.get("candidate_actions", ()), "candidate_actions"
        )
        normalized["governor"] = _mapping(
            normalized.get("governor"), "governor"
        )
        normalized["prediction"] = _mapping(
            normalized.get("prediction"), "prediction"
        )
        categories = tuple(
            _required_text(value, "suggested category")
            for value in normalized.get("suggested_categories", ())
        )
        unknown = sorted(set(categories) - _SCENE_CATEGORY_SET)
        if unknown:
            raise ValueError(f"unsupported scene category: {unknown[0]}")
        normalized["suggested_categories"] = tuple(dict.fromkeys(categories))
        lane = _required_text(normalized.get("evaluation_lane"), "evaluation_lane")
        if lane not in {
            "SOCIAL_CONVERSATION",
            "GROUPMATE_CAPABILITY",
            "EXTERNAL_PLUGIN_COMPATIBILITY",
        }:
            raise ValueError("unsupported evaluation lane")
        normalized["evaluation_lane"] = lane
        ownership = _required_text(normalized.get("ownership"), "ownership")
        if ownership not in {"GROUPMATE", "EXTERNAL_PLUGIN", "UNKNOWN"}:
            raise ValueError("unsupported ownership")
        normalized["ownership"] = ownership
        if type(normalized.get("installed")) is not bool:
            raise ValueError("installed must be a boolean")
        normalized["runtime_mode"] = _required_text(
            normalized.get("runtime_mode"), "runtime_mode"
        )
        target = str(normalized.get("target") or "").strip()
        normalized["target"] = target or None
        response = str(normalized.get("candidate_response") or "").strip()
        normalized["candidate_response"] = response or None
        return cls(**normalized)


@dataclass(frozen=True)
class ShadowReviewItem:
    decision_id: str
    entity_ref: str
    persona_id: str
    group_id: str
    occurred_at: int
    config_version: int
    source_kind: str
    installed: bool
    runtime_mode: str
    status: str
    categories: tuple[str, ...]
    label: dict[str, object] | None
    reviewer_id: str | None
    reviewed_at: int | None
    split: str | None
    labels_frozen: bool
    capture: ShadowDecisionCapture


@dataclass(frozen=True)
class ShadowCaptureResult:
    item: ShadowReviewItem
    event: SocialEventEnvelope


@dataclass(frozen=True)
class ShadowReleaseConfig:
    false_positive_rate_cap: float
    scene_minimums: Mapping[str, int]
    holdout_minimums: Mapping[str, float]
    attention_window_ms_bounds: tuple[int, int]
    participation_weight_bounds: tuple[float, float]
    lane_minimums: Mapping[str, int]
    capability_minimums: Mapping[str, float]
    compatibility_minimums: Mapping[str, float]
    minimum_reviewed: int = 100
    calibration_fraction: float = 0.8

    def __post_init__(self) -> None:
        if (
            isinstance(self.false_positive_rate_cap, bool)
            or not isinstance(self.false_positive_rate_cap, (int, float))
            or not math.isfinite(float(self.false_positive_rate_cap))
            or not 0 <= float(self.false_positive_rate_cap) <= 1
        ):
            raise ValueError("false_positive_rate_cap must be explicitly between 0 and 1")
        if type(self.minimum_reviewed) is not int or self.minimum_reviewed < 100:
            raise ValueError("minimum_reviewed cannot weaken the 100-review hard gate")
        if not 0 < float(self.calibration_fraction) < 1:
            raise ValueError("calibration_fraction must leave a later holdout")
        unknown = sorted(set(self.scene_minimums) - _SCENE_CATEGORY_SET)
        if unknown:
            raise ValueError(f"unsupported release scene category: {unknown[0]}")
        if not self.scene_minimums or any(
            type(value) is not int or value < 1
            for value in self.scene_minimums.values()
        ):
            raise ValueError("scene_minimums must explicitly require positive coverage")
        required_thresholds = {
            "attention_precision",
            "action_precision",
            "target_precision",
        }
        if set(self.holdout_minimums) != required_thresholds or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not 0 <= float(value) <= 1
            for value in self.holdout_minimums.values()
        ):
            raise ValueError("holdout_minimums must explicitly define precision gates")
        if (
            len(self.attention_window_ms_bounds) != 2
            or type(self.attention_window_ms_bounds[0]) is not int
            or type(self.attention_window_ms_bounds[1]) is not int
            or not 0 <= self.attention_window_ms_bounds[0]
            <= self.attention_window_ms_bounds[1]
        ):
            raise ValueError("attention_window_ms_bounds are invalid")
        if (
            len(self.participation_weight_bounds) != 2
            or not all(
                isinstance(value, (int, float)) and not isinstance(value, bool)
                for value in self.participation_weight_bounds
            )
            or not 0 <= float(self.participation_weight_bounds[0])
            <= float(self.participation_weight_bounds[1])
        ):
            raise ValueError("participation_weight_bounds are invalid")
        lane_names = {
            "SOCIAL_CONVERSATION",
            "GROUPMATE_CAPABILITY",
            "EXTERNAL_PLUGIN_COMPATIBILITY",
        }
        if set(self.lane_minimums) != lane_names or any(
            type(value) is not int or value < 0
            for value in self.lane_minimums.values()
        ):
            raise ValueError("lane_minimums must explicitly define every lane")
        if set(self.capability_minimums) != {"task", "delivery", "recovery"}:
            raise ValueError("capability_minimums must define task/delivery/recovery")
        if set(self.compatibility_minimums) != {
            "no_steal", "no_duplicate", "no_self_attribution"
        }:
            raise ValueError("compatibility_minimums must define compatibility gates")
        for values in (self.capability_minimums, self.compatibility_minimums):
            if any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not 0 <= float(value) <= 1
                for value in values.values()
            ):
                raise ValueError("lane quality minimums must be between 0 and 1")
        if self.lane_minimums["SOCIAL_CONVERSATION"] <= 0:
            raise ValueError("social lane minimum must be positive")
        for lane, thresholds in (
            ("GROUPMATE_CAPABILITY", self.capability_minimums),
            ("EXTERNAL_PLUGIN_COMPATIBILITY", self.compatibility_minimums),
        ):
            applicable = self.lane_minimums[lane] > 0
            if applicable and any(float(value) <= 0 for value in thresholds.values()):
                raise ValueError(
                    "applicable lane minimums must all be explicitly positive"
                )
            if not applicable and any(float(value) != 0 for value in thresholds.values()):
                raise ValueError(
                    "inapplicable lane minimums must all be explicitly zero"
                )

    def to_dict(self) -> dict[str, object]:
        return {
            "false_positive_rate_cap": float(self.false_positive_rate_cap),
            "scene_minimums": dict(self.scene_minimums),
            "holdout_minimums": dict(self.holdout_minimums),
            "attention_window_ms_bounds": list(self.attention_window_ms_bounds),
            "participation_weight_bounds": list(self.participation_weight_bounds),
            "lane_minimums": dict(self.lane_minimums),
            "capability_minimums": dict(self.capability_minimums),
            "compatibility_minimums": dict(self.compatibility_minimums),
            "minimum_reviewed": self.minimum_reviewed,
            "calibration_fraction": self.calibration_fraction,
        }


@dataclass(frozen=True)
class FrozenShadowCorpus:
    manifest_version: int
    scenario_digest: str
    label_digest: str
    artifact_digest: str
    records: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class ShadowCalibrationRun:
    calibration_id: str
    entity_ref: str
    persona_id: str
    group_id: str
    manifest_version: int
    status: str
    proposed_config: dict[str, object]
    comparison: dict[str, object]
    reason_codes: tuple[str, ...]
    baseline_config_version: int
    baseline_config_digest: str
    config_version: int | None = None


class ShadowReviewRepository:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        initialize_database(self.path)
        self._ensure_tables()

    def record(self, capture: ShadowDecisionCapture) -> ShadowReviewItem:
        if not isinstance(capture, ShadowDecisionCapture):
            raise ValueError("shadow capture contract is required")
        decision_id = "shadow-decision:" + hashlib.sha256(
            f"{capture.persona_id}\0{capture.group_id}\0{capture.frame_id}".encode()
        ).hexdigest()[:24]
        entity_ref = "evaluation:" + hashlib.sha256(decision_id.encode()).hexdigest()[:20]
        source_kind = (
            "installed_live_shadow"
            if capture.installed and capture.runtime_mode == "SHADOW"
            else "installed_live_social_runtime"
            if capture.installed and capture.runtime_mode == "SOCIAL_RUNTIME"
            else "historical_bootstrap"
        )
        encoded = _canonical(asdict(capture))
        with connect_database(self.path) as db:
            existing = db.execute(
                "SELECT capture_json FROM shadow_review_items WHERE decision_id=?",
                (decision_id,),
            ).fetchone()
            if existing is not None and str(existing[0]) != encoded:
                raise ValueError("shadow decision identity belongs to different content")
            db.execute(
                "INSERT OR IGNORE INTO shadow_review_items("
                "decision_id, entity_ref, persona_id, group_id, occurred_at, "
                "config_version, source_kind, installed, runtime_mode, capture_json, "
                "status, categories_json, label_json, labels_frozen"
                ") VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', '[]', NULL, 0)",
                (
                    decision_id,
                    entity_ref,
                    capture.persona_id,
                    capture.group_id,
                    capture.occurred_at,
                    capture.config_version,
                    source_kind,
                    int(capture.installed),
                    capture.runtime_mode,
                    encoded,
                ),
            )
        return self.load(entity_ref)

    def capture_runtime(self, evaluation: object) -> ShadowCaptureResult:
        if not bool(getattr(evaluation, "accepted", False)):
            raise ValueError("only accepted runtime evaluations may be reviewed")
        frame = evaluation.frame
        candidates = tuple(getattr(evaluation, "candidates", ()))
        selected = set(evaluation.governor_result.selected_intention_ids)
        selected_candidates = tuple(
            candidate for candidate in candidates if candidate.intention_id in selected
        )
        target = next(
            (candidate.target_id for candidate in selected_candidates if candidate.target_id),
            None,
        )
        target_alias = _alias(target)
        events = tuple(getattr(evaluation, "context_events", ()))
        focus_id = (
            frame.focus_event_ids[-1]
            if frame is not None and frame.focus_event_ids
            else evaluation.source_event.event_id
        )

        def safe_event(event: SocialEventEnvelope) -> dict[str, object]:
            return {
                "occurred_at": event.occurred_at,
                "actor_ref": _alias(event.actor_id),
                "summary": _safe_summary(event.payload.get("text") or event.event_type),
                "media": [
                    str(item.get("type") or "media")
                    for item in event.payload.get("media", ())
                    if isinstance(item, Mapping)
                ],
            }

        focus_event = next(
            (event for event in events if event.event_id == focus_id),
            getattr(evaluation, "source_event", None),
        )
        if focus_event is None:
            raise ValueError("accepted SHADOW evaluation requires one focus event")
        history = tuple(
            safe_event(event)
            for event in events
            if event.event_id != focus_event.event_id
        )
        actions = tuple(
            {
                "kind": candidate.kind,
                "proposed_act": candidate.proposed_act,
            }
            for candidate in candidates
        )
        source_event = getattr(evaluation, "source_event", focus_event)
        ownership = str(source_event.payload.get("interaction_owner") or "GROUPMATE")
        lane = (
            "EXTERNAL_PLUGIN_COMPATIBILITY"
            if ownership == "EXTERNAL_PLUGIN"
            else "GROUPMATE_CAPABILITY"
            if any(candidate.kind == "CAPABILITY" for candidate in candidates)
            else "SOCIAL_CONVERSATION"
        )
        outcome = evaluation.governor_result.outcome
        categories = (
            ("correct_silence",)
            if frame is None and ownership == "EXTERNAL_PLUGIN"
            else self._suggest_categories(frame, candidates, outcome)
        )
        expires_at = max(
            (candidate.expires_at for candidate in candidates),
            default=(frame.deadline if frame is not None else source_event.occurred_at),
        )
        runtime_mode = getattr(evaluation, "runtime_mode", None)
        if runtime_mode is None:
            raise ValueError("authoritative runtime mode is required")
        runtime_mode_value = getattr(runtime_mode, "value", str(runtime_mode))
        capture = ShadowDecisionCapture.create(
            persona_id=getattr(evaluation, "persona_id"),
            group_id=source_event.group_id,
            frame_id=(
                frame.frame_id
                if frame is not None
                else f"external:{evaluation.request_id}"
            ),
            source_event_id=source_event.event_id,
            correlation_id=source_event.correlation_id,
            occurred_at=source_event.occurred_at,
            config_version=evaluation.config_version,
            history=history,
            focus=safe_event(focus_event),
            attention={
                "trigger_kind": (
                    frame.trigger_kind
                    if frame is not None
                    else "EXTERNAL_COMPATIBILITY"
                ),
                "urgency": frame.urgency if frame is not None else "none",
                "deadline": (
                    frame.deadline
                    if frame is not None
                    else source_event.occurred_at
                ),
            },
            target=target_alias,
            candidate_response=None,
            candidate_actions=actions,
            governor={
                "outcome": outcome,
                "reason_codes": list(evaluation.governor_result.reason_codes),
                "constraints": list(evaluation.governor_result.constraints),
                "reconsider_at": evaluation.governor_result.reconsider_at,
            },
            expires_at=expires_at,
            prediction={
                "attention": frame is not None,
                "action": outcome == "ACT",
                "target": target_alias,
                "intent": (
                    selected_candidates[0].proposed_act if selected_candidates else None
                ),
                "modalities": ["text"] if outcome == "ACT" else [],
                "text": "",
            },
            suggested_categories=categories,
            evaluation_lane=lane,
            ownership=ownership,
            installed=True,
            runtime_mode=runtime_mode_value,
        )
        item = self.record(capture)
        event = self._projection_event(item)
        self._append_projection_effect(event, item)
        return ShadowCaptureResult(item=item, event=event)

    def review(
        self,
        entity_ref: str,
        *,
        reviewer_id: str,
        decision: str,
        categories: tuple[str, ...] = (),
        correction: Mapping[str, object] | None = None,
        reviewed_at: int | None = None,
    ) -> ShadowReviewItem:
        with connect_database(self.path) as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                self._review_on(
                    db,
                    entity_ref,
                    persona_id=None,
                    group_id=None,
                    reviewer_id=reviewer_id,
                    decision=decision,
                    categories=categories,
                    correction=correction,
                    reviewed_at=int(time.time()) if reviewed_at is None else reviewed_at,
                )
                db.commit()
            except BaseException:
                db.rollback()
                raise
        return self.load(entity_ref)

    def _review_on(
        self,
        db: sqlite3.Connection,
        entity_ref: str,
        *,
        persona_id: str | None,
        group_id: str | None,
        reviewer_id: str,
        decision: str,
        categories: tuple[str, ...],
        correction: Mapping[str, object] | None,
        reviewed_at: int,
    ) -> dict[str, object]:
        normalized_ref = _required_text(entity_ref, "entity_ref")
        reviewer = _required_text(reviewer_id, "reviewer_id")
        verdict = _required_text(decision, "review decision")
        if verdict not in _REVIEW_DECISIONS:
            raise ValueError("unsupported shadow review decision")
        query = (
            "SELECT * FROM shadow_review_items WHERE entity_ref=?"
            + (" AND persona_id=? AND group_id=?" if persona_id is not None else "")
        )
        parameters: tuple[object, ...] = (
            (normalized_ref, persona_id, group_id)
            if persona_id is not None
            else (normalized_ref,)
        )
        row = db.execute(query, parameters).fetchone()
        if row is None:
            raise LookupError("shadow review target is not available")
        if bool(row["labels_frozen"]):
            if str(row["split"] or "") == "holdout":
                raise FrozenHoldoutError("frozen holdout labels cannot be changed")
            raise ValueError("frozen calibration labels cannot be changed")
        if str(row["status"]) != "pending":
            raise ValueError("shadow decision was already reviewed")
        capture = ShadowDecisionCapture.create(**json.loads(str(row["capture_json"])))
        selected_categories = tuple(
            dict.fromkeys(
                _required_text(value, "scene category") for value in categories
            )
        ) or capture.suggested_categories
        unknown = sorted(set(selected_categories) - _SCENE_CATEGORY_SET)
        if unknown:
            raise ValueError(f"unsupported scene category: {unknown[0]}")
        if verdict != "insufficient" and not selected_categories:
            raise ValueError("reviewed shadow decision requires scene categories")
        label = None
        if verdict == "reasonable":
            if correction:
                raise ValueError("reasonable review does not accept a correction")
            label = self._label_from_prediction(capture)
        elif verdict == "unreasonable":
            if not isinstance(correction, Mapping) or not correction:
                raise ValueError("unreasonable review requires a correction")
            label = EvaluationLabel.from_dict(correction).to_dict()
        elif correction:
            raise ValueError("insufficient review cannot invent a correction")
        db.execute(
            "UPDATE shadow_review_items SET status=?, categories_json=?, "
            "label_json=?, reviewer_id=?, reviewed_at=? WHERE decision_id=?",
            (
                verdict,
                _canonical(selected_categories),
                None if label is None else _canonical(label),
                reviewer,
                int(reviewed_at),
                str(row["decision_id"]),
            ),
        )
        return {
            "entity_ref": normalized_ref,
            "decision": verdict,
            "categories": list(selected_categories),
        }

    def freeze(
        self,
        *,
        persona_id: str,
        group_id: str,
        release_config: ShadowReleaseConfig,
    ) -> FrozenShadowCorpus:
        if not isinstance(release_config, ShadowReleaseConfig):
            raise ValueError("explicit shadow release config is required")
        persona = _required_text(persona_id, "persona_id")
        group = _required_text(group_id, "group_id")
        existing = self._latest_manifest(persona, group)
        if existing is not None:
            return self.frozen_corpus(persona_id=persona, group_id=group)
        with connect_database(self.path) as db:
            rows = db.execute(
                "SELECT * FROM shadow_review_items WHERE persona_id=? AND group_id=? "
                "AND status IN ('reasonable','unreasonable') AND installed=1 "
                "AND runtime_mode='SHADOW' AND source_kind='installed_live_shadow' "
                "ORDER BY occurred_at, decision_id",
                (persona, group),
            ).fetchall()
            if len(rows) < release_config.minimum_reviewed:
                raise ValueError(
                    "first calibration requires at least 100 real human-reviewed "
                    "installed_live_shadow decisions"
                )
            coverage = {category: 0 for category in release_config.scene_minimums}
            for row in rows:
                for category in json.loads(str(row["categories_json"])):
                    if category in coverage:
                        coverage[category] += 1
            missing = {
                category: minimum - coverage[category]
                for category, minimum in release_config.scene_minimums.items()
                if coverage[category] < minimum
            }
            if missing:
                raise ValueError(f"release scene coverage is incomplete: {missing}")
            calibration_count = int(len(rows) * release_config.calibration_fraction)
            calibration_count = max(1, min(len(rows) - 1, calibration_count))
            db.execute("BEGIN IMMEDIATE")
            for index, row in enumerate(rows):
                split = "calibration" if index < calibration_count else "holdout"
                db.execute(
                    "UPDATE shadow_review_items SET split=?, labels_frozen=1 "
                    "WHERE decision_id=? AND labels_frozen=0",
                    (split, str(row["decision_id"])),
                )
            records = self._records_on(db, persona, group)
            scenario_digest, label_digest = self._digests(records)
            artifact_digest = frozen_artifact_digest(records)
            version = int(
                db.execute(
                    "SELECT COALESCE(MAX(manifest_version), 0) + 1 "
                    "FROM shadow_manifests WHERE persona_id=? AND group_id=?",
                    (persona, group),
                ).fetchone()[0]
            )
            db.execute(
                "INSERT INTO shadow_manifests(manifest_version, persona_id, group_id, "
                "scenario_digest, label_digest, artifact_digest, "
                "release_config_json, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    version,
                    persona,
                    group,
                    scenario_digest,
                    label_digest,
                    artifact_digest,
                    _canonical(release_config.to_dict()),
                    int(time.time()),
                ),
            )
            db.commit()
        return self.frozen_corpus(persona_id=persona, group_id=group)

    def frozen_corpus(self, *, persona_id: str, group_id: str) -> FrozenShadowCorpus:
        manifest = self._latest_manifest(persona_id, group_id)
        if manifest is None:
            raise ValueError("live SHADOW labels have not been frozen")
        with connect_database(self.path) as db:
            records = self._records_on(db, persona_id, group_id)
        scenario_digest, label_digest = self._digests(records)
        artifact_digest = frozen_artifact_digest(records)
        if (
            scenario_digest != str(manifest["scenario_digest"])
            or label_digest != str(manifest["label_digest"])
        ):
            raise ValueError("frozen SHADOW manifest no longer matches its labels")
        if artifact_digest != str(manifest["artifact_digest"]):
            raise ValueError("frozen SHADOW content no longer matches its manifest")
        provenance = {
            "kind": "installed_live_shadow",
            "manifest_version": int(manifest["manifest_version"]),
            "installed": True,
            "runtime_mode": "SHADOW",
            "frozen": True,
            "scenario_digest": scenario_digest,
            "label_digest": label_digest,
            "artifact_digest": artifact_digest,
        }
        bound = tuple(
            {**record, "shadow_provenance": copy.deepcopy(provenance)}
            for record in records
        )
        return FrozenShadowCorpus(
            manifest_version=int(manifest["manifest_version"]),
            scenario_digest=scenario_digest,
            label_digest=label_digest,
            artifact_digest=artifact_digest,
            records=bound,
        )

    def list_items(self, *, persona_id: str, group_id: str) -> tuple[ShadowReviewItem, ...]:
        with connect_database(self.path) as db:
            rows = db.execute(
                "SELECT * FROM shadow_review_items WHERE persona_id=? AND group_id=? "
                "ORDER BY occurred_at, decision_id",
                (persona_id, group_id),
            ).fetchall()
        return tuple(self._decode(row) for row in rows)

    def load(self, entity_ref: str) -> ShadowReviewItem:
        with connect_database(self.path) as db:
            row = db.execute(
                "SELECT * FROM shadow_review_items WHERE entity_ref=?",
                (_required_text(entity_ref, "entity_ref"),),
            ).fetchone()
        if row is None:
            raise LookupError("shadow review target is not available")
        return self._decode(row)

    def save_calibration(
        self,
        *,
        persona_id: str,
        group_id: str,
        manifest_version: int,
        proposed_config: Mapping[str, object],
        comparison: Mapping[str, object],
        baseline_config_version: int,
        baseline_config_digest: str,
        status: str,
        reason_codes: tuple[str, ...],
    ) -> ShadowCalibrationRun:
        identity = _canonical(
            {
                "persona_id": persona_id,
                "group_id": group_id,
                "manifest_version": manifest_version,
                "proposed_config": proposed_config,
                "comparison": comparison,
                "baseline_config_version": baseline_config_version,
                "baseline_config_digest": baseline_config_digest,
            }
        )
        calibration_id = "shadow-calibration-run:" + hashlib.sha256(
            identity.encode()
        ).hexdigest()[:24]
        entity_ref = "calibration:" + hashlib.sha256(
            calibration_id.encode()
        ).hexdigest()[:20]
        with connect_database(self.path) as db:
            db.execute(
                "INSERT OR IGNORE INTO shadow_calibration_runs("
                "calibration_id, entity_ref, persona_id, group_id, manifest_version, "
                "status, proposed_config_json, comparison_json, reason_codes_json, "
                "baseline_config_version, baseline_config_digest, created_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    calibration_id,
                    entity_ref,
                    persona_id,
                    group_id,
                    manifest_version,
                    status,
                    _canonical(dict(proposed_config)),
                    _canonical(dict(comparison)),
                    _canonical(reason_codes),
                    int(baseline_config_version),
                    _required_text(baseline_config_digest, "baseline_config_digest"),
                    int(time.time()),
                ),
            )
        run = self.load_calibration(entity_ref)
        self._append_calibration_projection(run)
        return run

    def load_calibration(self, entity_ref: str) -> ShadowCalibrationRun:
        with connect_database(self.path) as db:
            row = db.execute(
                "SELECT * FROM shadow_calibration_runs WHERE entity_ref=?",
                (entity_ref,),
            ).fetchone()
        if row is None:
            raise LookupError("shadow calibration is not available")
        return self._decode_calibration(row)

    def _approve_calibration_on(
        self,
        db: sqlite3.Connection,
        entity_ref: str,
        *,
        persona_id: str,
        group_id: str,
        config_repository: object,
        now: int,
    ) -> dict[str, object]:
        row = db.execute(
            "SELECT * FROM shadow_calibration_runs WHERE entity_ref=? "
            "AND persona_id=? AND group_id=?",
            (entity_ref, persona_id, group_id),
        ).fetchone()
        if row is None:
            raise LookupError("shadow calibration is not available")
        status = str(row["status"])
        if status != "PENDING_APPROVAL":
            raise ShadowCalibrationRejected(
                "only a passing pending calibration may be approved"
            )
        config_scope = hashlib.sha256(
            f"{persona_id}\0{group_id}".encode()
        ).hexdigest()[:24]
        config_id = f"shadow-calibration:{config_scope}"
        proposed = json.loads(str(row["proposed_config_json"]))
        current = config_repository._published_on(
            db, persona_id=persona_id, group_id=group_id
        )
        current_version = 0 if current is None else current.version
        current_config = {} if current is None else current.config
        current_digest = hashlib.sha256(_canonical(current_config).encode()).hexdigest()
        if (
            current_version != int(row["baseline_config_version"])
            or current_digest != str(row["baseline_config_digest"])
        ):
            raise ShadowCalibrationRejected(
                "calibration baseline is stale; rerun against current config"
            )
        merged = {**current_config, **proposed}
        draft = config_repository._create_draft_on(
            db,
            config_id,
            merged,
            persona_id=persona_id,
            group_id=group_id,
            now=now,
        )
        config_repository._validate_on(
            db,
            config_id,
            persona_id=persona_id,
            group_id=group_id,
        )
        published = config_repository._publish_on(
            db,
            config_id,
            persona_id=persona_id,
            group_id=group_id,
            expected_version=current_version,
        )
        db.execute(
            "UPDATE shadow_calibration_runs SET status='APPROVED', "
            "config_id=?, config_version=?, approved_at=? WHERE calibration_id=?",
            (config_id, published.version, now, str(row["calibration_id"])),
        )
        return {
            "entity_ref": entity_ref,
            "status": "APPROVED",
            "config_id": config_id,
            "config_version": published.version,
            "draft_version": draft.version,
        }

    def _projection_event(self, item: ShadowReviewItem) -> SocialEventEnvelope:
        capture = item.capture
        return SocialEventEnvelope.create(
            event_id=f"evaluation-capture:{item.decision_id}",
            event_type="evaluation.shadow_decision_captured",
            occurred_at=capture.occurred_at,
            received_at=int(time.time()),
            persona_id=capture.persona_id,
            group_id=capture.group_id,
            actor_id=None,
            source_message_id=None,
            correlation_id=capture.correlation_id,
            causation_id=capture.source_event_id,
            payload={
                "entity_ref": item.entity_ref,
                "status": item.status,
                "runtime_mode": capture.runtime_mode,
                "config_version": capture.config_version,
                "history": list(capture.history),
                "focus": [capture.focus],
                "attention": capture.attention,
                "target": capture.target,
                "candidate_response": capture.candidate_response,
                "candidate_actions": list(capture.candidate_actions),
                "governor_result": capture.governor,
                "reason_codes": list(capture.governor.get("reason_codes", ())),
                "suggested_categories": list(capture.suggested_categories),
                "expires_at": capture.expires_at,
            },
        )

    def _append_projection_effect(
        self, event: SocialEventEnvelope, item: ShadowReviewItem
    ) -> None:
        effect = {
            "effect_id": f"projection:{item.decision_id}",
            "kind": event.event_type,
            "persona_id": item.persona_id,
            "group_id": item.group_id,
            **dict(event.payload),
        }
        encoded = _canonical(effect)
        with connect_database(self.path) as db:
            existing = db.execute(
                "SELECT source_event_id, correlation_id, effect_type, effect_json "
                "FROM journal WHERE effect_id=?",
                (effect["effect_id"],),
            ).fetchone()
            identity = (
                item.capture.source_event_id,
                item.capture.correlation_id,
                event.event_type,
                encoded,
            )
            if existing is not None:
                if tuple(existing) != identity:
                    raise ValueError(
                        "shadow projection identity belongs to different content"
                    )
                return
            db.execute(
                "INSERT INTO journal(effect_id, source_event_id, correlation_id, "
                "causation_id, actor_key, effect_type, effect_json, committed_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    effect["effect_id"],
                    item.capture.source_event_id,
                    item.capture.correlation_id,
                    item.capture.source_event_id,
                    f"evaluation:{item.persona_id}:{item.group_id}",
                    event.event_type,
                    encoded,
                    int(time.time()),
                ),
            )

    def _append_calibration_projection(self, run: ShadowCalibrationRun) -> None:
        effect_id = f"projection:{run.calibration_id}"
        effect = {
            "effect_id": effect_id,
            "kind": "calibration.shadow_candidate_evaluated",
            "persona_id": run.persona_id,
            "group_id": run.group_id,
            "entity_ref": run.entity_ref,
            "manifest_version": run.manifest_version,
            "status": run.status,
            "reason_codes": list(run.reason_codes),
            "comparison": run.comparison,
        }
        encoded = _canonical(effect)
        with connect_database(self.path) as db:
            existing = db.execute(
                "SELECT effect_json FROM journal WHERE effect_id=?", (effect_id,)
            ).fetchone()
            if existing is not None:
                if str(existing[0]) != encoded:
                    raise ValueError(
                        "calibration projection identity belongs to different content"
                    )
                return
            db.execute(
                "INSERT INTO journal(effect_id, source_event_id, correlation_id, "
                "causation_id, actor_key, effect_type, effect_json, committed_at) "
                "VALUES(?, ?, ?, NULL, ?, ?, ?, ?)",
                (
                    effect_id,
                    f"shadow-manifest:{run.manifest_version}",
                    run.calibration_id,
                    f"evaluation:{run.persona_id}:{run.group_id}",
                    "calibration.shadow_candidate_evaluated",
                    encoded,
                    int(time.time()),
                ),
            )

    @staticmethod
    def _label_from_prediction(capture: ShadowDecisionCapture) -> dict[str, object]:
        prediction = capture.prediction
        action = bool(prediction.get("action"))
        intent = str(prediction.get("intent") or "").strip()
        modalities = prediction.get("modalities", ())
        return EvaluationLabel.create(
            attention=bool(prediction.get("attention")),
            action=action,
            target=str(prediction.get("target") or "").strip() or None,
            acceptable_intents=[intent] if action and intent else [],
            unacceptable_intents=[],
            modalities=list(modalities) if action and isinstance(modalities, (tuple, list)) else [],
            sensitivity="group",
            expires_after_ms=max(0, capture.expires_at - capture.occurred_at) * 1_000,
        ).to_dict()

    @staticmethod
    def _suggest_categories(frame: object, candidates: tuple[object, ...], outcome: str) -> tuple[str, ...]:
        kinds = {str(candidate.kind) for candidate in candidates}
        if "BOUNDARY" in kinds:
            return ("boundary",)
        if "CARE" in kinds:
            return ("care",)
        if "PLAY" in kinds:
            return ("humor",)
        if "HELP" in kinds:
            return ("public_help",)
        if getattr(frame, "trigger_kind", "") == "TEMPORAL":
            return ("autonomous_initiation",)
        if outcome == "SILENCE":
            return ("correct_silence",)
        return ("direct_interaction",)

    @staticmethod
    def _decode(row: sqlite3.Row) -> ShadowReviewItem:
        label = None if row["label_json"] is None else json.loads(str(row["label_json"]))
        return ShadowReviewItem(
            decision_id=str(row["decision_id"]),
            entity_ref=str(row["entity_ref"]),
            persona_id=str(row["persona_id"]),
            group_id=str(row["group_id"]),
            occurred_at=int(row["occurred_at"]),
            config_version=int(row["config_version"]),
            source_kind=str(row["source_kind"]),
            installed=bool(row["installed"]),
            runtime_mode=str(row["runtime_mode"]),
            status=str(row["status"]),
            categories=tuple(json.loads(str(row["categories_json"]))),
            label=label,
            reviewer_id=None if row["reviewer_id"] is None else str(row["reviewer_id"]),
            reviewed_at=None if row["reviewed_at"] is None else int(row["reviewed_at"]),
            split=None if row["split"] is None else str(row["split"]),
            labels_frozen=bool(row["labels_frozen"]),
            capture=ShadowDecisionCapture.create(**json.loads(str(row["capture_json"]))),
        )

    @staticmethod
    def _decode_calibration(row: sqlite3.Row) -> ShadowCalibrationRun:
        return ShadowCalibrationRun(
            calibration_id=str(row["calibration_id"]),
            entity_ref=str(row["entity_ref"]),
            persona_id=str(row["persona_id"]),
            group_id=str(row["group_id"]),
            manifest_version=int(row["manifest_version"]),
            status=str(row["status"]),
            proposed_config=json.loads(str(row["proposed_config_json"])),
            comparison=json.loads(str(row["comparison_json"])),
            reason_codes=tuple(json.loads(str(row["reason_codes_json"]))),
            baseline_config_version=int(row["baseline_config_version"]),
            baseline_config_digest=str(row["baseline_config_digest"]),
            config_version=(
                None if row["config_version"] is None else int(row["config_version"])
            ),
        )

    def _records_on(self, db: sqlite3.Connection, persona_id: str, group_id: str) -> tuple[dict[str, object], ...]:
        rows = db.execute(
            "SELECT * FROM shadow_review_items WHERE persona_id=? AND group_id=? "
            "AND labels_frozen=1 AND split IN ('calibration','holdout') "
            "ORDER BY occurred_at, decision_id",
            (persona_id, group_id),
        ).fetchall()
        records = []
        for row in rows:
            item = self._decode(row)
            context = [
                {**entry, "event_id": f"context:{item.decision_id}:{index}"}
                for index, entry in enumerate(item.capture.history)
            ]
            context.append(
                {
                    **item.capture.focus,
                    "event_id": f"focus:{item.decision_id}",
                }
            )
            records.append(
                {
                    "scenario_id": item.decision_id,
                    "split": item.split,
                    "corpus_kind": "shadow",
                    "labels_frozen": True,
                    "evaluation_lane": item.capture.evaluation_lane,
                    "ownership": item.capture.ownership,
                    "candidate_producer": "GROUPMATE",
                    "context_provenance": {
                        "kind": "installed_live_shadow",
                        "complete_member_context": True,
                        "bot_only": False,
                    },
                    "group_id": item.group_id,
                    "categories": list(item.categories),
                    "context": context,
                    "label": copy.deepcopy(item.label),
                    "prediction": copy.deepcopy(item.capture.prediction),
                    "decision_occurred_at": item.occurred_at,
                    "frozen_truth": (
                        {
                            "task": item.status == "reasonable",
                            "delivery": item.status == "reasonable",
                            "recovery": item.status == "reasonable",
                        }
                        if item.capture.evaluation_lane == "GROUPMATE_CAPABILITY"
                        else {}
                    ),
                    "external_response_owner": (
                        "EXTERNAL_PLUGIN"
                        if item.capture.evaluation_lane
                        == "EXTERNAL_PLUGIN_COMPATIBILITY"
                        else None
                    ),
                    "external_response_correlation": (
                        f"external:{item.decision_id}"
                        if item.capture.evaluation_lane
                        == "EXTERNAL_PLUGIN_COMPATIBILITY"
                        else None
                    ),
                }
            )
        return tuple(records)

    @staticmethod
    def _digests(records: tuple[Mapping[str, object], ...]) -> tuple[str, str]:
        scenario_digest = hashlib.sha256(
            _canonical(
                [
                    {
                        "scenario_id": item.get("scenario_id"),
                        "split": item.get("split"),
                        "evaluation_lane": item.get("evaluation_lane"),
                    }
                    for item in records
                ]
            ).encode()
        ).hexdigest()
        label_digest = hashlib.sha256(
            _canonical(
                [
                    {"scenario_id": item.get("scenario_id"), "label": item.get("label")}
                    for item in records
                ]
            ).encode()
        ).hexdigest()
        return scenario_digest, label_digest

    def _latest_manifest(self, persona_id: str, group_id: str):
        with connect_database(self.path) as db:
            return db.execute(
                "SELECT * FROM shadow_manifests WHERE persona_id=? AND group_id=? "
                "ORDER BY manifest_version DESC LIMIT 1",
                (persona_id, group_id),
            ).fetchone()

    def _ensure_tables(self) -> None:
        with connect_database(self.path) as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS shadow_review_items (
                    decision_id TEXT PRIMARY KEY,
                    entity_ref TEXT NOT NULL UNIQUE,
                    persona_id TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    occurred_at INTEGER NOT NULL,
                    config_version INTEGER NOT NULL,
                    source_kind TEXT NOT NULL,
                    installed INTEGER NOT NULL,
                    runtime_mode TEXT NOT NULL,
                    capture_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    categories_json TEXT NOT NULL,
                    label_json TEXT,
                    reviewer_id TEXT,
                    reviewed_at INTEGER,
                    split TEXT,
                    labels_frozen INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_shadow_review_scope
                    ON shadow_review_items(persona_id, group_id, occurred_at);
                CREATE TABLE IF NOT EXISTS shadow_manifests (
                    manifest_version INTEGER NOT NULL,
                    persona_id TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    scenario_digest TEXT NOT NULL,
                    label_digest TEXT NOT NULL,
                    artifact_digest TEXT NOT NULL,
                    release_config_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    PRIMARY KEY(persona_id, group_id, manifest_version)
                );
                CREATE TABLE IF NOT EXISTS shadow_calibration_runs (
                    calibration_id TEXT PRIMARY KEY,
                    entity_ref TEXT NOT NULL UNIQUE,
                    persona_id TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    manifest_version INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    proposed_config_json TEXT NOT NULL,
                    comparison_json TEXT NOT NULL,
                    reason_codes_json TEXT NOT NULL,
                    baseline_config_version INTEGER NOT NULL DEFAULT 0,
                    baseline_config_digest TEXT NOT NULL DEFAULT '',
                    config_id TEXT,
                    config_version INTEGER,
                    created_at INTEGER NOT NULL,
                    approved_at INTEGER
                );
                """
            )
            manifest_columns = {
                str(row[1]) for row in db.execute("PRAGMA table_info(shadow_manifests)")
            }
            if "artifact_digest" not in manifest_columns:
                db.execute(
                    "ALTER TABLE shadow_manifests ADD COLUMN artifact_digest TEXT NOT NULL DEFAULT ''"
                )
            calibration_columns = {
                str(row[1])
                for row in db.execute("PRAGMA table_info(shadow_calibration_runs)")
            }
            if "baseline_config_version" not in calibration_columns:
                db.execute(
                    "ALTER TABLE shadow_calibration_runs ADD COLUMN "
                    "baseline_config_version INTEGER NOT NULL DEFAULT 0"
                )
            if "baseline_config_digest" not in calibration_columns:
                db.execute(
                    "ALTER TABLE shadow_calibration_runs ADD COLUMN "
                    "baseline_config_digest TEXT NOT NULL DEFAULT ''"
                )


class ShadowCalibrationService:
    def __init__(
        self,
        repository: ShadowReviewRepository,
        *,
        runner: EvaluationRunner | None = None,
        config_repository: ConfigVersionRepository | None = None,
    ) -> None:
        self.repository = repository
        self.runner = runner or EvaluationRunner()
        self.config_repository = config_repository or ConfigVersionRepository(
            repository.path
        )
        if self.config_repository.path != repository.path:
            raise ValueError("config repository must share the shadow database")

    def run(
        self,
        *,
        persona_id: str,
        group_id: str,
        proposed_config: Mapping[str, object],
        release_config: ShadowReleaseConfig,
        baseline_runtime: object,
        candidate_runtime: object,
    ) -> ShadowCalibrationRun:
        normalized = self._validate_config(proposed_config, release_config)
        baseline_config = self.config_repository.snapshot(
            persona_id=persona_id, group_id=group_id
        )
        baseline_digest = hashlib.sha256(
            _canonical(baseline_config.config).encode()
        ).hexdigest()
        frozen = self.repository.frozen_corpus(
            persona_id=persona_id, group_id=group_id
        )
        full_baseline = self.runner.run(
            frozen.records, baseline_runtime, "live"
        )
        full_candidate = self.runner.run(
            frozen.records, candidate_runtime, "live"
        )
        for report in (full_baseline, full_candidate):
            if (
                report.kind != "frozen_shadow"
                or report.production_readiness_eligible is not True
            ):
                raise ValueError(
                    "calibration requires strict frozen SHADOW provenance"
                )
        comparison: dict[str, object] = {}
        reasons: list[str] = []
        for split in ("calibration", "holdout"):
            records = tuple(item for item in frozen.records if item["split"] == split)
            rebound = self._bind_subset_provenance(records, frozen.manifest_version)
            baseline = self.runner.run(rebound, baseline_runtime, "live")
            candidate = self.runner.run(rebound, candidate_runtime, "live")
            baseline_summary = self._report_summary(baseline)
            candidate_summary = self._report_summary(candidate)
            comparison[split] = {
                "baseline": baseline_summary,
                "candidate": candidate_summary,
            }
            if candidate_summary["safety_issue_count"] > baseline_summary["safety_issue_count"]:
                reasons.append("safety_regression")
            if candidate_summary["safety_issue_count"] != 0:
                reasons.append("safety_gate_failed")
            if candidate_summary["false_positive_rate"] > release_config.false_positive_rate_cap:
                reasons.append(f"{split}_false_positive_rate_cap_failed")
            self._lane_gate_reasons(
                split,
                baseline_summary,
                candidate_summary,
                release_config,
                reasons,
            )
        holdout_candidate = comparison["holdout"]["candidate"]
        assert isinstance(holdout_candidate, Mapping)
        for name, threshold in release_config.holdout_minimums.items():
            if float(holdout_candidate[name]) < float(threshold):
                reasons.append(f"holdout_{name}_failed")
        reason_codes = tuple(dict.fromkeys(reasons))
        return self.repository.save_calibration(
            persona_id=persona_id,
            group_id=group_id,
            manifest_version=frozen.manifest_version,
            proposed_config=normalized,
            comparison=comparison,
            baseline_config_version=baseline_config.version,
            baseline_config_digest=baseline_digest,
            status="REJECTED" if reason_codes else "PENDING_APPROVAL",
            reason_codes=reason_codes,
        )

    @staticmethod
    def _validate_config(
        config: Mapping[str, object], release: ShadowReleaseConfig
    ) -> dict[str, object]:
        if not isinstance(config, Mapping) or not config:
            raise ValueError("calibration config must be a non-empty object")
        unknown = sorted(set(config) - _CALIBRATABLE_FIELDS)
        if unknown:
            raise ValueError(f"field is not calibratable: {unknown[0]}")
        missing = sorted(_CALIBRATABLE_FIELDS - set(config))
        if missing:
            raise ValueError(f"calibratable field is missing: {missing[0]}")
        window = config["attention_window_ms"]
        low, high = release.attention_window_ms_bounds
        if type(window) is not int or not low <= window <= high:
            raise ValueError("attention_window_ms is outside the administrator bounds")
        if config["reply_length_tendency"] not in {"short", "balanced", "long"}:
            raise ValueError("reply_length_tendency is unsupported")
        if config["media_preference"] not in {
            "text_only",
            "contextual",
            "prefer_media",
        }:
            raise ValueError("media_preference is unsupported")
        weights = config["participation_weights"]
        if not isinstance(weights, Mapping) or not weights:
            raise ValueError("participation_weights must be a non-empty object")
        weight_low, weight_high = release.participation_weight_bounds
        for name, value in weights.items():
            _required_text(name, "participation weight name")
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or not weight_low <= float(value) <= weight_high
            ):
                raise ValueError("participation weight is outside administrator bounds")
        return json.loads(_canonical(dict(config)))

    @staticmethod
    def _bind_subset_provenance(
        records: tuple[Mapping[str, object], ...], manifest_version: int
    ) -> tuple[dict[str, object], ...]:
        copied = tuple(copy.deepcopy(dict(record)) for record in records)
        scenario_digest, label_digest = ShadowReviewRepository._digests(copied)
        artifact_digest = frozen_artifact_digest(copied)
        provenance = {
            "kind": "installed_live_shadow",
            "manifest_version": int(manifest_version),
            "installed": True,
            "runtime_mode": "SHADOW",
            "frozen": True,
            "scenario_digest": scenario_digest,
            "label_digest": label_digest,
            "artifact_digest": artifact_digest,
        }
        return tuple(
            {**record, "shadow_provenance": copy.deepcopy(provenance)}
            for record in copied
        )

    @staticmethod
    def _report_summary(report: object) -> dict[str, object]:
        social = report.lanes["SOCIAL_CONVERSATION"]
        action = social.metrics["action"] or {}
        attention = social.metrics["attention"] or {}
        target = social.metrics["target"] or {}
        false_denominator = int(action.get("fp", 0)) + int(action.get("tn", 0))
        false_positive_rate = (
            int(action.get("fp", 0)) / false_denominator
            if false_denominator
            else 0.0
        )
        lanes: dict[str, object] = {}
        for lane_name, lane in report.lanes.items():
            lane_summary: dict[str, object] = {
                "effect_count": lane.effect_count,
                "applicable": lane.effect_count > 0,
            }
            if lane_name == "GROUPMATE_CAPABILITY":
                quality = lane.metrics.get("quality")
                lane_summary["quality"] = {
                    name: (quality.get(name) if isinstance(quality, Mapping) else None)
                    for name in ("task", "delivery", "recovery")
                }
            if lane_name == "EXTERNAL_PLUGIN_COMPATIBILITY":
                compatibility = lane.compatibility or {}
                lane_summary["compatibility"] = {
                    name: (
                        float(compatibility.get(name, 0)) / lane.effect_count
                        if lane.effect_count
                        else None
                    )
                    for name in (
                        "no_steal", "no_duplicate", "no_self_attribution"
                    )
                }
            lanes[lane_name] = lane_summary
        return {
            "worker_mode": "live",
            "report_kind": "frozen_shadow_split",
            "candidate_digest": report.candidate_digest,
            "safety_issue_count": len(report.safety.issues),
            "false_positive_rate": false_positive_rate,
            "attention_precision": float(attention.get("precision", 0.0)),
            "action_precision": float(action.get("precision", 0.0)),
            "target_precision": float(target.get("precision", 0.0)),
            "lanes": lanes,
        }

    @staticmethod
    def _lane_gate_reasons(
        split: str,
        baseline: Mapping[str, object],
        candidate: Mapping[str, object],
        release: ShadowReleaseConfig,
        reasons: list[str],
    ) -> None:
        baseline_lanes = baseline.get("lanes", {})
        candidate_lanes = candidate.get("lanes", {})
        assert isinstance(baseline_lanes, Mapping)
        assert isinstance(candidate_lanes, Mapping)
        labels = {
            "SOCIAL_CONVERSATION": "social",
            "GROUPMATE_CAPABILITY": "capability",
            "EXTERNAL_PLUGIN_COMPATIBILITY": "external_compatibility",
        }
        for lane_name, minimum in release.lane_minimums.items():
            lane = candidate_lanes.get(lane_name, {})
            count = int(lane.get("effect_count", 0)) if isinstance(lane, Mapping) else 0
            if count < minimum:
                reasons.append(f"{labels[lane_name]}_coverage_unavailable")

        ShadowCalibrationService._quality_gate_reasons(
            split,
            "capability",
            baseline_lanes.get("GROUPMATE_CAPABILITY", {}),
            candidate_lanes.get("GROUPMATE_CAPABILITY", {}),
            "quality",
            release.capability_minimums,
            reasons,
        )
        ShadowCalibrationService._quality_gate_reasons(
            split,
            "external",
            baseline_lanes.get("EXTERNAL_PLUGIN_COMPATIBILITY", {}),
            candidate_lanes.get("EXTERNAL_PLUGIN_COMPATIBILITY", {}),
            "compatibility",
            release.compatibility_minimums,
            reasons,
        )

    @staticmethod
    def _quality_gate_reasons(
        split: str,
        lane_label: str,
        baseline_lane: object,
        candidate_lane: object,
        metric_group: str,
        minimums: Mapping[str, float],
        reasons: list[str],
    ) -> None:
        baseline_values = (
            baseline_lane.get(metric_group, {})
            if isinstance(baseline_lane, Mapping)
            else {}
        )
        candidate_values = (
            candidate_lane.get(metric_group, {})
            if isinstance(candidate_lane, Mapping)
            else {}
        )
        for name, threshold in minimums.items():
            before = baseline_values.get(name) if isinstance(baseline_values, Mapping) else None
            after = candidate_values.get(name) if isinstance(candidate_values, Mapping) else None
            if before is None or after is None:
                if float(threshold) > 0:
                    reasons.append(f"{lane_label}_coverage_unavailable")
                continue
            if before is not None and float(after) < float(before):
                reasons.append(f"{split}_{lane_label}_{name}_regression")
            if float(after) < float(threshold):
                reasons.append(f"{split}_{lane_label}_{name}_failed")


__all__ = (
    "FrozenHoldoutError",
    "FrozenShadowCorpus",
    "SCENE_CATEGORIES",
    "ShadowCalibrationRejected",
    "ShadowCalibrationRun",
    "ShadowCalibrationService",
    "ShadowCaptureResult",
    "ShadowDecisionCapture",
    "ShadowReleaseConfig",
    "ShadowReviewItem",
    "ShadowReviewRepository",
)
