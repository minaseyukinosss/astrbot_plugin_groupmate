"""Compose one ordered, scene-safe outbound draft."""

from __future__ import annotations

import random
from pathlib import Path
from typing import Callable, Optional, Sequence, Tuple
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
        policy = interaction or InteractionPolicy()
        poke_target, poke_count, drop_text = self._resolve_poke_plan(
            poke_back_enabled=poke_back_enabled,
            poke_role=poke_role,
            poke_target_user_id=poke_target_user_id,
            interaction=policy,
            affinity_band=affinity_band,
            act=act_plan.act,
            pressure=pressure,
            reason_codes=reason_codes,
            has_text=bool(cleaned_text),
        )
        if drop_text:
            cleaned_text = ""
        for _ in range(max(0, int(poke_count))):
            segments.append(
                OutboundSegment(OutboundKind.POKE, target_user_id=poke_target)
            )
        if cleaned_text:
            segments.append(OutboundSegment(OutboundKind.TEXT, text=cleaned_text))

        face_id = self._maybe_face_id(
            policy=policy,
            poke_role=poke_role,
            reason_codes=reason_codes,
            act=act_plan.act,
            has_payload=bool(segments),
        )
        if face_id:
            segments.append(
                OutboundSegment(OutboundKind.FACE, media_id=str(face_id))
            )

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

    def _resolve_poke_plan(
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
        has_text: bool,
    ) -> Tuple[str, int, bool]:
        """Return (target, poke_count, drop_text)."""
        if not poke_back_enabled:
            return "", 0, False
        target = str(poke_target_user_id or "").strip()
        if not target:
            return "", 0, False
        role = str(poke_role or "").strip().lower()
        reasons = set(str(item) for item in reason_codes or ())
        is_bystander = role == "bystander" or "poke_bystander" in reasons
        is_direct = role == "direct" or "poke_direct" in reasons
        if not is_bystander and not is_direct:
            return "", 0, False

        if is_bystander:
            count = self._burst_count(
                interaction=interaction,
                affinity_band=affinity_band,
                pressure=pressure,
                allow_burst=True,
            )
            return target, count, True

        if act is ResponseAct.BOUNDARY:
            return "", 0, False
        if affinity_band in (AffinityBand.HOSTILE, AffinityBand.WARY):
            return "", 0, False
        level = (
            pressure.level
            if pressure is not None
            else DirectAddressPressureLevel.NORMAL
        )
        if level in (
            DirectAddressPressureLevel.PESTER,
            DirectAddressPressureLevel.AFTER_BOUNDARY,
        ):
            return "", 0, False

        poke_chance = max(0.0, min(1.0, float(interaction.poke_back_probability)))
        if poke_chance <= 0:
            return "", 0, False
        if self._rng() >= poke_chance:
            return "", 0, False

        only_share = max(0.0, min(1.0, float(interaction.poke_only_share)))
        # Low rolls keep poke+text; high rolls (top only_share) drop to poke-only.
        drop_text = (not has_text) or (
            only_share > 0 and self._rng() >= (1.0 - only_share)
        )
        count = self._burst_count(
            interaction=interaction,
            affinity_band=affinity_band,
            pressure=pressure,
            allow_burst=True,
        )
        return target, count, drop_text

    def _maybe_face_id(
        self,
        *,
        policy: InteractionPolicy,
        poke_role: str,
        reason_codes: Sequence[str],
        act: ResponseAct,
        has_payload: bool,
    ) -> str:
        if not has_payload or act is ResponseAct.BOUNDARY:
            return ""
        probability = max(0.0, min(1.0, float(policy.poke_face_probability)))
        if probability <= 0:
            return ""
        reasons = set(str(item) for item in reason_codes or ())
        role = str(poke_role or "").strip().lower()
        is_poke = (
            role in {"direct", "bystander"}
            or "poke_direct" in reasons
            or "poke_bystander" in reasons
        )
        if not is_poke:
            return ""
        # High rolls trigger rare face; rng=0 keeps tests face-free.
        if self._rng() < (1.0 - probability):
            return ""
        pool = tuple(
            str(item).strip()
            for item in (policy.poke_face_pool or ())
            if str(item).strip()
        )
        if not pool:
            return ""
        index = min(len(pool) - 1, int(self._rng() * len(pool)))
        return pool[index]

    def _burst_count(
        self,
        *,
        interaction: InteractionPolicy,
        affinity_band: Optional[AffinityBand],
        pressure: Optional[DirectAddressPressureState],
        allow_burst: bool,
    ) -> int:
        max_burst = max(1, int(interaction.poke_burst_max or 1))
        if not allow_burst or max_burst <= 1:
            return 1
        if affinity_band not in (AffinityBand.FRIENDLY, AffinityBand.CLOSE):
            return 1
        level = (
            pressure.level
            if pressure is not None
            else DirectAddressPressureLevel.NORMAL
        )
        if level is not DirectAddressPressureLevel.NORMAL:
            return 1
        burst_prob = max(0.0, min(1.0, float(interaction.poke_burst_probability)))
        # High rolls trigger rare double-poke; rng=0 keeps single poke in tests.
        if burst_prob <= 0 or self._rng() < (1.0 - burst_prob):
            return 1
        return max_burst

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
