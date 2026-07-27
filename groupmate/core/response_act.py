"""Deterministic response acts selected from interaction scenes."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence, Tuple

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


@dataclass(frozen=True)
class ResponseActPlan:
    act: ResponseAct
    scene: InteractionScene
    reason_codes: Tuple[str, ...]
    required_information: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason_codes", tuple(self.reason_codes or ()))
        object.__setattr__(
            self,
            "required_information",
            tuple(self.required_information or ()),
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
    task_supported: bool = True,
    required_information: Sequence[str] = (),
) -> ResponseActPlan:
    """Choose one response act using only explicit, observable inputs."""

    missing = tuple(
        str(item).strip()
        for item in (required_information or ())
        if str(item).strip()
    )
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
            missing,
        )

    if scene is InteractionScene.TASK_REQUEST:
        if missing:
            return ResponseActPlan(
                ResponseAct.CLARIFY,
                scene,
                (scene_reason, "task_information_missing"),
                missing,
            )
        if task_supported:
            return ResponseActPlan(
                ResponseAct.TASK_HANDOFF,
                scene,
                (scene_reason, "task_supported"),
            )
        return ResponseActPlan(
            ResponseAct.TASK_UNSUPPORTED,
            scene,
            (scene_reason, "task_unsupported"),
        )

    cleaned = (text or "").strip()
    if has_visual:
        return ResponseActPlan(
            ResponseAct.VISUAL_REACTION,
            scene,
            (scene_reason, "visual_input"),
        )
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
