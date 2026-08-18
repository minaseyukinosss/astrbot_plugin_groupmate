"""Deterministic construction of bounded plans after Governor selection."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Mapping

from .actions.contracts import (
    MAX_ACTION_PLAN_DURATION,
    ActionEdge,
    ActionNode,
    ActionPlan,
    PlanContext,
)

if TYPE_CHECKING:
    from .governor import GovernorResult


class ActionPlanner:
    """Builds declarations only; it never sends a platform message."""

    def plan(
        self,
        text_intention: object,
        context: PlanContext,
        governor_result: "GovernorResult",
    ) -> ActionPlan:
        intention_id = self._value(text_intention, "intention_id")
        if governor_result.outcome != "ACT":
            raise ValueError("only an ACT governor result may be planned")
        if intention_id not in governor_result.selected_intention_ids:
            raise ValueError("intention was not selected by governor")
        if not context.group_id.strip():
            raise ValueError("group_id is required for an action plan")
        if not context.persona_id.strip():
            raise ValueError("persona_id is required for an action plan")

        duration = min(10, context.max_plan_duration, MAX_ACTION_PLAN_DURATION)
        expires_at = context.now + duration
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
            group_id=context.group_id,
            persona_id=context.persona_id,
            scene_version=context.scene_version,
            config_version=context.config_version,
            persona_version=context.persona_version,
            constitution_version=context.constitution_version,
            relationship_version=context.relationship_version,
            state_version=context.state_version,
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
            constitution_approved=context.constitution_allowed,
            relationship_approved=context.relationship_allowed,
            state_approved=context.state_allowed,
            risk_score=0,
            media_references=(),
            budget_cost=0,
            concurrency=1,
            confirmation_ids=(),
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
