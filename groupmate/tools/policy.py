"""Deterministic authorization for host tool execution."""

from __future__ import annotations

from typing import Any

from .contracts import ToolDescriptor, ToolPolicyDecision, ToolRisk


class ToolPolicyEngine:
    """Keep risk and authorization decisions outside the language model."""

    def authorize(self, descriptor: ToolDescriptor, event: Any) -> ToolPolicyDecision:
        if not descriptor.compatible:
            return ToolPolicyDecision(
                False,
                descriptor.compatibility_reason or "tool_incompatible",
            )
        requires_admin = (
            descriptor.permission.lower() == "admin"
            or descriptor.risk in (ToolRisk.DANGEROUS, ToolRisk.UNKNOWN)
        )
        if requires_admin and not is_group_or_astrbot_admin(event):
            return ToolPolicyDecision(False, "admin_required")
        return ToolPolicyDecision(True, "allowed")


def is_group_or_astrbot_admin(event: Any) -> bool:
    checker = getattr(event, "is_admin", None)
    if callable(checker):
        try:
            if checker():
                return True
        except Exception:
            pass
    role = str(getattr(event, "role", "") or "").lower()
    if role in ("admin", "owner"):
        return True
    message_obj = getattr(event, "message_obj", None)
    raw = getattr(message_obj, "raw_message", None)
    sender = getattr(raw, "sender", None)
    if isinstance(raw, dict):
        sender = raw.get("sender")
    if isinstance(sender, dict):
        role = str(sender.get("role") or "").lower()
    else:
        role = str(getattr(sender, "role", "") or "").lower()
    return role in ("admin", "owner")
