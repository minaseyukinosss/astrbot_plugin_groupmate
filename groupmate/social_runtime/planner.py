"""Deterministic construction of bounded plans after Governor selection."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Mapping, Optional

from .actions.contracts import ActionEdge, ActionNode, ActionPlan, PlanContext

if TYPE_CHECKING:
    from .governor import GovernorResult


class ActionPlanner:
    """Builds declarations only; it never sends a platform message."""

    def plan(
        self,
        text_intention: object,
        context: PlanContext,
        governor_result: Optional["GovernorResult"] = None,
    ) -> ActionPlan:
        intention_id = self._value(text_intention, "intention_id")
        if governor_result is not None:
            if governor_result.outcome != "ACT":
                raise ValueError("only an ACT governor result may be planned")
            if intention_id not in governor_result.selected_intention_ids:
                raise ValueError("intention was not selected by governor")

        expires_at = context.now + min(10, context.max_plan_duration)
        plan_identity = {
            "intention_id": intention_id,
            "scene_version": context.scene_version,
            "config_version": context.config_version,
            "persona_version": context.persona_version,
            "expires_at": expires_at,
        }
        digest = hashlib.sha256(
            json.dumps(plan_identity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:24]
        target_id = self._optional_value(text_intention, "target_id")
        topic_id = self._optional_value(text_intention, "topic_id")
        return ActionPlan(
            plan_id="plan:{0}".format(digest),
            correlation_id="intention:{0}".format(intention_id),
            group_id="",
            persona_id="",
            scene_version=context.scene_version,
            intention_ids=(intention_id,),
            audience=(target_id,) if target_id else (),
            topic_id=topic_id,
            origin="governor",
            nodes=(
                ActionNode(
                    "generate_text",
                    "GENERATE_TEXT",
                    "text_generator",
                    0,
                    context.now + min(5, context.max_plan_duration),
                    permission="generate_text",
                ),
                ActionNode(
                    "send_bundle",
                    "SEND_BUNDLE",
                    "bundle_delivery",
                    0,
                    expires_at,
                    permission="send_message",
                    visible=True,
                ),
            ),
            edges=(ActionEdge("generate_text", "send_bundle"),),
            constraints=("governor_act",),
            expires_at=expires_at,
        )

    @staticmethod
    def _value(intention: object, field: str) -> str:
        value = ActionPlanner._raw_value(intention, field)
        text = str(value or "").strip()
        if not text:
            raise ValueError("text intention requires {0}".format(field))
        return text

    @staticmethod
    def _optional_value(intention: object, field: str) -> Optional[str]:
        value = ActionPlanner._raw_value(intention, field)
        text = str(value or "").strip()
        return text or None

    @staticmethod
    def _raw_value(intention: object, field: str) -> object:
        if isinstance(intention, Mapping):
            return intention.get(field)
        return getattr(intention, field, None)


__all__ = ("ActionPlanner",)
