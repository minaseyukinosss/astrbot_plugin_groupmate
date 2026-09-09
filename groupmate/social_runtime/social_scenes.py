"""Frozen, evidence-bound descriptions of the current social scene."""

from __future__ import annotations

from dataclasses import dataclass, fields
from enum import Enum
from typing import Iterable, Mapping, Protocol

from .social_context import SceneContext


MAX_SCENE_REFERENCES = 32


class ResponseAct(str, Enum):
    ANSWER = "answer"
    ACKNOWLEDGE = "acknowledge"
    REACT = "react"
    FOLLOW_UP = "follow_up"
    CLOSE = "close"


class TargetScope(str, Enum):
    INDIVIDUAL = "INDIVIDUAL"
    GROUP = "GROUP"
    AMBIENT = "AMBIENT"


class ChorusTarget(str, Enum):
    NONE = "NONE"
    SELF = "SELF"
    MEMBER = "MEMBER"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class ChorusTone(str, Enum):
    NONE = "NONE"
    SAFE_BANTER = "SAFE_BANTER"
    SENSITIVE = "SENSITIVE"
    ATTACK = "ATTACK"
    DANGEROUS = "DANGEROUS"
    UNKNOWN = "UNKNOWN"


class SocialSceneModelPort(Protocol):
    async def classify_scene(
        self, facts: Mapping[str, object]
    ) -> Mapping[str, object]: ...


def _required_text(value: object, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} must not be empty")
    return text


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _unique_texts(values: Iterable[object], name: str) -> tuple[str, ...]:
    result = tuple(
        dict.fromkeys(str(value).strip() for value in values if str(value).strip())
    )
    if len(result) > MAX_SCENE_REFERENCES:
        raise ValueError(f"{name} exceeds {MAX_SCENE_REFERENCES} items")
    return result


@dataclass(frozen=True)
class SocialScene:
    scene_kind: str
    target_scope: TargetScope
    target_id: str | None
    literal_subject: str
    user_move: str
    continuity_event_ids: tuple[str, ...]
    repetition_count: int = 0
    chorus_target: ChorusTarget = ChorusTarget.NONE
    chorus_target_id: str | None = None
    chorus_chain_id: str | None = None
    chorus_payload: str | None = None
    chorus_event_ids: tuple[str, ...] = ()
    chorus_participant_ids: tuple[str, ...] = ()
    chorus_already_joined: bool = False
    chorus_tone: ChorusTone = ChorusTone.NONE
    constraints: tuple[str, ...] = ()
    information_gaps: tuple[str, ...] = ()
    capability_request: str = "NONE"
    confidence: float = 0.0
    response_act: ResponseAct | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "scene_kind", _required_text(self.scene_kind, "scene_kind"))
        object.__setattr__(self, "target_scope", TargetScope(self.target_scope))
        object.__setattr__(self, "target_id", _optional_text(self.target_id))
        object.__setattr__(
            self, "literal_subject", _required_text(self.literal_subject, "literal_subject")
        )
        object.__setattr__(self, "user_move", _required_text(self.user_move, "user_move"))
        object.__setattr__(self, "response_act", None if self.response_act is None else ResponseAct(self.response_act))
        object.__setattr__(
            self,
            "continuity_event_ids",
            _unique_texts(self.continuity_event_ids, "continuity_event_ids"),
        )
        object.__setattr__(self, "chorus_target", ChorusTarget(self.chorus_target))
        object.__setattr__(self, "chorus_tone", ChorusTone(self.chorus_tone))
        object.__setattr__(self, "chorus_target_id", _optional_text(self.chorus_target_id))
        object.__setattr__(self, "chorus_chain_id", _optional_text(self.chorus_chain_id))
        object.__setattr__(self, "chorus_payload", _optional_text(self.chorus_payload))
        object.__setattr__(
            self,
            "chorus_event_ids",
            _unique_texts(self.chorus_event_ids, "chorus_event_ids"),
        )
        object.__setattr__(
            self,
            "chorus_participant_ids",
            _unique_texts(self.chorus_participant_ids, "chorus_participant_ids"),
        )
        object.__setattr__(self, "constraints", _unique_texts(self.constraints, "constraints"))
        object.__setattr__(
            self,
            "information_gaps",
            _unique_texts(self.information_gaps, "information_gaps"),
        )
        object.__setattr__(
            self,
            "capability_request",
            _required_text(self.capability_request, "capability_request"),
        )

        repetition_count = int(self.repetition_count)
        if repetition_count < 0:
            raise ValueError("repetition_count must not be negative")
        object.__setattr__(self, "repetition_count", repetition_count)
        confidence = float(self.confidence)
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        object.__setattr__(self, "confidence", confidence)

        if self.target_scope is TargetScope.INDIVIDUAL and self.target_id is None:
            raise ValueError("INDIVIDUAL scene requires target_id")
        if not self.continuity_event_ids:
            raise ValueError("continuity_event_ids must not be empty")
        self._validate_chorus()

    def _validate_chorus(self) -> None:
        has_evidence = self.chorus_chain_id is not None
        evidence_fields_present = bool(
            self.chorus_payload
            or self.chorus_event_ids
            or self.chorus_participant_ids
            or self.chorus_target is not ChorusTarget.NONE
            or self.chorus_tone is not ChorusTone.NONE
            or self.chorus_already_joined
        )
        if has_evidence != evidence_fields_present:
            raise ValueError("chorus fields must form one complete evidence set")
        if not has_evidence:
            if self.chorus_target_id is not None:
                raise ValueError("chorus_target_id requires chorus evidence")
            return

        # 靶心是语义判断；只有 MEMBER 才能绑定群成员，避免把模型猜测当成关系对象。
        if self.chorus_target is ChorusTarget.MEMBER:
            if self.chorus_target_id is None:
                raise ValueError("MEMBER chorus requires chorus_target_id")
        elif self.chorus_target_id is not None:
            raise ValueError("chorus_target_id is only valid for MEMBER chorus")
        if self.chorus_target is ChorusTarget.NONE or self.chorus_tone is ChorusTone.NONE:
            raise ValueError("chorus target and tone are required")
        if not self.chorus_payload:
            raise ValueError("chorus_payload must not be empty")
        if len(self.chorus_event_ids) < 2 or len(self.chorus_participant_ids) < 2:
            raise ValueError("chorus evidence requires two events and participants")
        if not set(self.chorus_event_ids).issubset(self.continuity_event_ids):
            raise ValueError("chorus_event_ids must be a subset of continuity_event_ids")
        if self.repetition_count < 2:
            raise ValueError("chorus repetition_count must be at least 2")

    @classmethod
    def create(cls, **values: object) -> "SocialScene":
        return cls(**values)


@dataclass(frozen=True)
class SceneInterpretationResult:
    scene: SocialScene
    diagnostic_code: str | None = None
    diagnostic: Mapping[str, object] | None = None


class SocialSceneInterpreter:
    """Accept model semantics only when every referenced fact exists locally."""

    def __init__(self, model: SocialSceneModelPort) -> None:
        self._model = model

    async def interpret(self, context: SceneContext) -> SceneInterpretationResult:
        try:
            raw = dict(await self._model.classify_scene(context.to_model_facts()))
        except Exception as exc:
            return self._fallback(context, "scene_model_failed", exception=exc)

        claims_chorus = any(
            raw.get(field) not in (None, "", (), [], "NONE")
            for field in (
                "chorus_target",
                "chorus_tone",
                "chorus_chain_id",
                "chorus_payload",
                "chorus_event_ids",
                "chorus_participant_ids",
            )
        )
        if claims_chorus and context.chorus is None:
            return self._fallback(context, "chorus_evidence_missing")
        raw = self._freeze_chorus_evidence(raw, context)
        # No historical link is not the same as no evidence: the current input
        # is known locally. Never fill in or discard a claimed historical ID.
        if (
            context.chorus is None
            and raw.get("continuity_event_ids") in ([], ())
            and context.source_event_id in {item.event_id for item in context.events}
        ):
            raw["continuity_event_ids"] = [context.source_event_id]
        try:
            scene = SocialScene.create(**raw)
        except (TypeError, ValueError) as exc:
            return self._fallback(context, "scene_model_invalid", raw=raw, exception=exc)

        allowed_event_ids = {item.event_id for item in context.events}
        if not set(scene.continuity_event_ids).issubset(allowed_event_ids):
            return self._fallback(context, "scene_evidence_invalid")
        if scene.target_scope is TargetScope.INDIVIDUAL:
            if scene.target_id != context.target_id:
                return self._fallback(context, "scene_target_invalid")
        elif scene.target_id is not None:
            return self._fallback(context, "scene_target_invalid")
        if scene.chorus_target is ChorusTarget.MEMBER:
            if scene.chorus_target_id not in context.member_refs:
                return self._chorus_unknown(context, "chorus_member_invalid")
        return SceneInterpretationResult(scene)

    @staticmethod
    def _freeze_chorus_evidence(
        raw: dict[str, object], context: SceneContext
    ) -> dict[str, object]:
        evidence = context.chorus
        frozen = dict(raw)
        if evidence is None:
            for field in (
                "chorus_chain_id",
                "chorus_payload",
                "chorus_event_ids",
                "chorus_participant_ids",
                "chorus_already_joined",
            ):
                frozen.pop(field, None)
            return frozen
        # 链 ID、原文和参与者来自确定性检测，模型只能判断语义靶心与风险。
        frozen.update(
            {
                "repetition_count": max(
                    len(evidence.event_ids), int(frozen.get("repetition_count", 0))
                ),
                "chorus_chain_id": evidence.chain_id,
                "chorus_payload": evidence.payload,
                "chorus_event_ids": evidence.event_ids,
                "chorus_participant_ids": evidence.participant_ids,
                "chorus_already_joined": evidence.already_joined,
            }
        )
        if evidence.kind == "STICKER":
            frozen["scene_kind"] = "group_chorus"
            frozen["target_scope"] = "GROUP"
            frozen["target_id"] = None
            frozen["literal_subject"] = str(frozen.get("literal_subject") or "").strip() or "表情包"
            frozen["chorus_target"] = "OTHER"
            frozen["chorus_target_id"] = None
            frozen["chorus_tone"] = "SAFE_BANTER"
            frozen["user_move"] = str(frozen.get("user_move") or "").strip() or "chorus_sticker"
        continuity = [
            str(item)
            for item in tuple(frozen.get("continuity_event_ids") or ())
            if str(item or "").strip()
        ]
        for event_id in evidence.event_ids:
            if event_id not in continuity:
                continuity.append(event_id)
        frozen["continuity_event_ids"] = continuity
        target = str(frozen.get("chorus_target") or "NONE").strip().upper() or "NONE"
        tone = str(frozen.get("chorus_tone") or "NONE").strip().upper() or "NONE"
        payload = str(evidence.payload or "")
        if target == "NONE":
            if any(
                alias and alias in payload for alias in context.persona_aliases
            ):
                frozen["chorus_target"] = "SELF"
                target = "SELF"
            else:
                matched = next(
                    (
                        member_id
                        for member_id, names in context.member_refs.items()
                        if any(name and name in payload for name in names)
                    ),
                    None,
                )
                if matched is not None:
                    frozen["chorus_target"] = "MEMBER"
                    frozen["chorus_target_id"] = matched
                    target = "MEMBER"
                else:
                    frozen["chorus_target"] = "OTHER"
                    target = "OTHER"
        if tone == "NONE":
            frozen["chorus_tone"] = (
                "SAFE_BANTER" if target in {"OTHER", "SELF", "MEMBER"} else "UNKNOWN"
            )
        if target != "MEMBER":
            frozen["chorus_target_id"] = None
        return frozen

    @staticmethod
    def _fallback(
        context: SceneContext, diagnostic_code: str, *,
        raw: Mapping[str, object] | None = None,
        exception: Exception | None = None,
    ) -> SceneInterpretationResult:
        target_id = context.target_id
        scope = TargetScope.INDIVIDUAL if target_id else TargetScope.AMBIENT
        scene = SocialScene.create(
            scene_kind="conservative_direct" if target_id else "observe_only",
            target_scope=scope,
            target_id=target_id,
            literal_subject=context.current_text[:160] or "当前互动",
            user_move="direct_message" if target_id else "ambient_activity",
            continuity_event_ids=(context.source_event_id,),
            confidence=0.0,
        )
        diagnostic: dict[str, object] = {"code": diagnostic_code}
        if exception is not None:
            diagnostic["exception_type"] = type(exception).__name__[:80]
            cause = str(getattr(exception, "code", ""))
            if cause in {
                "text_timeout", "text_network_failed", "text_auth_failed",
                "text_rate_limited", "text_upstream_failed",
                "text_response_shape_invalid", "text_response_too_large",
                "scene_response_json_invalid", "scene_response_shape_invalid",
                "scene_response_size_invalid",
            }:
                diagnostic["cause_code"] = cause
        if raw is not None:
            # Only schema names/types: no exception message, arbitrary key or model prose.
            names = {field.name for field in fields(SocialScene)}
            diagnostic["field_types"] = {
                name: type(raw[name]).__name__ for name in sorted(names & raw.keys())
            }
            diagnostic["unknown_field_count"] = len(raw.keys() - names)
            for name, enum in (("target_scope", TargetScope),
                               ("response_act", ResponseAct),
                               ("chorus_target", ChorusTarget), ("chorus_tone", ChorusTone)):
                if name in raw and not (name == "response_act" and raw[name] is None):
                    try:
                        enum(raw[name])
                    except (ValueError, TypeError):
                        diagnostic["field"] = name
                        break
            if "field" not in diagnostic and exception is not None:
                # Extract only a known field name, never the untrusted exception text.
                diagnostic["field"] = next(
                    (name for name in sorted(names) if name in str(exception)), "schema"
                )
        return SceneInterpretationResult(scene, diagnostic_code, diagnostic)

    @staticmethod
    def _chorus_unknown(
        context: SceneContext, diagnostic_code: str
    ) -> SceneInterpretationResult:
        evidence = context.chorus
        if evidence is None:
            return SocialSceneInterpreter._fallback(context, diagnostic_code)
        scene = SocialScene.create(
            scene_kind="group_chorus",
            target_scope=TargetScope.GROUP,
            target_id=None,
            literal_subject="无法可靠解析的复读靶心",
            user_move="chorus_target_unknown",
            continuity_event_ids=evidence.event_ids,
            repetition_count=len(evidence.event_ids),
            chorus_target=ChorusTarget.UNKNOWN,
            chorus_target_id=None,
            chorus_chain_id=evidence.chain_id,
            chorus_payload=evidence.payload,
            chorus_event_ids=evidence.event_ids,
            chorus_participant_ids=evidence.participant_ids,
            chorus_already_joined=evidence.already_joined,
            chorus_tone=ChorusTone.UNKNOWN,
            confidence=0.0,
        )
        return SceneInterpretationResult(scene, diagnostic_code)


__all__ = (
    "ChorusTarget",
    "ChorusTone",
    "ResponseAct",
    "SceneInterpretationResult",
    "SocialScene",
    "SocialSceneInterpreter",
    "SocialSceneModelPort",
    "TargetScope",
)
