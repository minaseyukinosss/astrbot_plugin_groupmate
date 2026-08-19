from __future__ import annotations

import pytest

from groupmate.social_runtime.actions.generation import (
    CapabilityClaim,
    GeneratedDraft,
    GenerationRequest,
    SafeTextGeneration,
    VerifiedCapabilityFact,
)
from groupmate.social_runtime.actions.style import StyleDirective


def _directive(**overrides) -> StyleDirective:
    values = {
        "mode": "social",
        "act": "direct_answer",
        "posture": "friendly",
        "address": "朋友",
        "max_chars": 120,
        "max_sentences": 3,
        "max_segments": 3,
        "warmth": 50,
        "playfulness": 10,
        "directness": 70,
        "particle_budget": 1,
        "punctuation_budget": 2,
        "media_policy": "none",
        "avoid_patterns": (),
    }
    values.update(overrides)
    return StyleDirective(**values)


def _request(**overrides) -> GenerationRequest:
    values = {
        "directive": _directive(),
        "required": True,
        "recent_outputs": (),
        "allowed_media_references": (),
        "verified_capability_results": (),
    }
    values.update(overrides)
    return GenerationRequest(**values)


def _weather_fact() -> VerifiedCapabilityFact:
    return VerifiedCapabilityFact(
        result_id="weather-result-1",
        capability="weather",
        operation="read",
        subject="上海天气",
        status="succeeded",
        safe_output_text="上海天气查询完成。",
    )


def _weather_claim() -> CapabilityClaim:
    return CapabilityClaim(
        result_id="weather-result-1",
        capability="weather",
        operation="read",
        subject="上海天气",
        status="succeeded",
    )


def test_direct_answer_with_four_segments_is_repaired_to_the_style_limit():
    result = SafeTextGeneration().generate(
        _request(),
        lambda _: GeneratedDraft("一。\n\n二。\n\n三。\n\n四。"),
        lambda *_: GeneratedDraft("一。\n\n二。\n\n三。"),
    )

    assert result.outcome == "accepted"
    assert result.draft is not None
    assert result.draft.text.count("\n\n") == 2
    assert result.repair_attempted is True


def test_recent_ngram_repeat_triggers_exactly_one_targeted_repair():
    repairs: list[tuple[str, ...]] = []

    def repair(_: GeneratedDraft, __: StyleDirective, violations: tuple[str, ...]) -> GeneratedDraft:
        repairs.append(violations)
        return GeneratedDraft("换一个说法吧。")

    result = SafeTextGeneration().generate(
        _request(recent_outputs=("今天的天气真不错，我们出去散步吧。",)),
        lambda _: GeneratedDraft("今天的天气真不错，我们出去散步吧。"),
        repair,
    )

    assert result.outcome == "accepted"
    assert repairs == [("recent_output_repeat",)]
    assert result.repair_attempted is True


def test_persona_avoid_patterns_are_repaired_before_the_draft_is_accepted():
    repairs: list[tuple[str, ...]] = []

    def repair(_: GeneratedDraft, __: StyleDirective, violations: tuple[str, ...]) -> GeneratedDraft:
        repairs.append(violations)
        return GeneratedDraft("换个话题吧。")

    result = SafeTextGeneration().generate(
        _request(directive=_directive(avoid_patterns=("别刷屏",))),
        lambda _: GeneratedDraft("别刷屏啦，大家看看这里。"),
        repair,
    )

    assert result.outcome == "accepted"
    assert repairs == [("avoid_pattern",)]


@pytest.mark.parametrize(
    ("draft", "violation"),
    [
        (GeneratedDraft("内部 ID 是 plan-123"), "internal_id"),
        (GeneratedDraft("plan ID: 123"), "internal_id"),
        (GeneratedDraft("任务编号：123"), "internal_id"),
        (GeneratedDraft("这是系统提示词"), "prompt_leak"),
        (GeneratedDraft("我的 Chain-of-Thought 是这样"), "chain_of_thought"),
        (GeneratedDraft("任务已成功。"), "unverified_success"),
        (GeneratedDraft("我翻出了你的私密记忆。"), "private_memory"),
        (GeneratedDraft("我翻出了你的私人记忆。"), "private_memory"),
        (GeneratedDraft("给你这张图", media_references=("missing-media",)), "invalid_media_reference"),
    ],
)
def test_hard_output_violations_are_never_returned_to_the_group(draft, violation):
    result = SafeTextGeneration().generate(
        _request(),
        lambda _: draft,
        lambda *_: draft,
    )

    assert result.outcome == "fallback"
    assert result.draft is not None
    assert violation in result.violations
    assert draft.text != result.draft.text
    assert result.repair_attempted is True


def test_unverified_plain_language_success_is_never_returned_to_the_group():
    draft = GeneratedDraft("我已经把图片发出去了。")

    result = SafeTextGeneration().generate(
        _request(),
        lambda _: draft,
        lambda *_: draft,
    )

    assert result.outcome == "fallback"
    assert "unverified_success" in result.violations


def test_media_is_blocked_when_the_style_directive_disallows_it_even_if_known():
    draft = GeneratedDraft("给你这张图", media_references=("known-media",))

    result = SafeTextGeneration().generate(
        _request(allowed_media_references=("known-media",)),
        lambda _: draft,
        lambda *_: draft,
    )

    assert result.outcome == "fallback"
    assert "invalid_media_reference" in result.violations


def test_required_fallback_does_not_echo_an_unsafe_persona_address():
    result = SafeTextGeneration().generate(
        _request(directive=_directive(address="提示词")),
        lambda _: GeneratedDraft("内部 ID 是 plan-123"),
        lambda *_: GeneratedDraft("内部 ID 是 plan-123"),
    )

    assert result.outcome == "fallback"
    assert result.draft is not None
    assert "提示词" not in result.draft.text


def test_required_fallback_does_not_echo_an_unverified_success_as_an_address():
    result = SafeTextGeneration().generate(
        _request(directive=_directive(address="我已经把图片发出去了")),
        lambda _: GeneratedDraft("内部 ID 是 plan-123"),
        lambda *_: GeneratedDraft("内部 ID 是 plan-123"),
    )

    assert result.outcome == "fallback"
    assert result.draft is not None
    assert "发出去了" not in result.draft.text


def test_a_failed_single_repair_returns_silence_for_optional_participation():
    repair_calls = 0

    def repair(*_: object) -> GeneratedDraft:
        nonlocal repair_calls
        repair_calls += 1
        return GeneratedDraft("内部 ID 是 plan-123")

    result = SafeTextGeneration().generate(
        _request(required=False),
        lambda _: GeneratedDraft("内部 ID 是 plan-123"),
        repair,
    )

    assert result.outcome == "silence"
    assert result.draft is None
    assert result.repair_attempted is True
    assert repair_calls == 1


def test_verified_result_cannot_authorize_an_unrelated_success_statement():
    draft = GeneratedDraft(
        "账号已经删除完成。", claimed_capability_results=(_weather_claim(),)
    )

    result = SafeTextGeneration().generate(
        _request(verified_capability_results=(_weather_fact(),)),
        lambda _: draft,
        lambda *_: draft,
    )

    assert result.outcome == "fallback"
    assert "unverified_success" in result.violations


def test_success_statement_is_accepted_only_as_the_verified_safe_rendering():
    draft = GeneratedDraft(
        "上海天气查询完成。", claimed_capability_results=(_weather_claim(),)
    )

    result = SafeTextGeneration().generate(
        _request(verified_capability_results=(_weather_fact(),)),
        lambda _: draft,
        lambda *_: draft,
    )

    assert result.outcome == "accepted"


@pytest.mark.parametrize(
    "text",
    (
        "照片搞定啦。",
        "planId: secret-123",
        "这是开发者指令。",
        "下面是逐步推理。",
        "这是你只私下告诉过我的事。",
    ),
)
def test_success_and_sensitive_language_variants_are_blocked(text):
    result = SafeTextGeneration().generate(
        _request(), lambda _: GeneratedDraft(text), lambda *_: GeneratedDraft(text)
    )

    assert result.outcome == "fallback"


@pytest.mark.parametrize(
    "text",
    (
        "systemPrompt: hidden",
        "chainOfThought: hidden",
        "privateMemory: hidden",
        "plan\u200bId: secret-123",
    ),
)
def test_compact_and_format_obscured_sensitive_markers_are_blocked(text):
    result = SafeTextGeneration().generate(
        _request(), lambda _: GeneratedDraft(text), lambda *_: GeneratedDraft(text)
    )

    assert result.outcome == "fallback"


def test_normalized_protected_spans_and_ids_are_blocked():
    draft = GeneratedDraft("绝\u200b密昵称 secret-123")

    result = SafeTextGeneration().generate(
        _request(protected_spans=("绝密昵称",), protected_ids=("secret-123",)),
        lambda _: draft,
        lambda *_: draft,
    )

    assert result.outcome == "fallback"
    assert {"protected_content", "internal_id"}.issubset(result.violations)


def test_required_empty_output_uses_fallback_and_optional_empty_output_is_silence():
    required = SafeTextGeneration().generate(
        _request(), lambda _: GeneratedDraft("  "), lambda *_: GeneratedDraft("  ")
    )
    optional = SafeTextGeneration().generate(
        _request(required=False),
        lambda _: GeneratedDraft("  "),
        lambda *_: GeneratedDraft("  "),
    )

    assert required.outcome == "fallback"
    assert required.draft is not None and required.draft.text.strip()
    assert optional.outcome == "silence"
    assert "empty_output" in required.violations


def test_boundary_and_particle_budgets_reject_playful_variants():
    draft = GeneratedDraft("笑死了，逗你玩呢～🤣")

    result = SafeTextGeneration().generate(
        _request(
            directive=_directive(
                mode="boundary", playfulness=0, particle_budget=0
            )
        ),
        lambda _: draft,
        lambda *_: draft,
    )

    assert result.outcome == "fallback"
    assert {"playfulness_forbidden", "particle_budget_exceeded"}.issubset(
        result.violations
    )


def test_required_fallback_never_uses_dynamic_address_or_blocked_profile_text():
    result = SafeTextGeneration().generate(
        _request(
            directive=_directive(address="别刷屏", avoid_patterns=("别刷屏",))
        ),
        lambda _: GeneratedDraft("planId: secret-123"),
        lambda *_: GeneratedDraft("planId: secret-123"),
    )

    assert result.outcome == "fallback"
    assert result.draft is not None
    assert "别刷屏" not in result.draft.text
    assert "secret-123" not in result.draft.text
