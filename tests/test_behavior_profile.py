"""爱弥斯结构化行为人格档案。"""

from dataclasses import FrozenInstanceError, fields

import pytest

from groupmate.persona.aemeath import (
    AEMEATH_PARTICIPATION_PROFILE,
    AffinityParticipationRule,
    AemeathPersonaProvider,
    ParticipationMotive,
    PersonaParticipationProfile,
)
from groupmate.social import AffinityBand, ResponsePosture


def test_participation_profile_is_immutable_and_has_no_score_controls():
    profile = AEMEATH_PARTICIPATION_PROFILE

    with pytest.raises(FrozenInstanceError):
        profile.identity_name = "别的人格"

    field_names = {item.name for item in fields(PersonaParticipationProfile)}
    assert not field_names.intersection(
        {"probability", "weight", "threshold", "extroversion"}
    )


def test_every_affinity_band_has_exactly_one_rule():
    profile = AEMEATH_PARTICIPATION_PROFILE

    assert len(profile.affinity_rules) == len(AffinityBand)
    assert {rule.band for rule in profile.affinity_rules} == set(AffinityBand)


def test_duplicate_or_missing_affinity_rules_fail_closed():
    profile = AEMEATH_PARTICIPATION_PROFILE
    duplicate = profile.affinity_rules[:-1] + (profile.affinity_rules[0],)

    with pytest.raises(ValueError, match="one rule per affinity band"):
        PersonaParticipationProfile(
            identity_name=profile.identity_name,
            motives=profile.motives,
            inhibitions=profile.inhibitions,
            boundary_policy=profile.boundary_policy,
            relationship_policy=profile.relationship_policy,
            question_policy=profile.question_policy,
            turn_policy=profile.turn_policy,
            affinity_rules=duplicate,
        )


def test_hostile_rule_blocks_relationship_seeking_motives():
    rule = AEMEATH_PARTICIPATION_PROFILE.rule_for_affinity(AffinityBand.HOSTILE)

    assert rule.response_posture is ResponsePosture.FIRM
    assert ParticipationMotive.CARE_WITH_EVIDENCE not in rule.allowed_motives
    assert ParticipationMotive.PLAY_WHEN_INVITED not in rule.allowed_motives
    assert ParticipationMotive.EXPRESS_RELEVANT_PREFERENCE not in rule.allowed_motives


def test_wary_rule_only_allows_necessary_contributions():
    rule = AEMEATH_PARTICIPATION_PROFILE.rule_for_affinity(AffinityBand.WARY)

    assert rule.allowed_motives == (
        ParticipationMotive.HELP_WHEN_CONCRETE,
        ParticipationMotive.CONNECT_GROUP_CONTEXT,
        ParticipationMotive.CONTINUE_OWNED_THREAD,
    )
    assert rule.response_posture is ResponsePosture.RESERVED


@pytest.mark.parametrize("band", (AffinityBand.FRIENDLY, AffinityBand.CLOSE))
def test_warm_relationship_motives_require_friendly_or_close_affinity(band):
    rule = AEMEATH_PARTICIPATION_PROFILE.rule_for_affinity(band)

    assert ParticipationMotive.CARE_WITH_EVIDENCE in rule.allowed_motives
    assert ParticipationMotive.CONTINUE_OWNED_THREAD in rule.allowed_motives


def test_affinity_rule_rejects_motive_outside_profile():
    profile = AEMEATH_PARTICIPATION_PROFILE

    with pytest.raises(ValueError, match="unknown motive"):
        PersonaParticipationProfile(
            identity_name=profile.identity_name,
            motives=tuple(
                motive
                for motive in profile.motives
                if motive is not ParticipationMotive.CARE_WITH_EVIDENCE
            ),
            inhibitions=profile.inhibitions,
            boundary_policy=profile.boundary_policy,
            relationship_policy=profile.relationship_policy,
            question_policy=profile.question_policy,
            turn_policy=profile.turn_policy,
            affinity_rules=profile.affinity_rules,
        )


def test_provider_exposes_fixed_profile_read_only():
    provider = AemeathPersonaProvider()

    assert provider.participation_profile is AEMEATH_PARTICIPATION_PROFILE
    with pytest.raises(AttributeError):
        provider.participation_profile = AEMEATH_PARTICIPATION_PROFILE
