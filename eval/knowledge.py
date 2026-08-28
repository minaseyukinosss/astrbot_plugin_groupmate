"""Safety-focused metrics for game knowledge understanding runs."""

from __future__ import annotations

from typing import Iterable, Mapping

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


__all__ = ("game_knowledge_metrics",)
