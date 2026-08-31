"""Deterministic rendering and review boundaries for reply knowledge."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Mapping

from ..social_review import RealizedReply
from .contracts import (
    ClaimStatus,
    KnowledgeQualifier,
    KnowledgeSnapshot,
    RiskClass,
    StrictFactFragment,
    canonical_source_url,
)
from .seeds import load_bundled_seeds


_URL = re.compile(r"(?:https?://|www\.)", re.IGNORECASE)
_DIGIT = re.compile(r"\d")
_STRICT_STATUS_WORDS = (
    "版本",
    "前瞻",
    "上线",
    "发布",
    "更新",
    "官方",
    "爆料",
    "传闻",
    "测试服",
    "日期",
    "概率",
    "今天",
    "明天",
    "后天",
    "本周",
    "下周",
    "月底",
    "开服",
    "实装",
    "最新",
    "确定",
    "确认",
    "有消息",
)
# Retiring a seed removes its knowledge projections, not the strict renderer's
# injection guard. The game may still be learned from scoped group evidence.
_RETIRED_BUNDLED_SAFETY_TOKENS = ("原神", "Genshin", "Genshin Impact")
_UNSUPPORTED_GROUNDED_ASSERTION = re.compile(
    r"(?:https?://|www\.|\d|版本|前瞻|上线|发布|更新|测试服|卡池|日期|概率|"
    r"官方(?:确认|公布|表示)|爆料|传闻)",
    re.IGNORECASE,
)
_CLAUSE_BREAK = re.compile(r"[，,。！？!?；;\n]+")
_CLAIM_CHARACTER = re.compile(r"[\w\u3400-\u9fff]", re.UNICODE)


@dataclass(frozen=True)
class KnowledgeReview:
    accepted: bool
    violations: tuple[str, ...]


class KnowledgeFactRenderer:
    def __init__(self, *, source_urls: Mapping[str, str] | None = None) -> None:
        registry = {
            source.source_id: source.url
            for seed in load_bundled_seeds()
            for source in seed.official_sources
        }
        registry.update(dict(source_urls or {}))
        self.source_urls = registry

    def render(
        self, snapshot: KnowledgeSnapshot
    ) -> tuple[StrictFactFragment, ...]:
        if not isinstance(snapshot, KnowledgeSnapshot):
            raise ValueError("knowledge snapshot is invalid")
        frozen = {
            item.knowledge_id: item
            for item in snapshot.strict_fact_fragments
        }
        rendered = []
        for fact in snapshot.allowed_knowledge_facts:
            if fact.risk_class is RiskClass.STABLE_SEMANTIC:
                continue
            status = (
                fact.status.value
                if isinstance(fact.status, ClaimStatus)
                else str(fact.status)
            )
            if status == ClaimStatus.STALE.value:
                raise ValueError("stale knowledge fact cannot be rendered")
            original = frozen.get(fact.knowledge_id)
            if original is None:
                raise ValueError("strict knowledge fragment is missing")
            if status == ClaimStatus.DISPUTED.value:
                text = "现有来源信息相互冲突，暂时无法确认。"
            elif status != ClaimStatus.ACTIVE.value:
                raise ValueError("inactive knowledge fact cannot be rendered")
            else:
                text = self._qualified_text(fact.qualifier, fact.safe_summary)
                urls = self._source_urls(fact.source_ids, snapshot.source_ids)
                if urls:
                    text += " 来源：" + " ".join(urls)
            rendered.append(
                StrictFactFragment(
                    fragment_id=original.fragment_id,
                    knowledge_id=fact.knowledge_id,
                    text=text,
                    risk_class=fact.risk_class,
                )
            )
        return tuple(rendered)

    @staticmethod
    def _qualified_text(qualifier: KnowledgeQualifier, summary: str) -> str:
        value = KnowledgeQualifier(qualifier)
        if value is KnowledgeQualifier.OFFICIAL_PREVIEW:
            return "官方前瞻已公布：" + summary
        if value is KnowledgeQualifier.OFFICIAL_RELEASED:
            return "官方已确认上线：" + summary
        if value is KnowledgeQualifier.OFFICIAL:
            return "官方资料显示：" + summary
        if value is KnowledgeQualifier.RUMOR:
            return "非官方消息：" + summary + "（不能视为官方确认）"
        if value is KnowledgeQualifier.NEGATIVE_OFFICIAL:
            return "截至本次核验，官方来源未发现相关公布。"
        if value is KnowledgeQualifier.NEGATIVE_SEARCH:
            return "截至本次核验，公开检索未找到可靠信息。"
        raise ValueError("strict knowledge qualifier is unsupported")

    def _source_urls(
        self, fact_source_ids: Iterable[str], snapshot_source_ids: Iterable[str]
    ) -> tuple[str, ...]:
        allowed = set(snapshot_source_ids)
        urls = []
        for source_id in fact_source_ids:
            if source_id not in allowed:
                continue
            raw = self.source_urls.get(source_id)
            if raw is None:
                continue
            try:
                url = canonical_source_url(raw)
            except ValueError:
                continue
            if url not in urls:
                urls.append(url)
            if len(urls) == 2:
                break
        return tuple(urls)


class StrictReplyAssembler:
    def __init__(
        self,
        renderer: KnowledgeFactRenderer | None = None,
        *,
        allowed_addresses: Iterable[str] = (),
    ) -> None:
        self.renderer = renderer or KnowledgeFactRenderer()
        self.allowed_addresses = tuple(allowed_addresses)

    def assemble(
        self,
        parts: Iterable[Mapping[str, object]],
        snapshot: KnowledgeSnapshot,
        required_ids: Iterable[str],
        *,
        declared_used_knowledge_ids: Iterable[str] | None = None,
    ) -> RealizedReply:
        values = tuple(parts)
        if not values or len(values) > 8:
            raise ValueError("strict parts order is out of bounds")
        rendered = self.renderer.render(snapshot)
        by_fragment = {item.fragment_id: item for item in rendered}
        order = {item.fragment_id: index for index, item in enumerate(rendered)}
        required = tuple(dict.fromkeys(str(value) for value in required_ids))
        output = []
        used = []
        seen_fragments = set()
        last_fragment_order = -1
        for raw in values:
            if not isinstance(raw, Mapping):
                raise ValueError("strict part must be an object")
            part = dict(raw)
            kind = part.get("kind")
            if kind == "text":
                if set(part) != {"kind", "text"}:
                    raise ValueError("strict text part has an unknown field")
                text = self._safe_connector(part.get("text"), snapshot)
                output.append(text)
                continue
            if kind == "knowledge_fragment":
                if set(part) != {"kind", "fragment_id"}:
                    raise ValueError("knowledge fragment part has an unknown field")
                fragment_id = str(part.get("fragment_id") or "").strip()
                fragment = by_fragment.get(fragment_id)
                if fragment is None:
                    raise ValueError("unknown knowledge fragment")
                if fragment_id in seen_fragments:
                    raise ValueError("duplicate knowledge fragment")
                current_order = order[fragment_id]
                if current_order <= last_fragment_order:
                    raise ValueError("knowledge fragment order is out of bounds")
                last_fragment_order = current_order
                seen_fragments.add(fragment_id)
                used.append(fragment.knowledge_id)
                output.append(fragment.text)
                continue
            raise ValueError("unknown strict part kind")
        if not set(required).issubset(used):
            raise ValueError("required knowledge fragment is missing")
        declared = (
            tuple(dict.fromkeys(str(value) for value in declared_used_knowledge_ids))
            if declared_used_knowledge_ids is not None
            else tuple(used)
        )
        if declared != tuple(used):
            raise ValueError("declared knowledge IDs do not match strict fragments")
        text = "".join(output).strip()
        if not text or len(text) > 500:
            raise ValueError("strict reply text is out of bounds")
        return RealizedReply(
            text=text,
            covered_fact_ids=(),
            used_memory_ids=(),
            used_capability_ids=(),
            source_event_ids=(),
            used_knowledge_ids=tuple(used),
        )

    def _safe_connector(
        self, value: object, snapshot: KnowledgeSnapshot
    ) -> str:
        text = " ".join(
            unicodedata.normalize("NFC", str(value or "")).split()
        )
        if not text or len(text) > 48:
            raise ValueError("strict text part is out of bounds")
        if _URL.search(text):
            raise ValueError("model URL is forbidden in strict text")
        checked = unicodedata.normalize("NFKC", text).casefold()
        for address in self.allowed_addresses:
            normalized = " ".join(
                unicodedata.normalize("NFKC", str(address or "")).split()
            ).casefold()
            if normalized:
                checked = checked.replace(normalized, "")
        forbidden = set(_STRICT_STATUS_WORDS)
        forbidden.update(_RETIRED_BUNDLED_SAFETY_TOKENS)
        for seed in load_bundled_seeds():
            forbidden.add(seed.game.canonical_name)
            forbidden.add(seed.game.english_name)
            forbidden.update(alias.text for alias in seed.game.aliases)
            forbidden.update(entity.canonical_name for entity in seed.entities)
            forbidden.update(term.text for term in seed.terms)
        if _DIGIT.search(checked) or any(
            token
            and unicodedata.normalize("NFKC", token).casefold() in checked
            for token in forbidden
        ):
            raise ValueError("strict text contains a knowledge assertion")
        return text


class GroundedReplyReviewer:
    def review(
        self,
        reply: RealizedReply,
        plan: object,
        *,
        now: int,
        current_revision: int,
    ) -> KnowledgeReview:
        snapshot = getattr(plan, "knowledge_snapshot", None)
        move = getattr(plan, "move", None)
        violations = []
        if not isinstance(snapshot, KnowledgeSnapshot) or move is None:
            return KnowledgeReview(False, ("knowledge_snapshot_missing",))
        allowed = {
            *tuple(getattr(move, "must_use_knowledge_ids", ()) or ()),
            *tuple(getattr(move, "may_use_knowledge_ids", ()) or ()),
        }
        required = set(
            tuple(getattr(move, "must_use_knowledge_ids", ()) or ())
        )
        used = set(reply.used_knowledge_ids)
        if not used.issubset(allowed):
            violations.append("unknown_knowledge_id")
        if not required.issubset(used):
            violations.append("required_knowledge_missing")
        if not used and allowed:
            violations.append("knowledge_id_required")
        if int(now) >= snapshot.expires_at:
            violations.append("knowledge_snapshot_expired")
        if int(current_revision) != snapshot.version_state_revision:
            violations.append("knowledge_revision_changed")

        facts = {
            item.knowledge_id: item
            for item in snapshot.allowed_knowledge_facts
        }
        policy = str(getattr(getattr(move, "knowledge_policy", None), "value", ""))
        if policy == "grounded" and _UNSUPPORTED_GROUNDED_ASSERTION.search(
            reply.text
        ):
            violations.append("unsupported_knowledge_assertion")
        if policy == "grounded" and not self._clauses_are_grounded(
            reply.text,
            tuple(
                facts[knowledge_id].safe_summary
                for knowledge_id in used & set(facts)
            ),
        ):
            violations.append("unsupported_knowledge_assertion")
        for knowledge_id in used & set(facts):
            fact = facts[knowledge_id]
            text = reply.text.replace("不能视为官方确认", "")
            if fact.qualifier is KnowledgeQualifier.RUMOR and re.search(
                r"(?<!非)官方(?:已|确认|公布|表示|消息)", text
            ):
                violations.append("rumor_as_official")
            if fact.qualifier in {
                KnowledgeQualifier.OFFICIAL,
                KnowledgeQualifier.OFFICIAL_PREVIEW,
                KnowledgeQualifier.OFFICIAL_RELEASED,
            } and any(token in reply.text for token in ("传闻", "爆料")):
                violations.append("official_as_rumor")
            if fact.qualifier in {
                KnowledgeQualifier.NEGATIVE_OFFICIAL,
                KnowledgeQualifier.NEGATIVE_SEARCH,
            } and any(token in reply.text for token in ("确定没有", "绝对没有")):
                violations.append("negative_evidence_overclaimed")
        unique = tuple(dict.fromkeys(violations))
        return KnowledgeReview(not unique, unique)

    @staticmethod
    def _clauses_are_grounded(
        text: str, summaries: tuple[str, ...]
    ) -> bool:
        if not summaries:
            return False
        evidence = {
            character.casefold()
            for summary in summaries
            for character in _CLAIM_CHARACTER.findall(
                unicodedata.normalize("NFKC", summary)
            )
        }
        for clause in _CLAUSE_BREAK.split(
            unicodedata.normalize("NFKC", text)
        ):
            characters = {
                character.casefold()
                for character in _CLAIM_CHARACTER.findall(clause)
            }
            if len(characters) <= 5:
                continue
            overlap = len(characters & evidence)
            if overlap < 2 or overlap / len(characters) < 0.35:
                return False
        return True


__all__ = (
    "GroundedReplyReviewer",
    "KnowledgeFactRenderer",
    "KnowledgeReview",
    "StrictReplyAssembler",
)
