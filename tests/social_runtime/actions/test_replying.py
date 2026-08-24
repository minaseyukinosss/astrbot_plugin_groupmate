from __future__ import annotations

import asyncio
from types import SimpleNamespace

from groupmate.social_runtime.contracts import RuntimeMode, SocialEventEnvelope
from groupmate.social_runtime.governor import GovernorResult
from groupmate.social_runtime.intentions import CandidateIntention
from groupmate.social_runtime.replying import (
    ReplyExecutor,
    ReplyPlanRepository,
    ReplyPlanner,
)
from groupmate.social_runtime.delivery.outbox import OutboxService
from tests.factories import social_event_values


def _evaluation(trigger_kind="FAST"):
    candidate = CandidateIntention(
        intention_id="intention:help",
        kind="HELP",
        target_id="u1",
        topic_id="m1",
        evidence_event_ids=("qq:m1",),
        proposed_act="answer_help_request",
        obligation=1.0,
        relevance=1.0,
        relational_value=0.0,
        continuity_value=0.0,
        novelty=0.0,
        urgency=1.0,
        persona_fit=1.0,
        state_fit=1.0,
        information_gain=1.0,
        disruption_cost=0.0,
        uncertainty_cost=0.0,
        repetition_cost=0.0,
        resource_cost=0.0,
        risk=0.0,
        expires_at=130,
    )
    event = SocialEventEnvelope.create(
        **social_event_values(
            event_id="qq:m1",
            source_message_id="m1",
            actor_id="u1",
            correlation_id="corr:m1",
            payload={
                "text": "这个报错怎么看",
                "platform": "qq",
                "platform_id": "onebot-main",
                "session": "aiocqhttp:GroupMessage:885617919",
                "bot_id": "bot-1",
            },
        )
    )
    return SimpleNamespace(
        accepted=True,
        runtime_mode=RuntimeMode.SOCIAL_RUNTIME,
        frame=SimpleNamespace(
            frame_id="attention:1",
            trigger_kind=trigger_kind,
            candidate_audiences=("u1",),
            focus_topic_ids=("m1",),
        ),
        governor_result=GovernorResult(
            "ACT", (candidate.intention_id,), (), ("selected",), None, ()
        ),
        candidates=(candidate,),
        source_event=event,
        scene_version=1,
        config_version=1,
        persona_id="aemeath",
        context_events=(event,),
    )


def test_reply_planner_builds_one_short_text_plan():
    plan = ReplyPlanner().plan(_evaluation(), now=100)

    assert plan is not None
    assert plan.target_id == "u1"
    assert plan.platform_id == "onebot-main"
    assert plan.required is True
    assert plan.style.max_chars == 120
    assert plan.style.max_segments == 2


def test_optional_generation_failure_stays_silent(tmp_path):
    class FailingModel:
        async def complete_text(self, **kwargs):
            raise RuntimeError("provider unavailable")

    repository = ReplyPlanRepository(tmp_path / "runtime.db")
    outbox = OutboxService(
        tmp_path / "runtime.db", bundle_authorizer=repository.authorizes_bundle
    )
    executor = ReplyExecutor(repository, outbox, FailingModel())
    plan = ReplyPlanner().plan(_evaluation(trigger_kind="AMBIENT"), now=100)

    part = asyncio.run(
        executor.execute(
            plan,
            context_events=_evaluation().context_events,
            persona_profile={},
            recent_outputs=(),
        )
    )

    assert part is None
    assert repository.load(plan.plan_id).status == "silent"
    assert outbox.count() == 0


def test_shadow_preview_generates_reviewed_text_without_outbox(tmp_path):
    class Model:
        async def complete_text(self, **kwargs):
            return "这个报错先看最上面一行原因。"

    repository = ReplyPlanRepository(tmp_path / "runtime.db")
    outbox = OutboxService(
        tmp_path / "runtime.db", bundle_authorizer=repository.authorizes_bundle
    )
    executor = ReplyExecutor(repository, outbox, Model())
    evaluation = _evaluation()
    plan = ReplyPlanner().plan(evaluation, now=100)

    preview = asyncio.run(
        executor.preview(
            plan,
            context_events=evaluation.context_events,
            persona_profile={},
            recent_outputs=(),
        )
    )

    assert preview.status == "READY"
    assert preview.text == "这个报错先看最上面一行原因。"
    assert preview.diagnostic_code is None
    assert outbox.count() == 0
