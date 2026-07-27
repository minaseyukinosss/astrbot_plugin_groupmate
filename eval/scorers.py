"""Deterministic and optional LLM-based scoring for evaluation traces."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .providers import OpenAICompatibleClient, ProviderError
from .schema import CheckResult, EvaluationResult, Scenario


def guard_codes_from_reason(reason: str) -> Tuple[str, ...]:
    prefix = "guard_rejected:"
    if not (reason or "").startswith(prefix):
        return ()
    return tuple(
        item.strip()
        for item in reason[len(prefix) :].split(",")
        if item.strip()
    )


def _pattern_matches(pattern: str, text: str) -> bool:
    try:
        return bool(re.search(pattern, text, re.IGNORECASE))
    except re.error:
        return pattern.lower() in text.lower()


def _normalized(text: str) -> str:
    return re.sub(r"[\s，,。.!！?？~～]+", "", (text or "").lower())


def score_scenario(
    scenario: Scenario,
    *,
    trigger: str,
    sent: bool,
    outcome_reason: str,
    output_text: str,
    guard_codes: Sequence[str],
) -> Tuple[CheckResult, ...]:
    checks: List[CheckResult] = []
    expected = scenario.expected
    constraints = scenario.constraints
    if expected.trigger is not None:
        checks.append(
            CheckResult(
                "trigger",
                trigger == expected.trigger,
                expected.trigger,
                trigger,
            )
        )
    if expected.action is not None:
        actual_action = "sent" if sent else "silent"
        checks.append(
            CheckResult(
                "action",
                actual_action == expected.action,
                expected.action,
                actual_action,
            )
        )
    if expected.outcome_reason is not None:
        checks.append(
            CheckResult(
                "outcome_reason",
                outcome_reason == expected.outcome_reason,
                expected.outcome_reason,
                outcome_reason,
            )
        )
    if expected.guard_codes:
        actual_codes = set(guard_codes)
        missing = [item for item in expected.guard_codes if item not in actual_codes]
        checks.append(
            CheckResult(
                "guard_codes",
                not missing,
                list(expected.guard_codes),
                list(guard_codes),
            )
        )

    length = len(output_text)
    if sent:
        checks.append(
            CheckResult(
                "min_chars",
                length >= constraints.min_chars,
                constraints.min_chars,
                length,
            )
        )
        checks.append(
            CheckResult(
                "max_chars",
                length <= constraints.max_chars,
                constraints.max_chars,
                length,
            )
        )
        for pattern in constraints.required_patterns:
            checks.append(
                CheckResult(
                    "required_pattern:" + pattern,
                    _pattern_matches(pattern, output_text),
                    True,
                    _pattern_matches(pattern, output_text),
                )
            )
    for pattern in constraints.forbidden_patterns:
        checks.append(
            CheckResult(
                "forbidden_pattern:" + pattern,
                not _pattern_matches(pattern, output_text),
                False,
                _pattern_matches(pattern, output_text),
            )
        )

    internal_id_leak = bool(re.search(r"\b(?:u|g|m)\d+\b", output_text))
    checks.append(
        CheckResult(
            "visible_internal_id",
            not internal_id_leak,
            False,
            internal_id_leak,
        )
    )

    previous_outputs = [
        message.text for message in scenario.messages if message.is_bot and message.text
    ]
    if sent and previous_outputs:
        normalized = _normalized(output_text)
        ratios = [
            SequenceMatcher(None, normalized, _normalized(previous)).ratio()
            for previous in previous_outputs
            if _normalized(previous)
        ]
        highest = max(ratios) if ratios else 0.0
        checks.append(
            CheckResult(
                "conversation_repetition",
                highest < constraints.max_repeated_ratio,
                constraints.max_repeated_ratio,
                round(highest, 4),
            )
        )
    if scenario.category == "multi_turn":
        required_ok = all(
            _pattern_matches(pattern, output_text)
            for pattern in constraints.required_patterns
        )
        stale_ok = all(
            not _pattern_matches(pattern, output_text)
            for pattern in constraints.forbidden_patterns
        )
        checks.append(
            CheckResult(
                "conversation_context_retention",
                bool(output_text) and required_ok,
                True,
                bool(output_text) and required_ok,
            )
        )
        checks.append(
            CheckResult(
                "conversation_topic_continuity",
                stale_ok,
                True,
                stale_ok,
            )
        )
    return tuple(checks)


class LLMJudge:
    """Optional judge; deterministic checks remain the release source of truth."""

    def __init__(self, client: OpenAICompatibleClient) -> None:
        self.client = client
        rubric_path = Path(__file__).resolve().parent / "rubrics" / "persona_judge.md"
        self.rubric = rubric_path.read_text(encoding="utf-8")

    def judge(self, scenario: Scenario, output_text: str) -> Mapping[str, Any]:
        transcript = "\n".join(
            "{}: {}".format(message.sender_name, message.text or "[非文本消息]")
            for message in scenario.messages
        )
        prompt = "\n".join(
            [
                "场景说明：" + scenario.description,
                "群聊：",
                transcript,
                "待评回复：",
                output_text or "[沉默]",
                "",
                "只输出 JSON，不要代码块：",
                '{"naturalness":1,"role_adherence":1,"relevance":1,'
                '"context_retention":1,"ai_taste":false,"rationale":"一句话"}',
                "四项分数均为 1 到 5；ai_taste 表示是否有明显 AI/客服腔。",
            ]
        )
        raw = self.client.complete(
            system_prompt="你是群聊回复评测员。\n\n" + self.rubric,
            user_prompt=prompt,
            temperature=0.0,
            json_mode=True,
        )
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError:
            raise ProviderError("LLM judge returned invalid JSON")
        required = {
            "naturalness",
            "role_adherence",
            "relevance",
            "context_retention",
            "ai_taste",
            "rationale",
        }
        if set(payload) != required:
            raise ProviderError("LLM judge returned an unexpected schema")
        for key in (
            "naturalness",
            "role_adherence",
            "relevance",
            "context_retention",
        ):
            value = payload[key]
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 5:
                raise ProviderError("LLM judge score {} is invalid".format(key))
        if not isinstance(payload["ai_taste"], bool):
            raise ProviderError("LLM judge ai_taste must be boolean")
        if not isinstance(payload["rationale"], str):
            raise ProviderError("LLM judge rationale must be a string")
        return payload


def summarize_results(results: Sequence[EvaluationResult]) -> Dict[str, Any]:
    category_totals: Dict[str, int] = defaultdict(int)
    category_passed: Dict[str, int] = defaultdict(int)
    check_totals: Dict[str, int] = defaultdict(int)
    check_passed: Dict[str, int] = defaultdict(int)
    lengths = []
    latencies = []
    sent_count = 0
    errors = 0

    for result in results:
        category = result.category
        category_totals[str(category)] += 1
        if result.passed:
            category_passed[str(category)] += 1
        if result.error:
            errors += 1
        if result.sent:
            sent_count += 1
            lengths.append(len(result.output_text))
        latencies.append(result.latency_ms)
        for check in result.checks:
            check_totals[check.name] += 1
            if check.passed:
                check_passed[check.name] += 1

    total = len(results)
    return {
        "schema_version": 1,
        "total_runs": total,
        "passed_runs": sum(1 for result in results if result.passed),
        "pass_rate": (
            round(sum(1 for result in results if result.passed) / total, 4)
            if total
            else 0.0
        ),
        "errors": errors,
        "sent_runs": sent_count,
        "silence_rate": round((total - sent_count) / total, 4) if total else 0.0,
        "mean_output_chars": round(mean(lengths), 2) if lengths else 0.0,
        "mean_latency_ms": round(mean(latencies), 2) if latencies else 0.0,
        "categories": {
            category: {
                "total": category_totals[category],
                "passed": category_passed[category],
                "pass_rate": round(
                    category_passed[category] / category_totals[category], 4
                ),
            }
            for category in sorted(category_totals)
        },
        "checks": {
            name: {
                "total": check_totals[name],
                "passed": check_passed[name],
                "pass_rate": round(check_passed[name] / check_totals[name], 4),
            }
            for name in sorted(check_totals)
        },
    }
