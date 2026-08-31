from __future__ import annotations

import asyncio
from dataclasses import replace

from groupmate.social_runtime.attention import AttentionFrame
from groupmate.social_runtime.contracts import RuntimeMode, SocialEventEnvelope
from groupmate.social_runtime.governor import GovernorContext, SocialGovernor
from groupmate.social_runtime.intentions import CandidateIntention
from groupmate.social_runtime.manager import ShadowEvaluation, SocialRuntimeManager
from groupmate.social_runtime.world import (
    ConversationLease,
    GroupActivity,
    GroupWorldState,
    PresenceHistory,
    SocialAtmosphere,
)
from tests.factories import social_event_values


def _candidate(intention_id, target_id, topic_id, relevance):
    return CandidateIntention(
        intention_id=intention_id,
        kind="HELP",
        target_id=target_id,
        topic_id=topic_id,
        evidence_event_ids=(f"qq:{topic_id}",),
        proposed_act="answer_help_request",
        obligation=1,
        relevance=relevance,
        relational_value=1,
        continuity_value=1,
        novelty=1,
        urgency=1,
        persona_fit=1,
        state_fit=1,
        information_gain=1,
        disruption_cost=0,
        uncertainty_cost=0,
        repetition_cost=0,
        resource_cost=0,
        risk=0,
        expires_at=130,
    )


def test_parallel_topics_with_different_targets_never_merge_into_one_action():
    result = SocialGovernor().decide(
        (
            _candidate("project-help", "u1", "m1", 5),
            _candidate("dinner-help", "u2", "m2", 4),
        ),
        GovernorContext(
            now=100,
            scene_version=3,
            allowed_target_ids=("u1", "u2"),
            allowed_topic_ids=("m1", "m2"),
            privacy_allowed=True,
            boundary_active=False,
            paused=False,
            platform_available=True,
            capability_allowed=True,
            force_observe=False,
            rate_limited_until=None,
            minimum_utility=1,
        ),
    )

    assert result.outcome == "ACT"
    assert result.selected_intention_ids == ("project-help",)
    assert result.rejected[0].intention_id == "dinner-help"
    assert "different_target" in result.rejected[0].reason_codes


def _world(*, scene_version=3, lease_id="plan:lease", lease_target="u1"):
    return GroupWorldState(
        group_id="g1",
        scene_version=scene_version,
        active_topics=(),
        participants=(),
        interaction_edges=(),
        group_activity=GroupActivity(1, 1, 100),
        social_atmosphere=SocialAtmosphere("neutral", 1.0),
        bot_roles=(),
        pending_opportunities=(),
        running_tasks=(),
        open_loops=(),
        recent_presence=PresenceHistory(("qq:m1",), None),
        culture_version=0,
        conversation_lease=ConversationLease(
            target_id=lease_target,
            topic_id="topic:1",
            source_plan_id=lease_id,
            opened_at=90,
            expires_at=130,
            remaining_turns=3,
        ),
    )


def _continuation_evaluation(candidate=None):
    candidate = candidate or _candidate(
        "continue:1", "u1", "topic:1", relevance=5
    )
    event = SocialEventEnvelope.create(
        **social_event_values(
            event_id="qq:m1",
            source_message_id="m1",
            group_id="g1",
            actor_id="u1",
            occurred_at=100,
            received_at=100,
            correlation_id="corr:m1",
            payload={"text": "然后呢"},
        )
    )
    return ShadowEvaluation(
        persona_id="aemeath",
        request_id="request:1",
        runtime_mode=RuntimeMode.SHADOW,
        scene_version=3,
        config_version=1,
        frame=AttentionFrame(
            frame_id="frame:1",
            group_id="g1",
            scene_version=3,
            trigger_kind="CONTINUATION",
            focus_topic_ids=("topic:1",),
            focus_event_ids=("qq:m1",),
            candidate_audiences=("u1",),
            urgency="normal",
            deadline=100,
            requested_workers=(),
            persona_state_version=1,
            config_version=1,
        ),
        governor_result=SocialGovernor().decide(
            (candidate,),
            GovernorContext(
                now=100,
                scene_version=3,
                allowed_target_ids=(candidate.target_id,),
                allowed_topic_ids=(candidate.topic_id,),
                privacy_allowed=True,
                boundary_active=False,
                paused=False,
                platform_available=True,
                capability_allowed=True,
                force_observe=False,
                rate_limited_until=None,
                minimum_utility=1,
            ),
        ),
        source_event=event,
        context_events=(),
        candidates=(candidate,),
        accepted=True,
        status="accepted",
        participation_lane="CONTINUATION",
    )


def test_manager_scene_guard_distinguishes_scene_target_lease_and_expiry(tmp_path):
    now = [100]
    manager = SocialRuntimeManager(
        database_path=tmp_path / "groupmate-social-runtime-v2.db",
        persona_id="aemeath",
        mode=RuntimeMode.SHADOW,
        enabled_groups=("g1",),
        clock=lambda: now[0],
    )
    current_world = [_world()]

    async def snapshot(group_id):
        assert group_id == "g1"
        return current_world[0]

    manager.group_snapshot = snapshot
    evaluation = _continuation_evaluation()

    async def scenario():
        guard = await manager.freeze_scene_guard("g1", evaluation, now=100)
        guarded = replace(evaluation, knowledge_scene_guard=guard)
        assert (await manager.current_scene_guard("g1", guarded)).is_valid

        current_world[0] = _world(scene_version=4)
        scene = await manager.current_scene_guard("g1", guarded)
        current_world[0] = _world()

        changed_target = replace(
            guarded,
            candidates=(
                replace(guarded.candidates[0], target_id="u2"),
            ),
        )
        target = await manager.current_scene_guard("g1", changed_target)

        current_world[0] = _world(lease_id="plan:other")
        lease = await manager.current_scene_guard("g1", guarded)
        current_world[0] = _world()

        now[0] = guarded.candidates[0].expires_at + 1
        intention = await manager.current_scene_guard("g1", guarded)
        return scene, target, lease, intention

    scene, target, lease, intention = asyncio.run(scenario())
    assert scene.diagnostic_code == "scene_version_changed"
    assert target.diagnostic_code == "reply_target_changed"
    assert lease.diagnostic_code == "continuation_lease_changed"
    assert intention.diagnostic_code == "intention_expired"
