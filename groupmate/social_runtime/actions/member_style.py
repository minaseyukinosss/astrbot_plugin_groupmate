"""Safe surface-only overlay for temporarily imitating a group member.

This module never decides what Aemeath wants to say.  It receives an already
published qualitative style asset and turns it into bounded wording guidance.
Identity, facts, stance, permissions, and capabilities remain owned by the
normal Persona and social-decision pipeline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..profile.speech_style import MemberSpeechStyle


@dataclass(frozen=True)
class MemberStyleOverlay:
    target_member_id: str
    target_display_name: str
    style_version: int
    expires_at: int
    directives: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in ("target_member_id", "target_display_name"):
            value = " ".join(str(getattr(self, name) or "").split())
            if not value or len(value) > 120:
                raise ValueError(f"{name} is invalid")
            object.__setattr__(self, name, value)
        if int(self.style_version) < 1 or int(self.expires_at) < 1:
            raise ValueError("member style overlay version and expiry must be positive")
        object.__setattr__(self, "style_version", int(self.style_version))
        object.__setattr__(self, "expires_at", int(self.expires_at))
        directives = tuple(
            dict.fromkeys(
                " ".join(str(item or "").split())
                for item in self.directives
                if str(item or "").strip()
            )
        )
        if not directives or len(directives) > 24 or any(
            len(item) > 240 for item in directives
        ):
            raise ValueError("member style overlay directives are invalid")
        object.__setattr__(self, "directives", directives)


class MemberStyleOverlayBuilder:
    """Convert a published asset to prompt-safe qualitative directives."""

    def build(
        self,
        style: MemberSpeechStyle,
        *,
        target_display_name: str,
        expires_at: int,
    ) -> MemberStyleOverlay:
        directives = (
            *(f"起句：{item}" for item in style.opening_patterns[:3]),
            *(f"推进：{item}" for item in style.progression_patterns[:3]),
            *(f"收尾：{item}" for item in style.closing_patterns[:3]),
            f"长度与节奏：{style.length_rhythm}",
            f"直接程度：{style.directness}",
            f"反对与纠正：{style.disagreement_style}",
            f"调侃：{style.play_style}",
            f"关心：{style.care_style}",
            f"称呼：{style.addressing_style}",
            f"语气词与标点：{style.particles_punctuation}",
            *(f"稳定方式：{item}" for item in style.stable_traits[:6]),
            *(f"偶发表现，不要过度使用：{item}" for item in style.occasional_traits[:4]),
        )
        return MemberStyleOverlay(
            target_member_id=style.member_id,
            target_display_name=target_display_name,
            style_version=style.version,
            expires_at=int(expires_at),
            directives=tuple(directives),
        )


class IdentityImitationGuard:
    """Reject obvious target impersonation while allowing a style reference."""

    _IDENTITY_FORMS = (
        r"我(?:就)?是{name}",
        r"本人(?:就)?是{name}",
        r"{name}本人(?:来|在)",
        r"叫我{name}",
    )
    def review(
        self, text: str, *, overlay: MemberStyleOverlay
    ) -> tuple[str, ...]:
        candidate = " ".join(str(text or "").split())
        if not candidate:
            return ("imitation_output_empty",)
        escaped = re.escape(overlay.target_display_name)
        violations = []
        if any(
            re.search(pattern.format(name=escaped), candidate)
            for pattern in self._IDENTITY_FORMS
        ):
            violations.append("imitation_target_identity_claim")
        return tuple(violations)


__all__ = (
    "IdentityImitationGuard",
    "MemberStyleOverlay",
    "MemberStyleOverlayBuilder",
)
