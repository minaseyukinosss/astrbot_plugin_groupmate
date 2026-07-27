"""Deterministic response acts selected from interaction scenes."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

from ..models import InteractionScene, ReplyMode, StringEnum


class ResponseAct(StringEnum):
    ACKNOWLEDGE = "acknowledge"
    ANSWER = "answer"
    CLARIFY = "clarify"
    RECIPROCATE = "reciprocate"
    PLAYFUL_REPLY = "playful_reply"
    BOUNDARY = "boundary"
    TASK_HANDOFF = "task_handoff"
    TASK_UNSUPPORTED = "task_unsupported"
    VISUAL_REACTION = "visual_reaction"


class TaskResolutionStatus(StringEnum):
    UNKNOWN = "unknown"
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class TaskResolution:
    status: TaskResolutionStatus = TaskResolutionStatus.UNKNOWN
    capability_name: str = ""
    required_information: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        status = self.status
        if not isinstance(status, TaskResolutionStatus):
            try:
                status = TaskResolutionStatus(str(status))
            except ValueError:
                status = TaskResolutionStatus.UNKNOWN
        object.__setattr__(self, "status", status)
        object.__setattr__(
            self,
            "capability_name",
            _clean_fact(self.capability_name, max_chars=80),
        )
        object.__setattr__(
            self,
            "required_information",
            (
                _clean_facts(self.required_information)
                if status is TaskResolutionStatus.SUPPORTED
                else ()
            ),
        )

    @property
    def supported(self) -> bool:
        return self.status is TaskResolutionStatus.SUPPORTED


@dataclass(frozen=True)
class ResponseActPlan:
    act: ResponseAct
    scene: InteractionScene
    reason_codes: Tuple[str, ...]
    required_information: Tuple[str, ...] = ()
    capability_name: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason_codes", tuple(self.reason_codes or ()))
        object.__setattr__(
            self,
            "required_information",
            _clean_facts(self.required_information),
        )
        object.__setattr__(
            self,
            "capability_name",
            _clean_fact(self.capability_name, max_chars=80),
        )


_PLAYFUL = re.compile(r"(?:比比|逗|捏|摸|哈哈|嘿嘿|开玩笑|不服|来战)")
_ALIAS_PADDING = re.compile(r"[\s@＠,，。.!！?？~～:：、]+")


def plan_response_act(
    scene: InteractionScene,
    *,
    reply_mode: ReplyMode,
    text: str = "",
    aliases: Sequence[str] = (),
    has_visual: bool = False,
    boundary_required: bool = False,
    task_supported: bool = False,
    required_information: Sequence[str] = (),
    capability_name: str = "",
    task_resolution: Optional[TaskResolution] = None,
) -> ResponseActPlan:
    """Choose one response act using only explicit, observable inputs."""

    resolution = task_resolution or TaskResolution(
        status=(
            TaskResolutionStatus.SUPPORTED
            if task_supported
            else TaskResolutionStatus.UNSUPPORTED
        ),
        capability_name=capability_name,
        required_information=required_information,
    )
    missing = resolution.required_information
    capability = resolution.capability_name
    scene_reason = "scene:{}".format(scene.value)

    if boundary_required or reply_mode is ReplyMode.BOUNDARY:
        reason = (
            "boundary_required"
            if boundary_required
            else "reply_mode:boundary"
        )
        return ResponseActPlan(
            ResponseAct.BOUNDARY,
            scene,
            (scene_reason, reason),
        )

    if scene is InteractionScene.TASK_REQUEST:
        if resolution.status is TaskResolutionStatus.SUPPORTED and missing:
            return ResponseActPlan(
                ResponseAct.CLARIFY,
                scene,
                (scene_reason, "task_information_missing"),
                missing,
                capability,
            )
        if resolution.status is TaskResolutionStatus.SUPPORTED:
            return ResponseActPlan(
                ResponseAct.TASK_HANDOFF,
                scene,
                (scene_reason, "task_supported"),
                capability_name=capability,
            )
        return ResponseActPlan(
            ResponseAct.TASK_UNSUPPORTED,
            scene,
            (scene_reason, "task_unsupported"),
            capability_name=capability,
        )

    cleaned = (text or "").strip()
    if scene is InteractionScene.SOCIAL_RESPONSE:
        if _PLAYFUL.search(cleaned):
            return ResponseActPlan(
                ResponseAct.PLAYFUL_REPLY,
                scene,
                (scene_reason, "playful_signal"),
            )
        return ResponseActPlan(
            ResponseAct.RECIPROCATE,
            scene,
            (scene_reason, "social_reciprocity"),
        )
    if reply_mode is ReplyMode.HELP_DETAIL:
        return ResponseActPlan(
            ResponseAct.ANSWER,
            scene,
            (scene_reason, "help_request"),
        )
    if has_visual and not cleaned:
        return ResponseActPlan(
            ResponseAct.VISUAL_REACTION,
            scene,
            (scene_reason, "visual_input"),
        )
    if scene in (
        InteractionScene.DIRECT_ADDRESS,
        InteractionScene.REPLY_TO_BOT,
        InteractionScene.ACTIVE_CONTINUATION,
    ) and _is_bare_address(cleaned, aliases):
        return ResponseActPlan(
            ResponseAct.ACKNOWLEDGE,
            scene,
            (scene_reason, "bare_address"),
        )
    if _PLAYFUL.search(cleaned):
        return ResponseActPlan(
            ResponseAct.PLAYFUL_REPLY,
            scene,
            (scene_reason, "playful_signal"),
        )
    return ResponseActPlan(
        ResponseAct.ANSWER,
        scene,
        (scene_reason, "content_response"),
    )


def _is_bare_address(text: str, aliases: Sequence[str]) -> bool:
    normalized = _ALIAS_PADDING.sub("", text).casefold()
    if not normalized:
        return True
    normalized_aliases = {
        _ALIAS_PADDING.sub("", str(alias)).casefold()
        for alias in (aliases or ())
        if str(alias).strip()
    }
    return normalized in normalized_aliases


def _clean_facts(values: Optional[Sequence[str]]) -> Tuple[str, ...]:
    return tuple(
        cleaned
        for cleaned in (
            _clean_fact(item, max_chars=120) for item in (values or ())
        )
        if cleaned
    )


def _clean_fact(value: object, *, max_chars: int) -> str:
    return " ".join(str(value or "").split())[:max_chars]
