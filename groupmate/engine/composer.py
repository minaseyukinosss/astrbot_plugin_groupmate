"""Compose one ordered, scene-safe outbound draft."""

from __future__ import annotations

import random
from pathlib import Path
from typing import Callable, Optional, Sequence
from urllib.parse import urlparse

from ..capabilities.contracts import (
    CapabilityResult,
    CapabilityStatus,
    MediaCandidate,
)
from ..core.response_act import ResponseAct, ResponseActPlan
from ..models import OutboundKind, OutboundSegment, ResponseDraft
from ..policies import InteractionPolicy
from ..social.affinity import AffinityBand
from .direct_pressure import DirectAddressPressureLevel, DirectAddressPressureState


_SAFE_CAPABILITY_LABELS = frozenset(
    {"catalog_approved", "provider_approved", "reviewed", "safe"}
)


class ResponseComposer:
    def __init__(self, *, rng: Callable[[], float] = random.random) -> None:
        self._rng = rng

    def compose(
        self,
        *,
        text: str,
        act_plan: ResponseActPlan,
        quote_message_id: Optional[str],
        capability_result: Optional[CapabilityResult] = None,
        poke_back_enabled: bool = False,
        poke_role: str = "",
        poke_target_user_id: str = "",
        interaction: Optional[InteractionPolicy] = None,
        affinity_band: Optional[AffinityBand] = None,
        pressure: Optional[DirectAddressPressureState] = None,
        reason_codes: Sequence[str] = (),
    ) -> ResponseDraft:
        if not isinstance(act_plan, ResponseActPlan):
            raise TypeError("act_plan must be a ResponseActPlan")
        segments = []
        cleaned_text = str(text or "").strip()
        poke_target = self._resolve_poke_target(
            poke_back_enabled=poke_back_enabled,
            poke_role=poke_role,
            poke_target_user_id=poke_target_user_id,
            interaction=interaction or InteractionPolicy(),
            affinity_band=affinity_band,
            act=act_plan.act,
            pressure=pressure,
            reason_codes=reason_codes,
        )
        if poke_target:
            segments.append(
                OutboundSegment(OutboundKind.POKE, target_user_id=poke_target)
            )
            if str(poke_role or "").lower() == "bystander":
                cleaned_text = ""
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

    def _resolve_poke_target(
        self,
        *,
        poke_back_enabled: bool,
        poke_role: str,
        poke_target_user_id: str,
        interaction: InteractionPolicy,
        affinity_band: Optional[AffinityBand],
        act: ResponseAct,
        pressure: Optional[DirectAddressPressureState],
        reason_codes: Sequence[str],
    ) -> str:
        if not poke_back_enabled:
            return ""
        target = str(poke_target_user_id or "").strip()
        if not target:
            return ""
        role = str(poke_role or "").strip().lower()
        reasons = set(str(item) for item in reason_codes or ())
        if role == "bystander" or "poke_bystander" in reasons:
            return target
        if role != "direct" and "poke_direct" not in reasons:
            return ""
        if act is ResponseAct.BOUNDARY:
            return ""
        if affinity_band is AffinityBand.HOSTILE:
            return ""
        if affinity_band is AffinityBand.WARY:
            return ""
        level = (
            pressure.level
            if pressure is not None
            else DirectAddressPressureLevel.NORMAL
        )
        if level in (
            DirectAddressPressureLevel.PESTER,
            DirectAddressPressureLevel.AFTER_BOUNDARY,
        ):
            return ""
        probability = float(interaction.poke_back_probability)
        if probability <= 0:
            return ""
        if probability < 1.0 and self._rng() > probability:
            return ""
        return target

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
