"""Compose one ordered, scene-safe outbound draft."""

from __future__ import annotations

from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from ..capabilities.contracts import (
    CapabilityResult,
    CapabilityStatus,
    MediaCandidate,
)
from ..core.response_act import ResponseAct, ResponseActPlan
from ..models import OutboundKind, OutboundSegment, ResponseDraft


_DECORATIVE_ACTS = frozenset(
    {
        ResponseAct.RECIPROCATE,
        ResponseAct.PLAYFUL_REPLY,
        ResponseAct.VISUAL_REACTION,
    }
)
_SAFE_CAPABILITY_LABELS = frozenset(
    {"catalog_approved", "provider_approved", "reviewed", "safe"}
)


class ResponseComposer:
    def compose(
        self,
        *,
        text: str,
        act_plan: ResponseActPlan,
        quote_message_id: Optional[str],
        capability_result: Optional[CapabilityResult] = None,
        reaction: Optional[MediaCandidate] = None,
    ) -> ResponseDraft:
        if not isinstance(act_plan, ResponseActPlan):
            raise TypeError("act_plan must be a ResponseActPlan")
        segments = []
        cleaned_text = str(text or "").strip()
        if cleaned_text:
            segments.append(OutboundSegment(OutboundKind.TEXT, text=cleaned_text))

        if (
            capability_result is not None
            and capability_result.status is CapabilityStatus.SUCCESS
        ):
            for candidate in capability_result.media_candidates:
                if self._safe_capability_media(candidate):
                    segments.append(self._outbound_image(candidate))

        if self._safe_reaction(reaction, act_plan.act):
            segments.append(self._outbound_image(reaction))

        return ResponseDraft(
            segments=tuple(segments),
            quote_message_id=quote_message_id,
            response_act=act_plan.act,
            capability_name=act_plan.capability_name,
        )

    @staticmethod
    def _safe_capability_media(candidate: MediaCandidate) -> bool:
        return (
            isinstance(candidate, MediaCandidate)
            and candidate.media_kind == "image"
            and candidate.safety_label in _SAFE_CAPABILITY_LABELS
            and candidate.purpose != "decorative_reaction"
            and ResponseComposer._safe_media_ref(candidate.locator)
        )

    @staticmethod
    def _safe_reaction(
        candidate: Optional[MediaCandidate],
        act: ResponseAct,
    ) -> bool:
        if not isinstance(candidate, MediaCandidate) or act not in _DECORATIVE_ACTS:
            return False
        if (
            candidate.source != "local_reaction_catalog"
            or candidate.media_kind != "image"
            or candidate.purpose != "decorative_reaction"
            or candidate.safety_label != "catalog_approved"
        ):
            return False
        path = Path(candidate.locator)
        return path.is_absolute() and path.is_file()

    @staticmethod
    def _outbound_image(candidate: MediaCandidate) -> OutboundSegment:
        return OutboundSegment(
            OutboundKind.IMAGE,
            media_id=candidate.media_id,
            media_ref=candidate.locator,
        )

    @staticmethod
    def _safe_media_ref(locator: str) -> bool:
        parsed = urlparse(str(locator or ""))
        if parsed.scheme in ("http", "https"):
            return bool(parsed.netloc)
        path = Path(str(locator or ""))
        return path.is_absolute() and path.is_file()
