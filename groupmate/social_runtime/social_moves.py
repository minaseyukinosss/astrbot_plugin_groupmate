"""Concrete social moves and evidence-carrying realization facts."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping

from .knowledge.contracts import RiskClass
from .social_scenes import ChorusTarget, ChorusTone, ResponseAct, SocialScene, TargetScope
from .stances import Boundary, StanceDecision, Willingness


MAX_MOVE_REFERENCES = 32


class SocialMove(str, Enum):
    DIRECT_ANSWER = "DIRECT_ANSWER"
    ACCEPT = "ACCEPT"
    LIMITED_ACCEPT = "LIMITED_ACCEPT"
    PLAYFUL_RESISTANCE = "PLAYFUL_RESISTANCE"
    REFUSE = "REFUSE"
    FIRM_BOUNDARY = "FIRM_BOUNDARY"
    TEASE_FROM_CONTEXT = "TEASE_FROM_CONTEXT"
    COUNTER = "COUNTER"
    CORRECT_SELF = "CORRECT_SELF"
    REQUEST_NEEDED_EVIDENCE = "REQUEST_NEEDED_EVIDENCE"
    CONCRETE_CARE = "CONCRETE_CARE"
    SAFETY_MINIMUM = "SAFETY_MINIMUM"
    GROUP_RESPONSE = "GROUP_RESPONSE"
    JOIN_CHORUS = "JOIN_CHORUS"
    PROACTIVE_JOIN = "PROACTIVE_JOIN"
    SILENCE = "SILENCE"


class Ending(str, Enum):
    STOP = "STOP"
    QUESTION = "QUESTION"
    COUNTER = "COUNTER"
    OPEN_ACTION = "OPEN_ACTION"


class MediaIntent(str, Enum):
    NONE = "NONE"
    REACT = "REACT"
    SEND_IF_AVAILABLE = "SEND_IF_AVAILABLE"


class RealizationMode(str, Enum):
    GENERATED = "GENERATED"
    EXACT_CHORUS = "EXACT_CHORUS"


class KnowledgePolicy(str, Enum):
    NONE = "none"
    GROUNDED = "grounded"
    STRICT = "strict"


def _normalized_text(value: object, name: str) -> str:
    normalized = " ".join(unicodedata.normalize("NFKC", str(value or "")).split())
    if not normalized:
        raise ValueError(f"{name} must not be empty")
    return normalized


def _unique_texts(values: Iterable[object], name: str) -> tuple[str, ...]:
    result = tuple(dict.fromkeys(_normalized_text(value, name) for value in values))
    if len(result) > MAX_MOVE_REFERENCES:
        raise ValueError(f"{name} exceeds {MAX_MOVE_REFERENCES} items")
    return result


def _unique_facts(values: Iterable["DecisionFact"], name: str) -> tuple["DecisionFact", ...]:
    result: list[DecisionFact] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, DecisionFact):
            raise ValueError(f"{name} must contain DecisionFact values")
        if value.fact_id not in seen:
            seen.add(value.fact_id)
            result.append(value)
    if len(result) > MAX_MOVE_REFERENCES:
        raise ValueError(f"{name} exceeds {MAX_MOVE_REFERENCES} items")
    return tuple(result)


@dataclass(frozen=True)
class DecisionFact:
    fact_id: str
    category: str
    text: str
    source_event_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "fact_id", _normalized_text(self.fact_id, "fact_id"))
        object.__setattr__(self, "category", _normalized_text(self.category, "category"))
        object.__setattr__(self, "text", _normalized_text(self.text, "text"))
        object.__setattr__(
            self,
            "source_event_ids",
            _unique_texts(self.source_event_ids, "source_event_ids"),
        )
        if not self.source_event_ids:
            raise ValueError("source_event_ids must not be empty")

    @classmethod
    def create(
        cls,
        *,
        category: str,
        text: str,
        source_event_ids: Iterable[str],
    ) -> "DecisionFact":
        normalized_category = _normalized_text(category, "category")
        normalized_text = _normalized_text(text, "text")
        normalized_sources = _unique_texts(source_event_ids, "source_event_ids")
        if not normalized_sources:
            raise ValueError("source_event_ids must not be empty")
        canonical = json.dumps(
            {
                "category": normalized_category,
                "source_event_ids": normalized_sources,
                "text": normalized_text,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
        return cls(
            fact_id=f"decision-fact:{digest}",
            category=normalized_category,
            text=normalized_text,
            source_event_ids=normalized_sources,
        )


@dataclass(frozen=True)
class SocialMovePlan:
    primary_move: SocialMove
    secondary_move: SocialMove | None
    must_say: tuple[DecisionFact, ...]
    may_say: tuple[DecisionFact, ...]
    must_not_say: tuple[str, ...]
    mention_event_ids: tuple[str, ...]
    ask_for: tuple[str, ...]
    ending: Ending
    media_intent: MediaIntent
    knowledge_policy: KnowledgePolicy = KnowledgePolicy.NONE
    must_use_knowledge_ids: tuple[str, ...] = ()
    may_use_knowledge_ids: tuple[str, ...] = ()
    prohibited_assertion_classes: tuple[RiskClass, ...] = ()
    realization_mode: RealizationMode = RealizationMode.GENERATED
    verbatim_payload: str | None = None
    chorus_chain_id: str | None = None
    response_act: ResponseAct | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "primary_move", SocialMove(self.primary_move))
        object.__setattr__(self, "response_act", None if self.response_act is None else ResponseAct(self.response_act))
        object.__setattr__(
            self,
            "secondary_move",
            None if self.secondary_move is None else SocialMove(self.secondary_move),
        )
        object.__setattr__(self, "must_say", _unique_facts(self.must_say, "must_say"))
        object.__setattr__(self, "may_say", _unique_facts(self.may_say, "may_say"))
        object.__setattr__(
            self, "must_not_say", _unique_texts(self.must_not_say, "must_not_say")
        )
        object.__setattr__(
            self,
            "mention_event_ids",
            _unique_texts(self.mention_event_ids, "mention_event_ids"),
        )
        object.__setattr__(self, "ask_for", _unique_texts(self.ask_for, "ask_for"))
        object.__setattr__(self, "ending", Ending(self.ending))
        object.__setattr__(self, "media_intent", MediaIntent(self.media_intent))
        object.__setattr__(
            self, "knowledge_policy", KnowledgePolicy(self.knowledge_policy)
        )
        object.__setattr__(
            self,
            "must_use_knowledge_ids",
            _unique_texts(
                self.must_use_knowledge_ids, "must_use_knowledge_ids"
            ),
        )
        object.__setattr__(
            self,
            "may_use_knowledge_ids",
            _unique_texts(self.may_use_knowledge_ids, "may_use_knowledge_ids"),
        )
        try:
            prohibited = tuple(
                dict.fromkeys(
                    RiskClass(value)
                    for value in self.prohibited_assertion_classes
                )
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "prohibited assertion class is unsupported"
            ) from exc
        object.__setattr__(self, "prohibited_assertion_classes", prohibited)
        object.__setattr__(self, "realization_mode", RealizationMode(self.realization_mode))
        payload = None if self.verbatim_payload is None else str(self.verbatim_payload).strip()
        chain_id = None if self.chorus_chain_id is None else str(self.chorus_chain_id).strip()
        object.__setattr__(self, "verbatim_payload", payload or None)
        object.__setattr__(self, "chorus_chain_id", chain_id or None)
        self._validate_knowledge_authority()
        self._validate_realization()

    def _validate_knowledge_authority(self) -> None:
        knowledge_ids = {
            *self.must_use_knowledge_ids,
            *self.may_use_knowledge_ids,
        }
        decision_ids = {
            item.fact_id for item in (*self.must_say, *self.may_say)
        }
        if knowledge_ids & decision_ids:
            raise ValueError(
                "decision fact IDs and knowledge IDs must stay separate"
            )
        if self.knowledge_policy is KnowledgePolicy.NONE and knowledge_ids:
            raise ValueError("knowledge none cannot authorize knowledge IDs")
        if (
            self.knowledge_policy is KnowledgePolicy.STRICT
            and not self.must_use_knowledge_ids
        ):
            raise ValueError("strict knowledge requires required knowledge IDs")
        if self.primary_move in {SocialMove.SILENCE, SocialMove.JOIN_CHORUS} and (
            self.knowledge_policy is not KnowledgePolicy.NONE
        ):
            raise ValueError(
                "SILENCE and JOIN_CHORUS require knowledge none"
            )

    def _validate_realization(self) -> None:
        if self.ending is Ending.QUESTION and not (
            self.ask_for or self.primary_move is SocialMove.COUNTER
            or self.response_act is ResponseAct.FOLLOW_UP
        ):
            raise ValueError("question ending requires a real information gap or counter")

        if self.primary_move is SocialMove.JOIN_CHORUS:
            # 原样复读是冻结通道：不能混入模型生成、解释前缀或其他必须表达内容。
            if self.realization_mode is not RealizationMode.EXACT_CHORUS:
                raise ValueError("JOIN_CHORUS requires EXACT_CHORUS realization")
            if not self.verbatim_payload:
                raise ValueError("JOIN_CHORUS requires verbatim_payload")
            if not self.chorus_chain_id:
                raise ValueError("JOIN_CHORUS requires chorus_chain_id")
            if self.must_say or self.may_say or self.ask_for:
                raise ValueError("JOIN_CHORUS cannot carry generated content")
            if self.ending is not Ending.STOP:
                raise ValueError("JOIN_CHORUS must use STOP ending")
            return

        if self.realization_mode is not RealizationMode.GENERATED:
            raise ValueError("only JOIN_CHORUS may use EXACT_CHORUS")
        if self.verbatim_payload is not None or self.chorus_chain_id is not None:
            raise ValueError("verbatim payload and chain id are only JOIN_CHORUS fields")
        if self.primary_move is SocialMove.SILENCE and (
            self.must_say or self.may_say or self.ask_for
        ):
            raise ValueError("SILENCE cannot carry response content")

    @classmethod
    def create(cls, **values: object) -> "SocialMovePlan":
        normalized = {
            "secondary_move": None,
            "must_say": (),
            "may_say": (),
            "must_not_say": (),
            "mention_event_ids": (),
            "ask_for": (),
            "ending": Ending.STOP,
            "media_intent": MediaIntent.NONE,
            "knowledge_policy": KnowledgePolicy.NONE,
            "must_use_knowledge_ids": (),
            "may_use_knowledge_ids": (),
            "prohibited_assertion_classes": (),
            **values,
        }
        for field in ("must_say", "may_say"):
            normalized[field] = tuple(
                DecisionFact(
                    **{
                        **dict(item),
                        "source_event_ids": tuple(
                            dict(item).get("source_event_ids", ())
                        ),
                    }
                )
                if isinstance(item, Mapping)
                else item
                for item in normalized[field]
            )
        return cls(**normalized)


class SocialMovePlanner:
    """Turn a frozen scene and stance into one observable social action."""

    _DEFAULT_AVOIDANCES = (
        "不要编造没有证据的旧事",
        "不要泄漏内部关系分数",
        "不要追加通用服务邀请",
    )

    def plan(
        self,
        scene: SocialScene,
        stance: StanceDecision,
        *,
        profile: object | None,
        memories: Iterable[object],
    ) -> SocialMovePlan:
        if stance.willingness is Willingness.REQUIRED_MINIMUM:
            return self._safety_minimum(scene)
        if scene.chorus_chain_id is not None:
            return self._chorus(scene, stance)
        if stance.boundary in {Boundary.FIRM, Boundary.FINAL}:
            return self._firm_boundary(scene)
        if scene.scene_kind in {"proactive_no_entry", "observe_only"}:
            return self._silence(scene)
        if scene.scene_kind in {"proactive_specific_topic", "proactive_join"}:
            if stance.willingness is Willingness.UNWILLING:
                return self._silence(scene)
            return self._generated(scene, SocialMove.PROACTIVE_JOIN)
        if scene.target_scope is TargetScope.GROUP:
            return self._generated(scene, SocialMove.GROUP_RESPONSE)
        if scene.scene_kind == "self_correction":
            return self._correct_self(scene)
        if scene.information_gaps:
            return self._request_evidence(scene)
        if stance.willingness is Willingness.LIMITED:
            return self._generated(
                scene,
                SocialMove.LIMITED_ACCEPT,
                profile=profile,
                memories=memories,
            )
        if stance.willingness is Willingness.UNWILLING:
            return self._generated(scene, SocialMove.REFUSE)
        return self._direct_or_play(scene, profile=profile, memories=memories)

    def _chorus(
        self, scene: SocialScene, stance: StanceDecision
    ) -> SocialMovePlan:
        # 复读分流必须早于通用 GROUP；否则针对群友和针对爱弥斯会被混成一种回应。
        if scene.chorus_already_joined:
            return self._silence(scene)
        if scene.chorus_target is ChorusTarget.SELF:
            if stance.willingness is Willingness.UNWILLING:
                return self._silence(scene)
            return self._generated(
                scene,
                SocialMove.GROUP_RESPONSE,
                must_not_say=("不要逐字复读针对爱弥斯的原句",),
            )
        if scene.chorus_target in {ChorusTarget.MEMBER, ChorusTarget.OTHER}:
            if (
                scene.chorus_tone is ChorusTone.SAFE_BANTER
                and stance.willingness in {Willingness.WILLING, Willingness.EAGER}
            ):
                return SocialMovePlan.create(
                    primary_move=SocialMove.JOIN_CHORUS,
                    mention_event_ids=scene.chorus_event_ids,
                    realization_mode=RealizationMode.EXACT_CHORUS,
                    verbatim_payload=scene.chorus_payload,
                    chorus_chain_id=scene.chorus_chain_id,
                )
        return self._silence(scene)

    def _firm_boundary(self, scene: SocialScene) -> SocialMovePlan:
        count = max(1, scene.repetition_count)
        ordinal = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五"}.get(
            count, str(count)
        )
        fact = self._fact(
            "boundary",
            f"这是第{ordinal}次提出同类请求，爱弥斯明确拒绝。",
            scene,
        )
        return self._generated(
            scene,
            SocialMove.FIRM_BOUNDARY,
            must_say=(fact,),
        )

    def _request_evidence(self, scene: SocialScene) -> SocialMovePlan:
        fact = self._fact(
            "required_input",
            "需要" + "和".join(scene.information_gaps) + "才能继续判断。",
            scene,
        )
        return self._generated(
            scene,
            SocialMove.REQUEST_NEEDED_EVIDENCE,
            must_say=(fact,),
            ask_for=scene.information_gaps,
            ending=Ending.QUESTION,
        )

    def _correct_self(self, scene: SocialScene) -> SocialMovePlan:
        fact = self._fact(
            "correction",
            f"承认关于{scene.literal_subject}的上一判断不适用并立即改正。",
            scene,
        )
        return self._generated(scene, SocialMove.CORRECT_SELF, must_say=(fact,))

    def _safety_minimum(self, scene: SocialScene) -> SocialMovePlan:
        fact = self._fact(
            "safety",
            "给出当前处境下立即可执行的最低安全行动。",
            scene,
        )
        return self._generated(
            scene,
            SocialMove.SAFETY_MINIMUM,
            must_say=(fact,),
            ending=Ending.OPEN_ACTION,
        )

    def _direct_or_play(
        self,
        scene: SocialScene,
        *,
        profile: object | None,
        memories: Iterable[object],
    ) -> SocialMovePlan:
        if scene.response_act is not None:
            return self._generated(
                scene, SocialMove.DIRECT_ANSWER,
                response_act=scene.response_act,
                ending=Ending.QUESTION if scene.response_act is ResponseAct.FOLLOW_UP else Ending.STOP,
                profile=profile, memories=memories,
            )
        move = {
            "intimacy_request": SocialMove.PLAYFUL_RESISTANCE,
            "playful_negotiation": SocialMove.ACCEPT,
            "contextual_teasing": SocialMove.TEASE_FROM_CONTEXT,
            "identity_continuity": SocialMove.COUNTER,
            "care_signal": SocialMove.CONCRETE_CARE,
            "technical_constraint": SocialMove.DIRECT_ANSWER,
        }.get(scene.scene_kind, SocialMove.DIRECT_ANSWER)
        ending = Ending.COUNTER if move is SocialMove.COUNTER else Ending.STOP
        return self._generated(
            scene,
            move,
            may_say=self._supported_optional_facts(scene, profile, memories),
            ending=ending,
        )

    def _generated(
        self,
        scene: SocialScene,
        move: SocialMove,
        *,
        must_say: tuple[DecisionFact, ...] = (),
        may_say: tuple[DecisionFact, ...] = (),
        must_not_say: tuple[str, ...] = (),
        ask_for: tuple[str, ...] = (),
        ending: Ending = Ending.STOP,
        response_act: ResponseAct | None = None,
        profile: object | None = None,
        memories: Iterable[object] = (),
    ) -> SocialMovePlan:
        optional = may_say or self._supported_optional_facts(scene, profile, memories)
        return SocialMovePlan.create(
            primary_move=move,
            must_say=must_say,
            may_say=optional,
            must_not_say=(*self._DEFAULT_AVOIDANCES, *must_not_say),
            mention_event_ids=scene.continuity_event_ids,
            ask_for=ask_for,
            ending=ending,
            response_act=response_act,
            media_intent=MediaIntent.NONE,
        )

    @staticmethod
    def _silence(scene: SocialScene) -> SocialMovePlan:
        return SocialMovePlan.create(
            primary_move=SocialMove.SILENCE,
            mention_event_ids=scene.continuity_event_ids,
        )

    @staticmethod
    def _fact(category: str, text: str, scene: SocialScene) -> DecisionFact:
        return DecisionFact.create(
            category=category,
            text=text,
            source_event_ids=scene.continuity_event_ids,
        )

    def _supported_optional_facts(
        self,
        scene: SocialScene,
        profile: object | None,
        memories: Iterable[object],
    ) -> tuple[DecisionFact, ...]:
        optional: list[DecisionFact] = []
        subject_chars = set(scene.literal_subject.casefold())
        for fact in tuple(getattr(profile, "facts", ()) or ())[:3]:
            summary = str(getattr(fact, "summary", "")).strip()
            sources = tuple(getattr(fact, "source_event_ids", ()) or ())
            status = str(getattr(fact, "status", ""))
            if not summary or not sources or status != "confirmed":
                continue
            # 只让与当前具体主语有字符交集的画像进入可选材料，避免无关熟人梗乱入。
            if subject_chars and not (subject_chars & set(summary.casefold())):
                continue
            optional.append(DecisionFact.create(
                category="profile_fact", text=summary, source_event_ids=sources
            ))
        if scene.scene_kind in {
            "intimacy_request",
            "playful_negotiation",
            "identity_continuity",
            "apology_and_relationship_repair",
        }:
            for memory in tuple(memories)[:2]:
                if getattr(memory, "resolved_at", None) is not None:
                    continue
                summary = str(getattr(memory, "summary", "")).strip()
                source = str(getattr(memory, "relationship_event_id", "")).strip()
                if summary and source:
                    optional.append(DecisionFact.create(
                        category="relationship_memory",
                        text=summary,
                        source_event_ids=(source,),
                    ))
        return tuple(optional[:3])


__all__ = (
    "DecisionFact",
    "Ending",
    "KnowledgePolicy",
    "MediaIntent",
    "RealizationMode",
    "SocialMove",
    "SocialMovePlan",
    "SocialMovePlanner",
)
