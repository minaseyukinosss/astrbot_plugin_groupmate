"""Deterministic aggregate reports for export shadow alignment."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import Dict, Sequence

from eval.shadow_models import (
    BehaviorExample,
    ExportSummary,
    ReferenceLabel,
    ShadowProjection,
)


class PrivacyViolation(ValueError):
    pass


_SENSITIVE_KEYS = frozenset({
    "target_uin",
    "sender_uin",
    "sender_uid",
    "sender_name",
    "group_name",
    "text",
    "source_text",
    "response_text",
    "media_url",
    "filename",
    "md5",
})
_MEDIA_VALUE = re.compile(
    r"(?:https?://|download\?|fileid=|\bmd5\b|\.(?:png|jpe?g|gif|webp|mp4|mp3)\b)",
    re.I,
)
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_.:+-]+$")


def _increment_nested(target, left, right):
    target[left][right] += 1


def _sorted_nested(values):
    return {
        left: {right: inner[right] for right in sorted(inner)}
        for left, inner in sorted(values.items())
    }


def _sorted_three_level(values):
    return {
        left: {
            middle: {right: inner[right] for right in sorted(inner)}
            for middle, inner in sorted(middles.items())
        }
        for left, middles in sorted(values.items())
    }


def _run_bucket(value):
    if value <= 1:
        return "1"
    if value == 2:
        return "2"
    return "3+"


def _char_bucket(value):
    if value <= 0:
        return "0"
    if value <= 20:
        return "1-20"
    if value <= 60:
        return "21-60"
    if value <= 120:
        return "61-120"
    return "121+"


def _latency_bucket(value_ms):
    if value_ms < 2000:
        return "0-2s"
    if value_ms < 5000:
        return "2-5s"
    if value_ms < 15000:
        return "5-15s"
    if value_ms < 60000:
        return "15-60s"
    return "60s+"


def _ordered_counts(keys, values):
    return {key: int(values.get(key, 0)) for key in keys}


def build_diff_report(
    summary: ExportSummary,
    examples: Sequence[BehaviorExample],
    labels: Mapping[str, ReferenceLabel],
    projections: Mapping[str, ShadowProjection],
    *,
    review_count: int,
    configuration: Mapping[str, object],
) -> Dict[str, object]:
    aligned = tuple(
        (item, labels[item.sample_id], projections[item.sample_id])
        for item in examples
        if item.sample_id in labels and item.sample_id in projections
    )
    reply = defaultdict(int)
    scenes = defaultdict(lambda: defaultdict(int))
    acts = defaultdict(lambda: defaultdict(int))
    quote = defaultdict(int)
    quote_by_scene = defaultdict(lambda: defaultdict(int))
    media = defaultdict(int)
    media_by_scene_act = defaultdict(
        lambda: defaultdict(lambda: defaultdict(int))
    )
    run_messages = defaultdict(int)
    run_chars = defaultdict(int)
    run_latency = defaultdict(int)
    violations = defaultdict(int)
    mismatch = defaultdict(list)

    for example, reference, projected in aligned:
        target_state = "reply" if example.observed_replied else "silence"
        projected_state = "reply" if projected.would_reply else "silence"
        reply["target_{}_projected_{}".format(target_state, projected_state)] += 1
        _increment_nested(
            scenes, reference.scene.value, projected.scene.value
        )
        if example.observed_replied != projected.would_reply:
            mismatch["reply"].append(example.sample_id)
        if reference.scene is not projected.scene:
            mismatch["scene"].append(example.sample_id)

        both_reply = example.observed_replied and projected.would_reply
        if both_reply and reference.act is not None and projected.act is not None:
            _increment_nested(acts, reference.act.value, projected.act.value)
            if reference.act is not projected.act:
                mismatch["act"].append(example.sample_id)
        if both_reply and example.response_run is not None:
            observed_quote = example.response_run.quoted
            quote_key = "target_{}_projected_{}".format(
                "quote" if observed_quote else "unquoted",
                "quote" if projected.quote_allowed else "unquoted",
            )
            quote[quote_key] += 1
            quote_by_scene[reference.scene.value][quote_key] += 1
            if observed_quote != projected.quote_allowed:
                mismatch["quote"].append(example.sample_id)

            observed_media = example.response_run.has_media
            projected_media = bool(
                projected.decorative_media_allowed
                or projected.capability_media_allowed
            )
            media_key = "target_{}_projected_{}".format(
                "media" if observed_media else "text_only",
                "media" if projected_media else "text_only",
            )
            media[media_key] += 1
            reference_act = (
                reference.act.value if reference.act is not None else "none"
            )
            conditional_media = media_by_scene_act[reference.scene.value][
                reference_act
            ]
            conditional_media[media_key] += 1
            media["projected_decorative_eligible"] += int(
                projected.decorative_media_allowed
            )
            conditional_media["projected_decorative_eligible"] += int(
                projected.decorative_media_allowed
            )
            media["projected_capability_eligible"] += int(
                projected.capability_media_allowed
            )
            conditional_media["projected_capability_eligible"] += int(
                projected.capability_media_allowed
            )
            if observed_media != projected_media:
                mismatch["media"].append(example.sample_id)

        if example.observed_replied and example.response_run is not None:
            run = example.response_run
            run_messages[_run_bucket(run.message_count)] += 1
            run_chars[_char_bucket(run.reply_chars)] += 1
            latency = run.events[0].timestamp_ms - example.source.timestamp_ms
            run_latency[_latency_bucket(max(0, latency))] += 1

        projected_media = bool(
            projected.decorative_media_allowed
            or projected.capability_media_allowed
        )
        checks = {
            "boundary_media": bool(
                projected.act is not None
                and projected.act.value == "boundary"
                and projected_media
            ),
            "ambiguous_media": projected.ambiguous_target and projected_media,
            "false_completion_eligibility": projected.completion_claim_allowed,
            "multiple_owner": projected.owner_count != 1,
        }
        for category, failed in checks.items():
            if failed:
                violations[category] += 1
                mismatch[category].append(example.sample_id)

    reply_keys = (
        "target_reply_projected_reply",
        "target_reply_projected_silence",
        "target_silence_projected_reply",
        "target_silence_projected_silence",
    )
    violation_keys = (
        "ambiguous_media",
        "boundary_media",
        "false_completion_eligibility",
        "multiple_owner",
    )
    mismatch_keys = (
        "act",
        "ambiguous_media",
        "boundary_media",
        "false_completion_eligibility",
        "media",
        "multiple_owner",
        "quote",
        "reply",
        "scene",
    )
    return {
        "schema_version": 1,
        "configuration": dict(sorted(configuration.items())),
        "counts": {
            "manifest_records": summary.manifest_records,
            "observed_records": summary.observed_records,
            "target_records": summary.target_records,
            "excluded_system": summary.excluded_system,
            "excluded_recalled": summary.excluded_recalled,
            "excluded_content_ineligible": (
                summary.excluded_content_ineligible
            ),
            "duplicate_records": summary.duplicate_records,
            "chunks": summary.chunk_count,
            "examples": len(examples),
            "linked": sum(item.observed_replied for item in examples),
            "silence": sum(
                not item.observed_replied and not item.covered_context
                for item in examples
            ),
            "covered": sum(item.covered_context for item in examples),
            "high_confidence": len(aligned),
            "review": int(review_count),
        },
        "reply_confusion": _ordered_counts(reply_keys, reply),
        "scene_confusion": _sorted_nested(scenes),
        "act_confusion": _sorted_nested(acts),
        "quote": dict(
            sorted(quote.items()),
            by_scene=_sorted_nested(quote_by_scene),
        ),
        "media": dict(
            sorted(media.items()),
            by_scene_act=_sorted_three_level(media_by_scene_act),
        ),
        "run_diagnostics": {
            "message_count": _ordered_counts(("1", "2", "3+"), run_messages),
            "reply_chars": _ordered_counts(
                ("0", "1-20", "21-60", "61-120", "121+"), run_chars
            ),
            "latency": _ordered_counts(
                ("0-2s", "2-5s", "5-15s", "15-60s", "60s+"),
                run_latency,
            ),
        },
        "violations": _ordered_counts(violation_keys, violations),
        "mismatches": {
            key: sorted(set(mismatch.get(key, ()))) for key in mismatch_keys
        },
    }


def assert_shareable_report(
    report: Mapping[str, object],
    forbidden_identifiers: Sequence[str],
    forbidden_texts: Sequence[str],
) -> None:
    identifiers = frozenset(value for value in forbidden_identifiers if value)
    raw_texts = tuple(value for value in forbidden_texts if len(value) >= 8)

    def check_string(value, path, is_key=False):
        if not is_key and value in identifiers:
            raise PrivacyViolation("export identifier at {}".format(path))
        if _MEDIA_VALUE.search(value):
            raise PrivacyViolation("media locator at {}".format(path))
        if any(raw in value for raw in raw_texts):
            raise PrivacyViolation("raw export text at {}".format(path))
        if not _SAFE_TOKEN.match(value):
            kind = "key" if is_key else "value"
            raise PrivacyViolation("unsafe report {} at {}".format(kind, path))

    def walk(value, path):
        if isinstance(value, Mapping):
            for key, child in value.items():
                if not isinstance(key, str):
                    raise PrivacyViolation("non-string report key at {}".format(path))
                if key in _SENSITIVE_KEYS:
                    raise PrivacyViolation(
                        "sensitive key at {}".format(path + (key,))
                    )
                check_string(key, path + (key,), is_key=True)
                walk(child, path + (key,))
        elif isinstance(value, (list, tuple)):
            for index, child in enumerate(value):
                walk(child, path + (str(index),))
        elif isinstance(value, str):
            check_string(value, path)
        elif value is not None and not isinstance(value, (bool, int, float)):
            raise PrivacyViolation("unsupported report value at {}".format(path))

    walk(report, ())


def write_json_report(report: Mapping[str, object], path: Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def render_markdown(report: Mapping[str, object]) -> str:
    titles = (
        ("configuration", "Configuration"),
        ("counts", "Counts"),
        ("reply_confusion", "Reply Confusion"),
        ("scene_confusion", "Scene Confusion"),
        ("act_confusion", "Act Confusion"),
        ("quote", "Quote"),
        ("media", "Media"),
        ("run_diagnostics", "Run Diagnostics"),
        ("violations", "Violations"),
        ("mismatches", "Mismatches"),
    )
    lines = ["# Export Shadow Alignment"]
    for key, title in titles:
        lines.extend((
            "",
            "## " + title,
            "",
            "```json",
            json.dumps(report[key], ensure_ascii=False, indent=2, sort_keys=True),
            "```",
        ))
    return "\n".join(lines) + "\n"


def write_markdown_report(report: Mapping[str, object], path: Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_markdown(report), encoding="utf-8")
