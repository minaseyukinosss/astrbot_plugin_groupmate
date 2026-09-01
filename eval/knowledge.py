"""Safety-focused metrics for game knowledge understanding runs."""

from __future__ import annotations

from time import perf_counter
from typing import Iterable, Mapping

from groupmate.social_runtime.contracts import SocialEventEnvelope
from groupmate.social_runtime.knowledge.contracts import OriginClass


_REQUIRED_FIELDS = {
    "understanding_correct",
    "high_confidence_merge",
    "merge_correct",
    "cross_group_leak",
    "promoted_origin",
    "temporal_need_expected",
    "temporal_need_detected",
}
_BOOLEAN_FIELDS = _REQUIRED_FIELDS - {"promoted_origin"}
_ROLLOUT_FIELDS = {
    "observation_hours",
    "opportunities",
    "ambient_actions",
    "ambient_searches",
    "p95_latency_ms",
    "silence_reasons",
    "unsupported_claims",
    "stale_scene_sends",
    "cross_group_leaks",
    "nonknowledge_ambient_searches",
    "provider_quota_anomalies",
}
_ROLLOUT_COUNTERS = _ROLLOUT_FIELDS - {
    "observation_hours",
    "p95_latency_ms",
    "silence_reasons",
}


def _validated_record(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != _REQUIRED_FIELDS:
        raise ValueError("knowledge metric record has invalid fields")
    record = dict(value)
    if any(type(record[field]) is not bool for field in _BOOLEAN_FIELDS):
        raise ValueError("knowledge metric record booleans are invalid")
    promoted_origin = record["promoted_origin"]
    if promoted_origin is not None:
        try:
            OriginClass(promoted_origin)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "knowledge metric record promoted_origin is invalid"
            ) from exc
    if record["temporal_need_detected"] and not record["temporal_need_expected"]:
        raise ValueError(
            "knowledge metric record cannot detect an unexpected temporal need"
        )
    return record


def _ratio(numerator: int, denominator: int) -> float:
    return 0.0 if denominator == 0 else numerator / denominator


def _validated_rollout_window(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != _ROLLOUT_FIELDS:
        raise ValueError("rollout window has invalid fields")
    window = dict(value)
    for field in _ROLLOUT_COUNTERS:
        counter = window[field]
        if type(counter) is not int or counter < 0:
            raise ValueError(f"rollout window {field} must be a non-negative integer")
    for field in ("observation_hours", "p95_latency_ms"):
        number = window[field]
        if isinstance(number, bool) or not isinstance(number, (int, float)) or number < 0:
            raise ValueError(f"rollout window {field} must be non-negative")
    if window["ambient_actions"] > window["opportunities"]:
        raise ValueError("rollout window ambient_actions exceeds opportunities")
    if window["ambient_searches"] > window["opportunities"]:
        raise ValueError("rollout window ambient_searches exceeds opportunities")
    silence_reasons = window["silence_reasons"]
    if not isinstance(silence_reasons, Mapping) or len(silence_reasons) > 32:
        raise ValueError("rollout window silence_reasons is invalid")
    if any(
        not isinstance(code, str)
        or not code
        or len(code) > 64
        or type(count) is not int
        or count < 0
        for code, count in silence_reasons.items()
    ):
        raise ValueError("rollout window silence_reasons is invalid")
    window["silence_reasons"] = dict(sorted(silence_reasons.items()))
    return window


def evaluate_canary_rollout(
    baseline: Mapping[str, object],
    canary: Mapping[str, object],
) -> dict[str, object]:
    """Evaluate anonymous seven-day baseline and one-day canary aggregates."""

    before = _validated_rollout_window(baseline)
    after = _validated_rollout_window(canary)
    baseline_participation = _ratio(
        int(before["ambient_actions"]), int(before["opportunities"])
    )
    canary_participation = _ratio(
        int(after["ambient_actions"]), int(after["opportunities"])
    )
    absolute_change = canary_participation - baseline_participation
    relative_change = (
        None
        if baseline_participation == 0
        else absolute_change / baseline_participation
    )

    hazard_codes = (
        ("unsupported_claims", "unsupported_claim"),
        ("stale_scene_sends", "stale_scene_send"),
        ("cross_group_leaks", "cross_group_leak"),
        ("nonknowledge_ambient_searches", "nonknowledge_ambient_search"),
        ("provider_quota_anomalies", "provider_quota_anomaly"),
    )
    rollback_reasons = [
        code for field, code in hazard_codes if int(after[field]) > 0
    ]
    if relative_change is not None and relative_change > 0.10:
        rollback_reasons.append("participation_relative_increase")
    if absolute_change > 0.02:
        rollback_reasons.append("participation_absolute_increase")

    hold_reasons = []
    if float(before["observation_hours"]) < 168:
        hold_reasons.append("baseline_window_incomplete")
    if float(after["observation_hours"]) < 24:
        hold_reasons.append("canary_window_incomplete")
    if int(before["opportunities"]) == 0:
        hold_reasons.append("baseline_opportunities_empty")
    if int(after["opportunities"]) == 0:
        hold_reasons.append("canary_opportunities_empty")

    if rollback_reasons:
        decision = "rollback"
        reason_codes = tuple(rollback_reasons)
    elif hold_reasons:
        decision = "hold"
        reason_codes = tuple(hold_reasons)
    else:
        decision = "expand"
        reason_codes = ()
    return {
        "decision": decision,
        "reason_codes": reason_codes,
        "comparison": {
            "participation_rate": {
                "baseline": baseline_participation,
                "canary": canary_participation,
                "absolute_change": absolute_change,
                "relative_change": relative_change,
            },
            "ambient_search_rate": {
                "baseline": _ratio(
                    int(before["ambient_searches"]),
                    int(before["opportunities"]),
                ),
                "canary": _ratio(
                    int(after["ambient_searches"]),
                    int(after["opportunities"]),
                ),
            },
            "p95_latency_ms": {
                "baseline": float(before["p95_latency_ms"]),
                "canary": float(after["p95_latency_ms"]),
            },
            "silence_reasons": {
                "baseline": before["silence_reasons"],
                "canary": after["silence_reasons"],
            },
        },
    }


def game_knowledge_metrics(
    records: Iterable[Mapping[str, object]],
) -> dict[str, float | int]:
    normalized = tuple(_validated_record(value) for value in records)
    high_confidence = tuple(
        record for record in normalized if record["high_confidence_merge"]
    )
    temporal = tuple(
        record for record in normalized if record["temporal_need_expected"]
    )
    return {
        "understanding_accuracy": _ratio(
            sum(bool(record["understanding_correct"]) for record in normalized),
            len(normalized),
        ),
        "high_confidence_wrong_merge_rate": _ratio(
            sum(not bool(record["merge_correct"]) for record in high_confidence),
            len(high_confidence),
        ),
        "cross_group_leaks": sum(
            bool(record["cross_group_leak"]) for record in normalized
        ),
        "bot_promotions": sum(
            record["promoted_origin"]
            in {OriginClass.OWN_OUTPUT.value, OriginClass.EXTERNAL_BOT.value}
            for record in normalized
        ),
        "command_promotions": sum(
            record["promoted_origin"] == OriginClass.COMMAND.value
            for record in normalized
        ),
        "temporal_need_recall": _ratio(
            sum(bool(record["temporal_need_detected"]) for record in temporal),
            len(temporal),
        ),
    }


def run_frozen_game_knowledge_corpus(
    cases: Iterable[Mapping[str, object]],
    *,
    resolver: object,
    assessor: object,
    safety_records: Iterable[Mapping[str, object]],
) -> dict[str, object]:
    """Run anonymous frozen cases and return bounded release-gate evidence."""

    case_results = []
    for raw_case in cases:
        case = dict(raw_case)
        expected = dict(case.get("expected") or {})
        temporal_expected = (
            expected.get("need") == "fresh_evidence_required"
        )
        started = perf_counter()
        error_type = None
        correct = False
        confidence = 0.0
        temporal_detected = False
        try:
            context = tuple(
                _corpus_event(
                    case,
                    f"{case['case_id']}:context:{index}",
                    text,
                )
                for index, text in enumerate(case.get("context") or ())
            )
            event = _corpus_event(case, str(case["case_id"]), case["text"])
            frame = resolver.resolve(
                event,
                context,
                str(case["group_id"]),
                int(case["occurred_at"]),
            )
            need = assessor.assess(frame, (), now=int(case["occurred_at"]))
            actual = {
                "games": list(frame.game_ids),
                "entities": [
                    item.entity_id
                    for item in frame.resolved_entities
                    if item.entity_type != "game"
                ],
                "terms": [item.term_id for item in frame.resolved_terms],
                "version": (
                    None
                    if frame.version_reference is None
                    else {
                        "game_id": frame.version_reference.game_id,
                        "relative_kind": frame.version_reference.relative_kind,
                        "disclosure_kind": (
                            frame.version_reference.disclosure_kind
                        ),
                    }
                ),
                "need": need.outcome.value,
                "ambiguity": any(
                    code.startswith("ambiguous_")
                    for code in frame.ambiguity_codes
                ),
            }
            correct = actual == expected
            confidence = float(frame.confidence)
            temporal_detected = (
                temporal_expected
                and need.outcome.value == "fresh_evidence_required"
            )
        except Exception as error:  # release runner records, then continues
            error_type = type(error).__name__[:80]
        latency_ms = max(0, round((perf_counter() - started) * 1000, 3))
        case_results.append(
            {
                "case_id": str(case.get("case_id") or "")[:128],
                "correct": correct,
                "high_confidence": confidence >= 0.95,
                "latency_ms": latency_ms,
                "error_type": error_type,
                "temporal_need_expected": temporal_expected,
                "temporal_need_detected": temporal_detected,
            }
        )
    safety_metrics = game_knowledge_metrics(safety_records)
    high_confidence = tuple(
        item for item in case_results if item["high_confidence"]
    )
    temporal = tuple(
        item for item in case_results if item["temporal_need_expected"]
    )
    metrics = {
        "understanding_accuracy": _ratio(
            sum(bool(item["correct"]) for item in case_results),
            len(case_results),
        ),
        "high_confidence_wrong_merge_rate": _ratio(
            sum(not bool(item["correct"]) for item in high_confidence),
            len(high_confidence),
        ),
        "cross_group_leaks": safety_metrics["cross_group_leaks"],
        "bot_promotions": safety_metrics["bot_promotions"],
        "command_promotions": safety_metrics["command_promotions"],
        "temporal_need_recall": _ratio(
            sum(bool(item["temporal_need_detected"]) for item in temporal),
            len(temporal),
        ),
    }
    return {
        "metrics": metrics,
        "case_results": tuple(case_results),
    }


def _corpus_event(
    case: Mapping[str, object], event_id: str, text: object
) -> SocialEventEnvelope:
    occurred_at = int(case["occurred_at"])
    return SocialEventEnvelope.create(
        event_id=f"eval:{event_id}",
        event_type="platform.message",
        occurred_at=occurred_at,
        received_at=occurred_at,
        persona_id="groupmate:default",
        group_id=str(case["group_id"]),
        actor_id="anonymous-member",
        source_message_id=event_id,
        correlation_id=f"eval:{case['case_id']}",
        causation_id=None,
        payload={"text": str(text), "direct_address": False},
    )


__all__ = (
    "evaluate_canary_rollout",
    "game_knowledge_metrics",
    "run_frozen_game_knowledge_corpus",
)
