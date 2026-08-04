"""Code-owned behavior policies, separated by runtime responsibility."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ParticipationPolicy:
    """Participation thresholds for repeated direct address."""

    direct_pressure_window_seconds: int = 600
    direct_pressure_nudge_count: int = 2
    direct_pressure_pester_count: int = 3


@dataclass(frozen=True)
class ConversationPolicy:
    """Per-group topic window and scheduling rules."""

    history_limit: int = 100
    debounce_min_seconds: float = 4.0
    debounce_max_seconds: float = 8.0
    topic_max_seconds: int = 12
    candidate_ttl_seconds: int = 20
    continuation_seconds: int = 90


@dataclass(frozen=True)
class ReplyPolicy:
    """Delivery-shape rules independent of reply content."""

    humanize_delay_enabled: bool = True
    max_reply_segments: int = 2


@dataclass(frozen=True)
class ResourcePolicy:
    """Deterministic resource and optional-send budgets."""

    open_send_hourly_limit: int = 6
    open_send_cooldown_seconds: int = 600
    generation_hourly_limit: int = 30
    vision_hourly_limit: int = 12


@dataclass(frozen=True)
class InteractionPolicy:
    """Code-owned host-interaction (poke) restraint rules."""

    poke_react_probability: float = 0.88
    poke_cooldown_seconds: int = 8
    poke_session_per_minute: int = 6
    poke_back_probability: float = 0.35
    poke_bystander_probability: float = 0.33
    poke_bystander_cooldown_seconds: int = 10
    poke_bystander_target: str = "victim"


@dataclass(frozen=True)
class BehaviorPolicy:
    """Internal behavior policy bundle injected into runtime components."""

    participation: ParticipationPolicy = field(default_factory=ParticipationPolicy)
    conversation: ConversationPolicy = field(default_factory=ConversationPolicy)
    reply: ReplyPolicy = field(default_factory=ReplyPolicy)
    resources: ResourcePolicy = field(default_factory=ResourcePolicy)
    interaction: InteractionPolicy = field(default_factory=InteractionPolicy)


__all__ = [
    "BehaviorPolicy",
    "ConversationPolicy",
    "InteractionPolicy",
    "ParticipationPolicy",
    "ReplyPolicy",
    "ResourcePolicy",
]
