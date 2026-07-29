"""SocialEvent → RelationshipState 可重放投影。"""

from __future__ import annotations

from typing import Iterable, Optional

from ..models import RelationshipState, SocialEvent, SocialEventKind
from .affinity import AFFINITY_MAX, clamp_affinity

# 事件必须由上游完整语境验证；普通互动只增加熟悉度。
_DELTAS = {
    SocialEventKind.PRAISE: (1, 2, 0, 0),
    SocialEventKind.THANKS: (1, 2, 1, 0),
    SocialEventKind.HELP_REQUEST: (1, 0, 0, 0),
    SocialEventKind.HELPED: (1, 3, 2, 0),
    SocialEventKind.FRIENDLY_TEASE: (1, 1, 0, 0),
    SocialEventKind.CORRECTION: (1, 0, 0, 0),
    SocialEventKind.BOUNDARY_PUSH: (0, -6, -2, 8),
    SocialEventKind.HARASSMENT: (0, -15, -5, 20),
    SocialEventKind.APOLOGY: (1, 2, 1, -5),
    SocialEventKind.NEUTRAL: (1, 0, 0, 0),
}


def _clamp_signed(value: int) -> int:
    return max(-AFFINITY_MAX, min(AFFINITY_MAX, int(value)))


def _clamp_non_negative(value: int) -> int:
    return max(0, min(AFFINITY_MAX, int(value)))


class SocialStateProjector:
    def project(
        self,
        events: Iterable[SocialEvent],
        *,
        group_id: str,
        user_id: str,
        configured_relationship: Optional[str] = None,
        seed_affinity: int = 0,
        now: int = 0,
    ) -> RelationshipState:
        familiarity = 0
        affinity = clamp_affinity(seed_affinity)
        trust = 0
        boundary = 0
        count = 0
        last_at = 0
        for event in events:
            if str(event.group_id) != str(group_id) or str(event.user_id) != str(user_id):
                continue
            df, da, dt, db = _DELTAS.get(event.kind, (0, 0, 0, 0))
            familiarity = _clamp_non_negative(familiarity + df)
            affinity = clamp_affinity(affinity + da)
            trust = _clamp_signed(trust + dt)
            boundary = _clamp_non_negative(boundary + db)
            count += 1
            last_at = max(last_at, int(event.occurred_at or 0))
        return RelationshipState(
            group_id=str(group_id),
            user_id=str(user_id),
            familiarity=familiarity,
            affinity=affinity,
            trust=trust,
            boundary_pressure=boundary,
            interaction_count=count,
            last_interaction_at=last_at,
            configured_relationship=configured_relationship,
            updated_at=int(now or last_at or 0),
        )

    def apply_event(
        self,
        state: Optional[RelationshipState],
        event: SocialEvent,
        *,
        configured_relationship: Optional[str] = None,
        now: int = 0,
    ) -> RelationshipState:
        base = state or RelationshipState(
            group_id=event.group_id,
            user_id=event.user_id,
            configured_relationship=configured_relationship,
        )
        df, da, dt, db = _DELTAS.get(event.kind, (0, 0, 0, 0))
        return RelationshipState(
            group_id=base.group_id,
            user_id=base.user_id,
            familiarity=_clamp_non_negative(base.familiarity + df),
            affinity=clamp_affinity(base.affinity + da),
            trust=_clamp_signed(base.trust + dt),
            boundary_pressure=_clamp_non_negative(base.boundary_pressure + db),
            interaction_count=int(base.interaction_count) + 1,
            last_interaction_at=max(
                int(base.last_interaction_at or 0), int(event.occurred_at or 0)
            ),
            configured_relationship=(
                configured_relationship
                if configured_relationship is not None
                else base.configured_relationship
            ),
            updated_at=int(now or event.occurred_at or base.updated_at or 0),
        )
