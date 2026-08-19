"""Provider-independent generation and deterministic output firewall."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Literal

from .style import StyleDirective


@dataclass(frozen=True)
class GeneratedDraft:
    text: str
    media_references: tuple[str, ...] = ()
    claimed_capability_results: tuple[str, ...] = ()


@dataclass(frozen=True)
class GenerationRequest:
    directive: StyleDirective
    required: bool
    recent_outputs: tuple[str, ...]
    allowed_media_references: tuple[str, ...]
    verified_capability_results: tuple[str, ...]


@dataclass(frozen=True)
class FirewallReview:
    accepted: bool
    stage: Literal["safety", "consistency", "style", "repeat"] | None
    violations: tuple[str, ...]


@dataclass(frozen=True)
class GenerationResult:
    outcome: Literal["accepted", "fallback", "silence"]
    draft: GeneratedDraft | None
    violations: tuple[str, ...]
    repair_attempted: bool


DraftGenerator = Callable[[StyleDirective], GeneratedDraft]
DraftRepairer = Callable[[GeneratedDraft, StyleDirective, tuple[str, ...]], GeneratedDraft]


class OutputFirewall:
    """Checks every candidate in the mandated order without granting authority."""

    _INTERNAL_ID = re.compile(
        r"\b(?:plan|correlation|persona|group|event|task|node)[_-][A-Za-z0-9][A-Za-z0-9_-]*\b"
        r"|\b(?:plan|correlation|persona|group|event|task|node)\s+(?:id|identifier)\s*[:：= -]?\s*[A-Za-z0-9][A-Za-z0-9_-]*\b"
        r"|(?:计划|关联|人格|群组|事件|任务|节点)\s*(?:id|编号)\s*[:：= -]?\s*[A-Za-z0-9][A-Za-z0-9_-]*\b",
        re.IGNORECASE,
    )
    _SUCCESS_CLAIM = re.compile(
        r"(?:任务|操作|请求).{0,4}(?:已)?(?:成功|完成)"
        r"|(?:我|我们)?(?:已经|已).{0,24}(?:发出去了|发送(?:完成|成功|好了)?|完成了|做好了)"
        r"|\b(?:task|operation|request)\s+(?:has\s+)?(?:succeeded|completed|sent)\b"
        r"|\b(?:i|we)\s+(?:have\s+)?(?:already\s+)?(?:sent|completed|finished|uploaded|created)\b",
        re.IGNORECASE,
    )

    def review(self, draft: GeneratedDraft, request: GenerationRequest) -> FirewallReview:
        safety = self._safety_violations(draft.text)
        if safety:
            return FirewallReview(False, "safety", safety)
        consistency = self._consistency_violations(draft, request)
        if consistency:
            return FirewallReview(False, "consistency", consistency)
        style = self._style_violations(draft.text, request.directive)
        if style:
            return FirewallReview(False, "style", style)
        if self._repeats_recent_output(draft.text, request.recent_outputs):
            return FirewallReview(False, "repeat", ("recent_output_repeat",))
        return FirewallReview(True, None, ())

    def _safety_violations(self, text: str) -> tuple[str, ...]:
        folded = text.casefold()
        violations: list[str] = []
        if self._INTERNAL_ID.search(text) or "内部 id" in folded or "internal id" in folded:
            violations.append("internal_id")
        if any(value in folded for value in ("system prompt", "提示词", "系统提示")):
            violations.append("prompt_leak")
        if any(value in folded for value in ("chain-of-thought", "chain of thought", "思维链", "推理过程")):
            violations.append("chain_of_thought")
        if any(
            value in folded
            for value in ("private memory", "私密记忆", "隐私记忆", "私人记忆")
        ):
            violations.append("private_memory")
        return tuple(violations)

    def _consistency_violations(
        self, draft: GeneratedDraft, request: GenerationRequest
    ) -> tuple[str, ...]:
        violations: list[str] = []
        verified = set(request.verified_capability_results)
        unverified_claims = set(draft.claimed_capability_results) - verified
        if unverified_claims or (
            self._SUCCESS_CLAIM.search(draft.text)
            and (not draft.claimed_capability_results or unverified_claims)
        ):
            violations.append("unverified_success")
        allowed = set(request.allowed_media_references)
        if draft.media_references and (
            request.directive.media_policy != "registered_only"
            or not set(draft.media_references).issubset(allowed)
        ):
            violations.append("invalid_media_reference")
        return tuple(violations)

    @staticmethod
    def _style_violations(text: str, directive: StyleDirective) -> tuple[str, ...]:
        violations: list[str] = []
        if len(text) > directive.max_chars:
            violations.append("max_chars_exceeded")
        segments = [segment for segment in re.split(r"\n\s*\n", text.strip()) if segment]
        if len(segments) > directive.max_segments:
            violations.append("max_segments_exceeded")
        sentences = len(re.findall(r"[。！？.!?]+", text))
        if text.strip() and not sentences:
            sentences = 1
        if sentences > directive.max_sentences:
            violations.append("max_sentences_exceeded")
        if len(re.findall(r"[!！?？]", text)) > directive.punctuation_budget:
            violations.append("punctuation_budget_exceeded")
        if directive.playfulness == 0 and any(token in text for token in ("哈哈", "嘿嘿", "😜")):
            violations.append("playfulness_forbidden")
        folded = text.casefold()
        if any(pattern.casefold() in folded for pattern in directive.avoid_patterns if pattern.strip()):
            violations.append("avoid_pattern")
        return tuple(violations)

    @staticmethod
    def _repeats_recent_output(text: str, recent_outputs: tuple[str, ...]) -> bool:
        candidate = OutputFirewall._ngrams(text)
        return bool(candidate) and any(candidate & OutputFirewall._ngrams(old) for old in recent_outputs)

    @staticmethod
    def _ngrams(text: str) -> set[tuple[str, ...]]:
        words = re.findall(r"[\w']+", text.casefold())
        if len(words) >= 3:
            return {tuple(words[index : index + 3]) for index in range(len(words) - 2)}
        compact = re.sub(r"\s+", "", text.casefold())
        if len(compact) >= 6:
            return {tuple(compact[index : index + 6]) for index in range(len(compact) - 5)}
        return set()


class SafeTextGeneration:
    """Runs one generation and at most one directed repair before a safe outcome."""

    def __init__(self, firewall: OutputFirewall | None = None) -> None:
        self._firewall = firewall or OutputFirewall()

    def generate(
        self,
        request: GenerationRequest,
        generator: DraftGenerator,
        repairer: DraftRepairer,
    ) -> GenerationResult:
        try:
            draft = generator(request.directive)
        except Exception:
            return self._failed(request, ("generation_failed",), repair_attempted=False)
        review = self._firewall.review(draft, request)
        if review.accepted:
            return GenerationResult("accepted", draft, (), False)
        try:
            repaired = repairer(draft, request.directive, review.violations)
        except Exception:
            return self._failed(request, review.violations, repair_attempted=True)
        repaired_review = self._firewall.review(repaired, request)
        if repaired_review.accepted:
            return GenerationResult("accepted", repaired, (), True)
        return self._failed(request, repaired_review.violations, repair_attempted=True)

    def _failed(
        self,
        request: GenerationRequest,
        violations: tuple[str, ...],
        *,
        repair_attempted: bool,
    ) -> GenerationResult:
        if not request.required:
            return GenerationResult("silence", None, violations, repair_attempted)
        fallback = GeneratedDraft(self._fallback_text(request))
        return GenerationResult("fallback", fallback, violations, repair_attempted)

    def _fallback_text(self, request: GenerationRequest) -> str:
        """Keep the required fallback deterministic without echoing unsafe profile text."""

        directive = request.directive
        address = directive.address or ""
        prefix = ""
        address_draft = GeneratedDraft(address)
        if (
            address
            and not self._firewall._safety_violations(address)
            and not self._firewall._consistency_violations(address_draft, request)
        ):
            candidate = f"{address}，"
            if len(candidate) < directive.max_chars:
                prefix = candidate
        ending = "。" if directive.punctuation_budget else ""
        body = f"我暂时不展开{ending}"
        return (prefix + body)[: directive.max_chars]


__all__ = (
    "DraftGenerator",
    "DraftRepairer",
    "FirewallReview",
    "GeneratedDraft",
    "GenerationRequest",
    "GenerationResult",
    "OutputFirewall",
    "SafeTextGeneration",
)
