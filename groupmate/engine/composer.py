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
from ..core.response_act import ResponseActPlan
from ..models import OutboundKind, OutboundSegment, ResponseDraft


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
