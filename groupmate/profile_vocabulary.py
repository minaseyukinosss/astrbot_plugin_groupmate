"""Shared vocabulary for profile model output and local validation.

The model prompt and the deterministic validator must consume the same
values.  Keeping them here prevents a prompt change from silently drifting
away from the values accepted by the profile domain.
"""

from __future__ import annotations

import unicodedata


FACT_CATEGORIES = frozenset(
    {
        "identity",
        "preference",
        "dislike",
        "boundary",
        "interest",
        "skill",
        "speech_style",
        "behavior_pattern",
        "group_role",
    }
)
MODEL_FACT_SOURCE_KINDS = frozenset(
    {"self_statement", "observed_pattern", "third_party_claim"}
)
EPISODE_TYPES = frozenset(
    {
        "shared_achievement",
        "conflict",
        "support",
        "running_joke",
        "milestone",
        "notable_interaction",
        "custom",
    }
)
RELATION_TYPES = frozenset(
    {
        "frequent_interaction",
        "familiar",
        "supportive",
        "technical_peer",
        "teasing",
        "conflict",
        "avoidance",
        "custom",
    }
)
EDGE_DIRECTIONS = frozenset({"directed", "bidirectional"})
_IGNORABLE_CLAIM_PUNCTUATION = frozenset(
    "，。！？、,.!?；;：:…'\"“”‘’（）()【】[]{}《》<>"
)


def normalize_profile_claim(value: object) -> str:
    """Return a stable comparison key for one model-written claim.

    Model calls can vary harmless typography between batches.  We fold width
    and case, collapse whitespace, and ignore punctuation so those variations
    reinforce the same claim instead of creating parallel profile records.
    """

    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    # Keep symbols such as C#, C++, paths and hyphenated identifiers because
    # removing every Unicode punctuation mark would merge different skills.
    without_punctuation = "".join(
        character
        for character in normalized
        if character not in _IGNORABLE_CLAIM_PUNCTUATION
    )
    return " ".join(without_punctuation.split())


__all__ = (
    "EDGE_DIRECTIONS",
    "EPISODE_TYPES",
    "FACT_CATEGORIES",
    "MODEL_FACT_SOURCE_KINDS",
    "normalize_profile_claim",
    "RELATION_TYPES",
)
