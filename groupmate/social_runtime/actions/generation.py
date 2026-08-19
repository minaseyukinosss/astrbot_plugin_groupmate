"""Provider-independent generation and deterministic output firewall."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Callable, Literal

from .style import StyleDirective


@dataclass(frozen=True)
class CapabilityClaim:
    result_id: str
    capability: str
    operation: str
    subject: str
    status: str

    def identity(self) -> tuple[str, str, str, str, str]:
        return (
            self.result_id,
            self.capability,
            self.operation,
            self.subject,
            self.status,
        )


@dataclass(frozen=True)
class VerifiedCapabilityFact:
    result_id: str
    capability: str
    operation: str
    subject: str
    status: str
    safe_output_text: str

    def identity(self) -> tuple[str, str, str, str, str]:
        return (
            self.result_id,
            self.capability,
            self.operation,
            self.subject,
            self.status,
        )


@dataclass(frozen=True)
class GeneratedDraft:
    text: str
    media_references: tuple[str, ...] = ()
    claimed_capability_results: tuple[CapabilityClaim, ...] = ()


@dataclass(frozen=True)
class GenerationRequest:
    directive: StyleDirective
    required: bool
    recent_outputs: tuple[str, ...]
    allowed_media_references: tuple[str, ...]
    verified_capability_results: tuple[VerifiedCapabilityFact, ...]
    protected_spans: tuple[str, ...] = ()
    protected_ids: tuple[str, ...] = ()


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
        r"|\b(?:plan|correlation|persona|group|event|task|node)(?:id|identifier)\s*[:：= -]?\s*[A-Za-z0-9][A-Za-z0-9_-]*\b"
        r"|\b(?:plan|correlation|persona|group|event|task|node)\s+(?:id|identifier)\s*[:：= -]?\s*[A-Za-z0-9][A-Za-z0-9_-]*\b"
        r"|(?:计划|关联|人格|群组|事件|任务|节点)\s*(?:id|编号)\s*[:：= -]?\s*[A-Za-z0-9][A-Za-z0-9_-]*\b",
        re.IGNORECASE,
    )
    _SUCCESS_CLAIM = re.compile(
        r"(?:任务|操作|请求).{0,4}(?:已)?(?:成功|完成)"
        r"|(?:我|我们)?(?:已经|已).{0,24}(?:发出去了|发送(?:完成|成功|好了)?|完成了|做好了)"
        r"|(?:照片|图片|文件|账号|账户|任务|操作|请求).{0,12}(?:搞定|弄好|处理好|办好|删除完成|上传完成|创建完成|发出去了)"
        r"|\b(?:task|operation|request)\s+(?:has\s+)?(?:succeeded|completed|sent)\b"
        r"|\b(?:i|we)\s+(?:have\s+)?(?:already\s+)?(?:sent|completed|finished|uploaded|created|deleted|updated)\b",
        re.IGNORECASE,
    )
    _PLAYFUL = re.compile(r"哈哈|嘿嘿|笑死|逗你|开玩笑|[🤣😂😜😏]|[～~]")
    _PARTICLE = re.compile(r"啦|呀|嘛|哟|哦|诶|呢|吧|哈|[～~]")

    def review(self, draft: GeneratedDraft, request: GenerationRequest) -> FirewallReview:
        safety = self._safety_violations(draft.text, request)
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

    def _safety_violations(
        self, text: str, request: GenerationRequest
    ) -> tuple[str, ...]:
        folded = self._normalize(text)
        compact = re.sub(r"[\W_]+", "", folded)
        violations: list[str] = []
        compact_internal_id = re.search(
            r"(?:plan|correlation|persona|group|event|task|node)(?:id|identifier)[a-z0-9]",
            compact,
        )
        if (
            self._INTERNAL_ID.search(folded)
            or compact_internal_id
            or "内部 id" in folded
            or "internal id" in folded
        ):
            violations.append("internal_id")
        if any(
            value in folded
            for value in (
                "system prompt",
                "developer instruction",
                "developer message",
                "提示词",
                "系统提示",
                "开发者指令",
                "开发者消息",
            )
        ) or any(
            value in compact
            for value in ("systemprompt", "developerinstruction", "developermessage")
        ):
            violations.append("prompt_leak")
        if any(
            value in folded
            for value in (
                "chain-of-thought",
                "chain of thought",
                "reasoning steps",
                "思维链",
                "推理过程",
                "逐步推理",
                "逐步思考",
            )
        ) or any(
            value in compact for value in ("chainofthought", "reasoningsteps")
        ):
            violations.append("chain_of_thought")
        if any(
            value in folded
            for value in (
                "private memory",
                "confidential memory",
                "only told me privately",
                "私密记忆",
                "隐私记忆",
                "私人记忆",
                "只私下告诉",
                "私下告诉过我",
            )
        ) or any(
            value in compact
            for value in (
                "privatememory",
                "confidentialmemory",
                "onlytoldmeprivately",
            )
        ):
            violations.append("private_memory")
        protected = tuple(
            self._normalize(value)
            for value in request.protected_spans
            if self._normalize(value)
        )
        if any(value in folded for value in protected):
            violations.append("protected_content")
        protected_ids = tuple(
            self._normalize(value)
            for value in request.protected_ids
            if self._normalize(value)
        )
        if any(value in folded for value in protected_ids):
            self._append_once(violations, "internal_id")
        return tuple(violations)

    def _consistency_violations(
        self, draft: GeneratedDraft, request: GenerationRequest
    ) -> tuple[str, ...]:
        violations: list[str] = []
        facts = {
            fact.identity(): fact for fact in request.verified_capability_results
        }
        claims = draft.claimed_capability_results
        matched = tuple(facts.get(claim.identity()) for claim in claims)
        unverified_claims = any(fact is None for fact in matched)
        safe_rendering = (
            len(claims) == 1
            and not unverified_claims
            and matched[0] is not None
            and self._normalize(draft.text)
            == self._normalize(matched[0].safe_output_text)
        )
        if unverified_claims or (claims and not safe_rendering) or (
            self._SUCCESS_CLAIM.search(draft.text) and not safe_rendering
        ):
            violations.append("unverified_success")
        allowed = set(request.allowed_media_references)
        if draft.media_references and (
            request.directive.media_policy != "registered_only"
            or not set(draft.media_references).issubset(allowed)
        ):
            violations.append("invalid_media_reference")
        return tuple(violations)

    @classmethod
    def _style_violations(cls, text: str, directive: StyleDirective) -> tuple[str, ...]:
        violations: list[str] = []
        if not text.strip():
            violations.append("empty_output")
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
        if directive.playfulness == 0 and cls._PLAYFUL.search(text):
            violations.append("playfulness_forbidden")
        if len(cls._PARTICLE.findall(text)) > directive.particle_budget:
            violations.append("particle_budget_exceeded")
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

    @staticmethod
    def _normalize(text: str) -> str:
        normalized = unicodedata.normalize("NFKC", str(text))
        without_format = "".join(
            character
            for character in normalized
            if unicodedata.category(character) != "Cf"
        )
        return re.sub(r"\s+", " ", without_format).strip().casefold()

    @staticmethod
    def _append_once(values: list[str], value: str) -> None:
        if value not in values:
            values.append(value)


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
        """Return a fixed safe message without echoing any generated/profile data."""

        body = "暂时无法可靠回答"
        if request.directive.punctuation_budget:
            body += "。"
        return body[: request.directive.max_chars]


__all__ = (
    "CapabilityClaim",
    "DraftGenerator",
    "DraftRepairer",
    "FirewallReview",
    "GeneratedDraft",
    "GenerationRequest",
    "GenerationResult",
    "OutputFirewall",
    "SafeTextGeneration",
    "VerifiedCapabilityFact",
)
