"""Groupmate WebUI 插件页 API。"""

from __future__ import annotations

from typing import Any

PLUGIN_NAME = "astrbot_plugin_groupmate"


def _request_params(request: Any) -> Any:
    return getattr(request, "query", getattr(request, "args", {}))


def _param(params: Any, name: str, default: Any = None) -> Any:
    if isinstance(params, dict):
        return params.get(name, default)
    getter = getattr(params, "get", None)
    return getter(name, default) if callable(getter) else default


class GroupmateWebAPI:
    def __init__(self, bridge: Any) -> None:
        self.bridge = bridge

    def register(self, context: Any) -> None:
        register = getattr(context, "register_web_api", None)
        if register is None:
            return
        register(
            f"/{PLUGIN_NAME}/status",
            self.status,
            ["GET"],
            "Groupmate runtime status",
        )
        register(
            f"/{PLUGIN_NAME}/runtime",
            self.set_runtime,
            ["POST"],
            "Pause or resume Groupmate",
        )
        register(
            f"/{PLUGIN_NAME}/decisions",
            self.decisions,
            ["GET"],
            "List recent Groupmate decisions",
        )
        register(
            f"/{PLUGIN_NAME}/decisions/<decision_id>",
            self.decision_detail,
            ["GET"],
            "Decision stage trail",
        )
        register(
            f"/{PLUGIN_NAME}/cognition",
            self.cognition,
            ["GET"],
            "Groupmate cognition governance snapshot",
        )
        register(
            f"/{PLUGIN_NAME}/fun/status",
            self.fun_status,
            ["GET"],
            "Groupmate optional fun feature status",
        )
        register(
            f"/{PLUGIN_NAME}/fun/dynamic-card/refresh",
            self.refresh_dynamic_card,
            ["POST"],
            "Refresh dynamic group card now",
        )
        register(
            f"/{PLUGIN_NAME}/memories/<memory_id>/delete",
            self.delete_memory,
            ["POST"],
            "Delete a governed memory",
        )
        register(
            f"/{PLUGIN_NAME}/relationships/correct",
            self.correct_relationship,
            ["POST"],
            "Correct a relationship state",
        )
        register(
            f"/{PLUGIN_NAME}/members/correct",
            self.correct_member,
            ["POST"],
            "Correct a member address",
        )
        register(
            f"/{PLUGIN_NAME}/members/link",
            self.link_member,
            ["POST"],
            "Link a mistaken member identity",
        )
        register(
            f"/{PLUGIN_NAME}/continuity/<item_id>/status",
            self.correct_continuity,
            ["POST"],
            "Correct an ongoing continuity item",
        )
        register(
            f"/{PLUGIN_NAME}/continuity/followups/<event_id>/reject",
            self.reject_continuity_followup,
            ["POST"],
            "Reject a mistaken continuity follow-up match",
        )
        register(
            f"/{PLUGIN_NAME}/care/<care_id>/correct",
            self.correct_proactive_care,
            ["POST"],
            "Correct a proactive care target or event match",
        )
        register(
            f"/{PLUGIN_NAME}/commitments/<commitment_id>/status",
            self.correct_self_commitment,
            ["POST"],
            "Correct an Aemeath self commitment",
        )
        register(
            f"/{PLUGIN_NAME}/commitments/<commitment_id>/run",
            self.run_self_commitment,
            ["POST"],
            "Run an Aemeath self commitment now",
        )
        register(
            f"/{PLUGIN_NAME}/relationships/evidence/<event_id>/review",
            self.review_relationship_evidence,
            ["POST"],
            "Review pending relationship evidence",
        )
        register(
            f"/{PLUGIN_NAME}/relationships/evidence/<event_id>/reject",
            self.reject_relationship_evidence,
            ["POST"],
            "Reject relationship evidence and rebuild state",
        )
        register(
            f"/{PLUGIN_NAME}/governance/<action_id>/revert",
            self.revert_governance,
            ["POST"],
            "Revert a governance action",
        )

    async def status(self):
        from astrbot.api.web import json_response

        return json_response(self.bridge.status())

    async def set_runtime(self):
        from astrbot.api.web import error_response, json_response, request

        payload = await request.json(default={})
        if "paused" not in payload or not isinstance(payload.get("paused"), bool):
            return error_response("paused must be a boolean", status_code=400)
        self.bridge.paused = bool(payload["paused"])
        return json_response({"paused": self.bridge.paused})

    async def decisions(self):
        from astrbot.api.web import error_response, json_response, request

        params = _request_params(request)
        group_id = str(_param(params, "group_id", "") or "").strip() or None
        outcome = str(_param(params, "outcome", "all") or "all").strip()
        try:
            limit = int(_param(params, "limit", 50) or 50)
        except (TypeError, ValueError):
            return error_response("limit must be an integer", status_code=400)
        try:
            payload = self.bridge.list_decisions(
                group_id=group_id,
                outcome=outcome,
                limit=limit,
            )
        except ValueError as exc:
            return error_response(str(exc), status_code=400)
        return json_response(payload)

    async def decision_detail(self, decision_id: str):
        from astrbot.api.web import error_response, json_response

        decision_id = str(decision_id or "").strip()
        if not decision_id:
            return error_response("decision_id is required", status_code=400)
        payload = self.bridge.get_decision_trace(decision_id)
        if payload is None:
            return error_response("decision not found", status_code=404)
        return json_response(payload)

    async def cognition(self):
        from astrbot.api.web import json_response

        return json_response(self.bridge.cognition_snapshot())

    async def fun_status(self):
        from astrbot.api.web import json_response

        return json_response(self.bridge.fun_status())

    async def refresh_dynamic_card(self):
        from astrbot.api.web import error_response, json_response, request

        payload = await request.json(default={})
        group_id = str(payload.get("group_id") or "").strip()
        if not group_id:
            return error_response("group_id is required", status_code=400)
        event = await self.bridge.refresh_dynamic_card(group_id)
        if event is None:
            return error_response("dynamic card unavailable", status_code=409)
        return json_response(
            {
                "event_id": event.event_id,
                "feature_id": event.feature_id,
                "group_id": event.group_id,
                "public_value": event.public_value,
                "status": event.status,
                "error_code": event.error_code,
                "created_at": event.created_at,
                "expires_at": event.expires_at,
            }
        )

    async def delete_memory(self, memory_id: str):
        from astrbot.api.web import error_response, json_response, request

        memory_id = str(memory_id or "").strip()
        if not memory_id:
            return error_response("memory_id is required", status_code=400)
        payload = await request.json(default={})
        if payload.get("confirm") is not True:
            return error_response("explicit confirmation is required", status_code=400)
        reason = str(payload.get("reason") or "").strip()
        if not reason:
            return error_response("reason is required", status_code=400)
        if len(reason) > 120:
            return error_response("reason is too long", status_code=400)
        action = self.bridge.delete_governed_memory(memory_id, reason)
        if action is None:
            return error_response("memory not found", status_code=404)
        return json_response(
            {"deleted": True, "memory_id": memory_id, "action": action}
        )

    async def correct_relationship(self):
        from astrbot.api.web import error_response, json_response, request

        payload = await request.json(default={})
        if payload.get("confirm") is not True:
            return error_response("explicit confirmation is required", status_code=400)
        group_id = str(payload.get("group_id") or "").strip()
        user_id = str(payload.get("user_id") or "").strip()
        if not group_id or not user_id:
            return error_response("group_id and user_id are required", status_code=400)
        fields = ("familiarity", "affinity", "trust", "boundary_pressure")
        try:
            values = {name: int(payload[name]) for name in fields}
        except (KeyError, TypeError, ValueError):
            return error_response(
                "relationship values must be integers", status_code=400
            )
        reason = str(payload.get("reason") or "").strip()
        if not reason:
            return error_response("reason is required", status_code=400)
        if len(reason) > 120:
            return error_response("reason is too long", status_code=400)
        result = self.bridge.correct_relationship(
            group_id=group_id,
            user_id=user_id,
            reason=reason,
            **values,
        )
        return json_response({"corrected": True, **result})

    async def correct_member(self):
        from astrbot.api.web import error_response, json_response, request

        payload = await request.json(default={})
        if payload.get("confirm") is not True:
            return error_response("explicit confirmation is required", status_code=400)
        group_id = str(payload.get("group_id") or "").strip()
        subject_id = str(payload.get("subject_id") or "").strip()
        preferred_address = str(payload.get("preferred_address") or "").strip()
        reason = str(payload.get("reason") or "").strip()
        if not group_id or not subject_id:
            return error_response("group_id and subject_id are required", status_code=400)
        if len(preferred_address) > 80:
            return error_response("preferred_address is too long", status_code=400)
        if not reason:
            return error_response("reason is required", status_code=400)
        if len(reason) > 120:
            return error_response("reason is too long", status_code=400)
        try:
            result = self.bridge.correct_member_profile(
                group_id=group_id,
                subject_id=subject_id,
                preferred_address=preferred_address,
                reason=reason,
            )
        except KeyError:
            return error_response("member profile not found", status_code=404)
        return json_response({"corrected": True, **result})

    async def correct_proactive_care(self, care_id: str):
        from astrbot.api.web import error_response, json_response, request

        care_id = str(care_id or "").strip()
        if not care_id:
            return error_response("care_id is required", status_code=400)
        payload = await request.json(default={})
        if payload.get("confirm") is not True:
            return error_response("explicit confirmation is required", status_code=400)
        reason = str(payload.get("reason") or "").strip()
        if not reason:
            return error_response("reason is required", status_code=400)
        if len(reason) > 160:
            return error_response("reason is too long", status_code=400)
        result = self.bridge.correct_proactive_care(care_id, reason)
        if result is None:
            return error_response("care decision not found or already corrected", status_code=404)
        return json_response({"corrected": True, "care_id": care_id})

    async def link_member(self):
        from astrbot.api.web import error_response, json_response, request

        payload = await request.json(default={})
        if payload.get("confirm") is not True:
            return error_response("explicit confirmation is required", status_code=400)
        group_id = str(payload.get("group_id") or "").strip()
        source_subject_id = str(payload.get("source_subject_id") or "").strip()
        canonical_subject_id = str(payload.get("canonical_subject_id") or "").strip()
        reason = str(payload.get("reason") or "").strip()
        if not group_id or not source_subject_id or not canonical_subject_id:
            return error_response("member identity fields are required", status_code=400)
        if not reason:
            return error_response("reason is required", status_code=400)
        if len(reason) > 120:
            return error_response("reason is too long", status_code=400)
        try:
            action = self.bridge.link_member_identity(
                group_id=group_id,
                source_subject_id=source_subject_id,
                canonical_subject_id=canonical_subject_id,
                reason=reason,
            )
        except KeyError:
            return error_response("member profile not found", status_code=404)
        except ValueError as exc:
            return error_response(str(exc), status_code=409)
        return json_response({"linked": True, "action": action})

    async def correct_continuity(self, item_id: str):
        from astrbot.api.web import error_response, json_response, request

        item_id = str(item_id or "").strip()
        if not item_id:
            return error_response("item_id is required", status_code=400)
        payload = await request.json(default={})
        if payload.get("confirm") is not True:
            return error_response("explicit confirmation is required", status_code=400)
        status = str(payload.get("status") or "").strip()
        if status not in {"open", "completed", "cancelled", "deleted"}:
            return error_response("unsupported continuity status", status_code=400)
        reason = str(payload.get("reason") or "").strip()
        if not reason:
            return error_response("reason is required", status_code=400)
        if len(reason) > 120:
            return error_response("reason is too long", status_code=400)
        try:
            result = self.bridge.correct_continuity_status(
                item_id=item_id,
                status=status,
                reason=reason,
            )
        except ValueError as exc:
            return error_response(str(exc), status_code=409)
        if result is None:
            return error_response("continuity item not found", status_code=404)
        return json_response({"corrected": True, **result})

    async def correct_self_commitment(self, commitment_id: str):
        from astrbot.api.web import error_response, json_response, request

        commitment_id = str(commitment_id or "").strip()
        if not commitment_id:
            return error_response("commitment_id is required", status_code=400)
        payload = await request.json(default={})
        if payload.get("confirm") is not True:
            return error_response("explicit confirmation is required", status_code=400)
        status = str(payload.get("status") or "").strip()
        if status not in {
            "pending",
            "in_progress",
            "completed",
            "blocked",
            "withdrawn",
            "deleted",
        }:
            return error_response("unsupported self commitment status", status_code=400)
        reason = str(payload.get("reason") or "").strip()
        if not reason:
            return error_response("reason is required", status_code=400)
        if len(reason) > 120:
            return error_response("reason is too long", status_code=400)
        try:
            result = self.bridge.correct_self_commitment_status(
                commitment_id=commitment_id,
                status=status,
                reason=reason,
            )
        except ValueError as exc:
            return error_response(str(exc), status_code=409)
        if result is None:
            return error_response("self commitment not found", status_code=404)
        return json_response({"corrected": True, **result})

    async def reject_continuity_followup(self, event_id: str):
        from astrbot.api.web import error_response, json_response, request

        event_id = str(event_id or "").strip()
        if not event_id:
            return error_response("event_id is required", status_code=400)
        payload = await request.json(default={})
        if payload.get("confirm") is not True:
            return error_response("explicit confirmation is required", status_code=400)
        reason = str(payload.get("reason") or "").strip()
        if not reason:
            return error_response("reason is required", status_code=400)
        if len(reason) > 120:
            return error_response("reason is too long", status_code=400)
        try:
            action = self.bridge.reject_continuity_followup(event_id, reason)
        except ValueError as exc:
            return error_response(str(exc), status_code=409)
        if action is None:
            return error_response("continuity follow-up not found", status_code=404)
        return json_response({"rejected": True, "event_id": event_id, "action": action})

    async def run_self_commitment(self, commitment_id: str):
        from astrbot.api.web import error_response, json_response, request

        commitment_id = str(commitment_id or "").strip()
        if not commitment_id:
            return error_response("commitment_id is required", status_code=400)
        payload = await request.json(default={})
        if payload.get("confirm") is not True:
            return error_response("explicit confirmation is required", status_code=400)
        try:
            result = await self.bridge.run_self_commitment_now(commitment_id)
        except KeyError:
            return error_response("self commitment not found", status_code=404)
        except ValueError as exc:
            return error_response(str(exc), status_code=409)
        return json_response({"started": True, **result})

    async def revert_governance(self, action_id: str):
        from astrbot.api.web import error_response, json_response, request

        action_id = str(action_id or "").strip()
        if not action_id:
            return error_response("action_id is required", status_code=400)
        payload = await request.json(default={})
        if payload.get("confirm") is not True:
            return error_response("explicit confirmation is required", status_code=400)
        reason = str(payload.get("reason") or "").strip()
        if not reason:
            return error_response("reason is required", status_code=400)
        if len(reason) > 120:
            return error_response("reason is too long", status_code=400)
        try:
            action = self.bridge.revert_governance_action(action_id, reason)
        except KeyError:
            return error_response("governance action not found", status_code=404)
        except ValueError as exc:
            return error_response(str(exc), status_code=409)
        return json_response({"reverted": True, "action": action})

    async def reject_relationship_evidence(self, event_id: str):
        from astrbot.api.web import error_response, json_response, request

        event_id = str(event_id or "").strip()
        if not event_id:
            return error_response("event_id is required", status_code=400)
        payload = await request.json(default={})
        if payload.get("confirm") is not True:
            return error_response("explicit confirmation is required", status_code=400)
        reason = str(payload.get("reason") or "").strip()
        if not reason:
            return error_response("reason is required", status_code=400)
        if len(reason) > 120:
            return error_response("reason is too long", status_code=400)
        try:
            action = self.bridge.reject_relationship_evidence(event_id, reason)
        except ValueError as exc:
            return error_response(str(exc), status_code=409)
        if action is None:
            return error_response("relationship evidence not found", status_code=404)
        return json_response(
            {"rejected": True, "event_id": event_id, "action": action}
        )

    async def review_relationship_evidence(self, event_id: str):
        from astrbot.api.web import error_response, json_response, request

        event_id = str(event_id or "").strip()
        if not event_id:
            return error_response("event_id is required", status_code=400)
        payload = await request.json(default={})
        if payload.get("confirm") is not True:
            return error_response("explicit confirmation is required", status_code=400)
        outcome = str(payload.get("outcome") or "").strip()
        if outcome not in {
            "correct",
            "wrong_person",
            "wrong_kind",
            "insufficient_context",
        }:
            return error_response("unsupported review outcome", status_code=400)
        reason = str(payload.get("reason") or "").strip()
        if not reason:
            return error_response("reason is required", status_code=400)
        if len(reason) > 120:
            return error_response("reason is too long", status_code=400)
        try:
            action = self.bridge.review_relationship_evidence(
                event_id, outcome, reason
            )
        except ValueError as exc:
            return error_response(str(exc), status_code=409)
        if action is None:
            return error_response("relationship evidence not found", status_code=404)
        return json_response(
            {"reviewed": True, "event_id": event_id, "action": action}
        )
