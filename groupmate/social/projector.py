"""SocialEvent → RelationshipState 可重放投影。"""

from __future__ import annotations

from typing import Iterable, Mapping, Optional, Tuple

from ..core.favorability import SCORE_MAX, SCORE_MIN, clamp_score
from ..models import RelationshipState, SocialEvent, SocialEventKind

# 单次事件封顶：避免关键词大幅变脸
_DELTAS = {
    SocialEventKind.PRAISE: (1, 2, 0, 0),
    SocialEventKind.THANKS: (1, 2, 1, 0),
    SocialEventKind.HELP_REQUEST: (1, 0, 0, 0),
    SocialEventKind.HELPED: (1, 2, 2, 0),
    SocialEventKind.FRIENDLY_TEASE: (1, 1, 0, 0),
    SocialEventKind.CORRECTION: (0, -1, 0, 0),
    SocialEventKind.BOUNDARY_PUSH: (0, -3, -1, 8),
    SocialEventKind.HARASSMENT: (0, -8, -2, 15),
    SocialEventKind.APOLOGY: (0, 1, 1, -5),
    SocialEventKind.NEUTRAL: (1, 1, 0, 0),
}


def _clamp_dim(value: int) -> int:
    return max(SCORE_MIN, min(SCORE_MAX, int(value)))


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
        affinity = clamp_score(seed_affinity)
        trust = 0
        boundary = 0
        count = 0
        last_at = 0
        for event in events:
            if str(event.group_id) != str(group_id) or str(event.user_id) != str(user_id):
                continue
            df, da, dt, db = _DELTAS.get(event.kind, (0, 0, 0, 0))
            # soft NEUTRAL on soft_trigger historically +1; classifier already chose kind
            familiarity = _clamp_dim(familiarity + df)
            affinity = _clamp_dim(affinity + da)
            trust = _clamp_dim(trust + dt)
            boundary = _clamp_dim(boundary + db)
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
        soft_trigger: bool = False,
    ) -> RelationshipState:
        base = state or RelationshipState(
            group_id=event.group_id,
            user_id=event.user_id,
            configured_relationship=configured_relationship,
        )
        df, da, dt, db = _DELTAS.get(event.kind, (0, 0, 0, 0))
        if event.kind is SocialEventKind.NEUTRAL:
            da = 1 if soft_trigger else 2
        return RelationshipState(
            group_id=base.group_id,
            user_id=base.user_id,
            familiarity=_clamp_dim(base.familiarity + df),
            affinity=_clamp_dim(base.affinity + da),
            trust=_clamp_dim(base.trust + dt),
            boundary_pressure=_clamp_dim(base.boundary_pressure + db),
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


def affinity_for_persona(
    state: Optional[RelationshipState],
    *,
    configured_relationship: str = "",
    relationships: Optional[Mapping[str, Tuple[str, str]]] = None,
    user_id: str = "",
) -> Optional[int]:
    """Persona 只读 affinity 档位来源；人工配置不改数值，只影响展示。"""
    del configured_relationship, relationships, user_id
    if state is None:
        return None
    return int(state.affinity)
