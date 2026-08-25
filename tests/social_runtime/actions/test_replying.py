from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from groupmate.social_runtime.contracts import RuntimeMode, SocialEventEnvelope
from groupmate.social_runtime.governor import GovernorResult
from groupmate.social_runtime.intentions import CandidateIntention
from groupmate.social_runtime.persona.profile import GroupmatePersonaProfile
from groupmate.social_runtime.persona.presets import AEMEATH_CURRENT_CANON
from groupmate.social_runtime.society.relationships import (
    PublicAffection,
    RelationshipStage,
)
from groupmate.social_runtime.replying import (
    ReplyExecutor,
    ReplyPlanRepository,
    ReplyPlanner,
)
from groupmate.social_runtime.delivery.outbox import OutboxService
from tests.factories import social_event_values


def _evaluation(trigger_kind="FAST", text="这个报错怎么看"):
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
                "text": text,
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
        participation_lane=(
            "CONTINUATION" if trigger_kind == "CONTINUATION" else "DIRECT_FAST"
        ),
    )


def _persona_profile():
    profile = GroupmatePersonaProfile.default().to_mapping()
    profile["identity"]["name"] = "爱弥斯"
    profile["canon"] = AEMEATH_CURRENT_CANON.to_mapping()
    return profile


def test_reply_planner_builds_one_short_text_plan():
    plan = ReplyPlanner().plan(
        _evaluation(), now=100, persona_profile=_persona_profile()
    )

    assert plan is not None
    assert plan.target_id == "u1"
    assert plan.platform_id == "onebot-main"
    assert plan.required is True
    assert plan.participation_lane == "DIRECT_FAST"
    assert plan.style.max_chars == 120
    assert plan.style.max_segments == 2
    assert plan.expression.persona_cues[0] == "爱弥斯"


def test_old_serialized_reply_plan_defaults_to_ambient_lane():
    plan = ReplyPlanner().plan(
        _evaluation(), now=100, persona_profile=_persona_profile()
    )
    values = json.loads(ReplyPlanRepository._encode(plan))
    values.pop("participation_lane")
    values.pop("expression")

    restored = ReplyPlanRepository._decode(json.dumps(values))

    assert restored.participation_lane == "AMBIENT"
    assert restored.expression.reaction_stance == "attentive"


def test_optional_generation_failure_stays_silent(tmp_path):
    class FailingModel:
        async def complete_text(self, **kwargs):
            raise RuntimeError("provider unavailable")

    repository = ReplyPlanRepository(tmp_path / "runtime.db")
    outbox = OutboxService(
        tmp_path / "runtime.db", bundle_authorizer=repository.authorizes_bundle
    )
    executor = ReplyExecutor(repository, outbox, FailingModel())
    plan = ReplyPlanner().plan(
        _evaluation(trigger_kind="AMBIENT"),
        now=100,
        persona_profile=_persona_profile(),
    )

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


def test_required_generation_fallback_is_not_usable_for_a_dialogue_lease(
    tmp_path,
):
    class FailingModel:
        async def complete_text(self, **kwargs):
            raise RuntimeError("provider unavailable")

    repository = ReplyPlanRepository(tmp_path / "runtime.db")
    outbox = OutboxService(
        tmp_path / "runtime.db", bundle_authorizer=repository.authorizes_bundle
    )
    executor = ReplyExecutor(repository, outbox, FailingModel())
    evaluation = _evaluation()
    plan = ReplyPlanner().plan(
        evaluation, now=100, persona_profile=_persona_profile()
    )

    result = asyncio.run(
        executor.execute_with_result(
            plan,
            context_events=evaluation.context_events,
            persona_profile={},
            recent_outputs=(),
        )
    )

    assert result.part is not None
    assert result.status == "MODEL_FAILED"
    assert result.usable_for_lease is False


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
    plan = ReplyPlanner().plan(
        evaluation, now=100, persona_profile=_persona_profile()
    )

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


def test_expression_uses_persona_cues_without_reference_bot_phrases():
    plan = ReplyPlanner().plan(
        _evaluation(), now=100, persona_profile=_persona_profile()
    )

    prompt = ReplyExecutor._system_prompt(plan, _persona_profile())

    assert "爱弥斯" in prompt
    assert "咪呀" not in prompt
    assert "花房" not in prompt
    assert "先接住对方的情绪和关系信号" in prompt


def test_prompt_does_not_dump_the_full_persona_material_pool():
    plan = ReplyPlanner().plan(
        _evaluation(text="小爱，陪我聊会儿"),
        now=100,
        persona_profile=_persona_profile(),
        relationship=PublicAffection(0.0, RelationshipStage.STRANGER),
        recent_outputs=(),
    )

    prompt = ReplyExecutor._system_prompt(plan, _persona_profile())

    assert '"explicit_material": null' in prompt
    assert "已重归现世" in prompt
    assert "隧者兵装" not in prompt
    assert "写歌" not in prompt
    assert "默认不要显式提及任何设定素材" in prompt


def test_prompt_gets_only_selected_relationship_memory_cues():
    plan = ReplyPlanner().plan(
        _evaluation(text="现在陪我聊天"),
        now=100,
        persona_profile=_persona_profile(),
        relationship=PublicAffection(-45.0, RelationshipStage.GUARDED),
        relationship_memory_cues=(
            "未修复边界事件：成员上次明确辱骂爱弥斯",
        ),
    )

    prompt = ReplyExecutor._system_prompt(plan, _persona_profile())

    assert "成员上次明确辱骂爱弥斯" in prompt
    assert "只有当前语境相关时才可简短引用" in prompt
    assert "relationship:boundary-1" not in prompt
