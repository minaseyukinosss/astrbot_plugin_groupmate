"""Independent, conservative labels for observed target behavior."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Sequence, Tuple

from eval.shadow_models import (
    AssociationConfidence,
    BehaviorExample,
    LocalReviewItem,
    ReferenceLabel,
)
from groupmate.core.response_act import ResponseAct
from groupmate.models import InteractionScene


_BOUNDARY = re.compile(
    r"(?:老婆|老公|亲一下|摸摸|隐私|密码|住址|去死|滚|骚扰)", re.I
)
_TASK = re.compile(
    r"(?:帮我|麻烦你|请你|给我).{0,16}"
    r"(?:看|查|找|搜|识别|翻译|执行|处理|发送|导出)"
)
_MISSING_OBJECT = re.compile(
    r"(?:帮我|麻烦你|请你)(?:翻译|看看|查查|处理)(?:一下)?[。！？?!]*$"
)
_SOCIAL = re.compile(r"(?:谢谢|感谢|厉害|真棒|喜欢你|给你|送你|牛奶|礼物)")
_PLAYFUL = re.compile(r"(?:比比|逗|捏|哈哈|开玩笑|不服|来战)")
_PRESENCE = re.compile(r"(?:在吗|在不在|早安|早上好|晚安|你好)[。！？?!]*$")
_QUESTION = re.compile(r"[？?]|(?:吗|呢|怎么|什么|谁|哪)[。！!]*$")
_UNSUPPORTED_REPLY = re.compile(r"(?:做不了|不能做|没法|不支持|办不到)")
_ALIAS_PADDING = re.compile(r"[\s@＠,，。.!！?？~～:：、]+")


@dataclass(frozen=True)
class LabelOverride:
    sample_id: str
    scene: InteractionScene
    act: Optional[ResponseAct]


class ReferenceLabeler:
    def __init__(self, target_alias: str, target_uin: str) -> None:
        self.target_alias = str(target_alias or "").strip()
        self.target_uin = str(target_uin or "").strip()
        if not self.target_alias or not self.target_uin:
            raise ValueError("target alias and UIN are required")

    def label(self, example: BehaviorExample) -> ReferenceLabel:
        if not isinstance(example, BehaviorExample):
            raise TypeError("example must be a BehaviorExample")
        if example.covered_context or example.review_reason:
            return self._review(
                InteractionScene.AMBIENT_CONTRIBUTION,
                None,
                "association_ambiguous",
            )
        if not example.observed_replied and not self._directed(example):
            return self._high(
                InteractionScene.AMBIENT_CONTRIBUTION,
                None,
                "ambient_observed_silence",
            )
        candidate = self._classify(example)
        if not example.observed_replied:
            return ReferenceLabel(
                scene=candidate.scene,
                act=None,
                confidence=candidate.confidence,
                reason_codes=candidate.reason_codes + ("observed_silence",),
            )
        return candidate

    def _classify(self, example: BehaviorExample) -> ReferenceLabel:
        text = example.source.text
        directed = self._directed(example)
        if _BOUNDARY.search(text):
            return self._high(
                InteractionScene.DIRECT_ADDRESS,
                ResponseAct.BOUNDARY,
                "boundary_signal",
            )
        if _TASK.search(text):
            if _MISSING_OBJECT.search(text) and self._response_is_question(example):
                return self._high(
                    InteractionScene.TASK_REQUEST,
                    ResponseAct.CLARIFY,
                    "missing_task_object",
                )
            if example.source.has_media:
                return self._high(
                    InteractionScene.TASK_REQUEST,
                    ResponseAct.TASK_HANDOFF,
                    "visual_task",
                )
            if self._response_matches(example, _UNSUPPORTED_REPLY):
                return self._high(
                    InteractionScene.TASK_REQUEST,
                    ResponseAct.TASK_UNSUPPORTED,
                    "explicit_limitation",
                )
            return self._review(
                InteractionScene.TASK_REQUEST,
                ResponseAct.TASK_HANDOFF,
                "task_status_ambiguous",
            )
        if _SOCIAL.search(text) and directed:
            return self._high(
                InteractionScene.SOCIAL_RESPONSE,
                ResponseAct.RECIPROCATE,
                "social_signal",
            )
        if _PLAYFUL.search(text) and directed:
            return self._high(
                InteractionScene.DIRECT_ADDRESS,
                ResponseAct.PLAYFUL_REPLY,
                "playful_signal",
            )
        if example.source.has_media and directed:
            return self._high(
                InteractionScene.DIRECT_ADDRESS,
                ResponseAct.VISUAL_REACTION,
                "visual_signal",
            )
        if self._bare_alias(text) or (_PRESENCE.search(text) and directed):
            return self._high(
                InteractionScene.DIRECT_ADDRESS,
                ResponseAct.ACKNOWLEDGE,
                "presence_signal",
            )
        if example.source.reply_to_sender_uin == self.target_uin:
            return self._high(
                InteractionScene.REPLY_TO_BOT,
                ResponseAct.ANSWER,
                "reply_to_target",
            )
        if _QUESTION.search(text) and directed:
            return self._high(
                InteractionScene.DIRECT_ADDRESS,
                ResponseAct.ANSWER,
                "direct_question",
            )
        scene = (
            InteractionScene.DIRECT_ADDRESS
            if directed else InteractionScene.AMBIENT_CONTRIBUTION
        )
        return self._review(scene, ResponseAct.ANSWER, "semantic_ambiguity")

    def _directed(self, example: BehaviorExample) -> bool:
        source = example.source
        return bool(
            self.target_alias in source.text
            or self.target_uin in source.mentions
            or source.reply_to_sender_uin == self.target_uin
        )

    def _bare_alias(self, text: str) -> bool:
        return _ALIAS_PADDING.sub("", text or "").casefold() == _ALIAS_PADDING.sub(
            "", self.target_alias
        ).casefold()

    @staticmethod
    def _response_matches(example: BehaviorExample, pattern) -> bool:
        return bool(
            example.response_run
            and any(pattern.search(item.text) for item in example.response_run.events)
        )

    @classmethod
    def _response_is_question(cls, example: BehaviorExample) -> bool:
        return cls._response_matches(example, _QUESTION)

    @staticmethod
    def _high(scene, act, reason):
        return ReferenceLabel(
            scene, act, AssociationConfidence.HIGH, (reason,)
        )

    @staticmethod
    def _review(scene, act, reason):
        return ReferenceLabel(
            scene, act, AssociationConfidence.REVIEW, (reason,)
        )


def load_overrides(path: Path) -> Tuple[LabelOverride, ...]:
    overrides = []
    seen = set()
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except ValueError as exc:
                raise ValueError("override line {} is invalid JSON".format(line_number)) from exc
            if not isinstance(raw, dict) or set(raw) != {"sample_id", "scene", "act"}:
                raise ValueError("override line {} has invalid keys".format(line_number))
            sample_id = raw["sample_id"]
            if not isinstance(sample_id, str) or not sample_id.strip():
                raise ValueError("override sample_id is required")
            sample_id = sample_id.strip()
            if sample_id in seen:
                raise ValueError("duplicate override sample_id {}".format(sample_id))
            try:
                scene = InteractionScene(raw["scene"])
            except (TypeError, ValueError):
                raise ValueError("invalid override scene for {}".format(sample_id))
            try:
                act = None if raw["act"] is None else ResponseAct(raw["act"])
            except (TypeError, ValueError):
                raise ValueError("invalid override act for {}".format(sample_id))
            seen.add(sample_id)
            overrides.append(LabelOverride(sample_id, scene, act))
    return tuple(overrides)


def apply_overrides(
    labels: Mapping[str, ReferenceLabel],
    overrides: Sequence[LabelOverride],
) -> dict:
    applied = dict(labels)
    for override in overrides:
        if not isinstance(override, LabelOverride):
            raise TypeError("overrides must contain LabelOverride values")
        current = applied.get(override.sample_id)
        if current is None:
            raise ValueError(
                "override sample {} is absent from current run".format(
                    override.sample_id
                )
            )
        if current.act is None and override.act is not None:
            raise ValueError("observed silence override act must be null")
        if current.act is not None and override.act is None:
            raise ValueError("replied example override act must not be null")
        applied[override.sample_id] = ReferenceLabel(
            override.scene,
            override.act,
            AssociationConfidence.HIGH,
            ("human_override",),
        )
    return applied


def collect_label_reviews(
    examples: Sequence[BehaviorExample],
    labels: Mapping[str, ReferenceLabel],
) -> Tuple[LocalReviewItem, ...]:
    reviews = []
    for example in examples:
        label = labels.get(example.sample_id)
        if label is None or label.confidence is not AssociationConfidence.REVIEW:
            continue
        response_events = (
            example.response_run.events if example.response_run is not None else ()
        )
        reviews.append(LocalReviewItem(
            sample_id=example.sample_id,
            reason=(label.reason_codes[-1] if label.reason_codes else "label_review"),
            source_events=(example.source,),
            response_events=response_events,
        ))
    return tuple(reviews)
