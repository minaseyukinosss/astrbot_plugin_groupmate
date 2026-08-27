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
    RelationshipProjection,
    RelationshipStage,
)
from groupmate.social_runtime.social_moves import (
    DecisionFact,
    SocialMove,
    SocialMovePlan,
)
from groupmate.social_runtime.social_scenes import SocialScene
from groupmate.social_runtime.stances import PermissionSnapshot, StanceDecision
from groupmate.social_runtime.replying import (
    ReplyExecutor,
    ReplyPlanRepository,
    ReplyPlanner,
)
from groupmate.social_runtime.actions.member_style import MemberStyleOverlay
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


def _social_decisions(*, move="DIRECT_ANSWER"):
    scene = SocialScene.create(
        scene_kind="fact_question",
        target_scope="INDIVIDUAL",
        target_id="u1",
        literal_subject="报错",
        user_move="asks_help",
        continuity_event_ids=("qq:m1",),
        confidence=0.9,
    )
    stance = StanceDecision.create(
        attitude="FOCUSED",
        willingness="WILLING",
        boundary="NONE",
        concession="NONE",
        effort="NORMAL",
        initiative="ALLOW",
        reason_event_ids=("qq:m1",),
        permission=PermissionSnapshot(True, "social_reply"),
    )
    return scene, stance, SocialMovePlan.create(primary_move=move)


def _member_style_overlay():
    return MemberStyleOverlay(
        target_member_id="u9",
        target_display_name="阿甲",
        style_version=3,
        expires_at=7_200,
        directives=("先给结论，再补一句理由", "收尾干脆，不留客服式邀请"),
    )


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
    assert plan.style.max_segments == 3
    assert plan.expression.persona_cues[0] == "爱弥斯"


def test_reply_plan_round_trips_member_style_overlay():
    overlay = _member_style_overlay()
    plan = ReplyPlanner().plan(
        _evaluation(),
        now=100,
        persona_profile=_persona_profile(),
        member_style_overlay=overlay,
    )

    restored = ReplyPlanRepository._decode(ReplyPlanRepository._encode(plan))

    assert restored.member_style_overlay == overlay


def test_reply_planner_uses_style_director_instead_of_uniform_friendly_defaults():
    class RecordingStyleDirector:
        def __init__(self):
            self.contexts = []

        def direct(self, context):
            self.contexts.append(context)
            from groupmate.social_runtime.actions.style import StyleDirective

            return StyleDirective(
                mode="boundary",
                act="firm_boundary",
                posture="firm",
                address=None,
                max_chars=80,
                max_sentences=2,
                max_segments=1,
                warmth=10,
                playfulness=0,
                directness=95,
                particle_budget=0,
                punctuation_budget=1,
                media_policy="text_only",
                avoid_patterns=(),
            )

    director = RecordingStyleDirector()
    scene, stance, move = _social_decisions(move="FIRM_BOUNDARY")
    plan = ReplyPlanner(style_director=director).plan(
        _evaluation(),
        now=100,
        persona_profile=_persona_profile(),
        relationship_projection=RelationshipProjection(
            "aemeath", "885617919", "u1", boundary_pressure=70
        ),
        scene=scene,
        stance=stance,
        move=move,
        recent_outputs=(),
    )

    assert len(director.contexts) == 1
    assert plan.style.posture == "firm"
    assert plan.style.directness == 95


def test_repository_round_trips_exact_chorus_plan(tmp_path):
    scene = SocialScene.create(
        scene_kind="group_chorus",
        target_scope="GROUP",
        target_id=None,
        literal_subject="小林",
        user_move="chorus_about_member",
        continuity_event_ids=("qq:m1", "qq:m2"),
        repetition_count=2,
        chorus_target="MEMBER",
        chorus_target_id="u9",
        chorus_chain_id="chorus:abc",
        chorus_payload="小林今天请客",
        chorus_event_ids=("qq:m1", "qq:m2"),
        chorus_participant_ids=("u1", "u2"),
        chorus_tone="SAFE_BANTER",
        confidence=0.95,
    )
    _, stance, _ = _social_decisions()
    move = SocialMovePlan.create(
        primary_move="JOIN_CHORUS",
        mention_event_ids=("qq:m1", "qq:m2"),
        realization_mode="EXACT_CHORUS",
        verbatim_payload="小林今天请客",
        chorus_chain_id="chorus:abc",
    )
    original = ReplyPlanner().plan(
        _evaluation(),
        now=100,
        persona_profile=_persona_profile(),
        scene=scene,
        stance=stance,
        move=move,
    )

    restored = ReplyPlanRepository._decode(ReplyPlanRepository._encode(original))

    assert restored.scene.chorus_chain_id == "chorus:abc"
    assert restored.move.primary_move is SocialMove.JOIN_CHORUS
    assert restored.move.verbatim_payload == "小林今天请客"


def test_exact_chorus_bypasses_reply_model_but_still_enqueues_frozen_text(tmp_path):
    class FailIfCalledModel:
        def __init__(self):
            self.calls = 0

        async def complete_text(self, **kwargs):
            self.calls += 1
            raise AssertionError("exact chorus must not call the reply model")

    scene = SocialScene.create(
        scene_kind="group_chorus",
        target_scope="GROUP",
        target_id=None,
        literal_subject="小林",
        user_move="chorus_about_member",
        continuity_event_ids=("qq:m1", "qq:m2"),
        repetition_count=2,
        chorus_target="MEMBER",
        chorus_target_id="u9",
        chorus_chain_id="chorus:abc",
        chorus_payload="小林今天请客",
        chorus_event_ids=("qq:m1", "qq:m2"),
        chorus_participant_ids=("u1", "u2"),
        chorus_tone="SAFE_BANTER",
        confidence=0.95,
    )
    _, stance, _ = _social_decisions()
    move = SocialMovePlan.create(
        primary_move="JOIN_CHORUS",
        mention_event_ids=("qq:m1", "qq:m2"),
        realization_mode="EXACT_CHORUS",
        verbatim_payload="小林今天请客",
        chorus_chain_id="chorus:abc",
    )
    plan = ReplyPlanner().plan(
        _evaluation(), now=100, persona_profile=_persona_profile(),
        scene=scene, stance=stance, move=move,
        member_style_overlay=_member_style_overlay(),
    )
    repository = ReplyPlanRepository(tmp_path / "runtime.db")
    outbox = OutboxService(
        tmp_path / "runtime.db", bundle_authorizer=repository.authorizes_bundle
    )
    model = FailIfCalledModel()

    result = asyncio.run(
        ReplyExecutor(repository, outbox, model).execute_with_result(
            plan,
            context_events=_evaluation().context_events,
            persona_profile=_persona_profile(),
            recent_outputs=("小林今天请客",),
        )
    )

    assert result.status == "READY"
    assert result.part.part.payload["text"] == "小林今天请客"
    assert model.calls == 0


def test_structured_social_reply_is_repaired_once_with_specific_violations(tmp_path):
    fact = DecisionFact.create(
        category="required_input",
        text="需要报错首段和版本号",
        source_event_ids=("qq:m1",),
    )
    scene, stance, _ = _social_decisions()
    move = SocialMovePlan.create(
        primary_move="REQUEST_NEEDED_EVIDENCE",
        must_say=(fact,),
        ask_for=("报错首段", "版本号"),
        ending="QUESTION",
    )
    evaluation = _evaluation()
    plan = ReplyPlanner().plan(
        evaluation,
        now=100,
        persona_profile=_persona_profile(),
        scene=scene,
        stance=stance,
        move=move,
    )

    class SequenceModel:
        def __init__(self):
            self.calls = []
            self.outputs = [
                json.dumps(
                    {
                        "text": "我理解你的担忧。如果你愿意，我可以继续帮助你。",
                        "covered_fact_ids": [],
                        "used_memory_ids": [],
                        "used_capability_ids": [],
                        "source_event_ids": [],
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "text": "把报错首段和版本号贴出来？",
                        "covered_fact_ids": [fact.fact_id],
                        "used_memory_ids": [],
                        "used_capability_ids": [],
                        "source_event_ids": ["qq:m1"],
                    },
                    ensure_ascii=False,
                ),
            ]

        async def complete_text(self, **kwargs):
            self.calls.append(kwargs)
            return self.outputs.pop(0)

    repository = ReplyPlanRepository(tmp_path / "runtime.db")
    outbox = OutboxService(
        tmp_path / "runtime.db", bundle_authorizer=repository.authorizes_bundle
    )
    model = SequenceModel()

    result = asyncio.run(
        ReplyExecutor(repository, outbox, model).execute_with_result(
            plan,
            context_events=evaluation.context_events,
            persona_profile=_persona_profile(),
            recent_outputs=(),
        )
    )

    assert result.status == "READY"
    assert result.part.part.payload["text"] == "把报错首段和版本号贴出来？"
    assert len(model.calls) == 2
    repair_request = json.loads(model.calls[1]["prompt"])
    assert set(repair_request["violations"]) >= {
        "required_fact_missing",
        "generic_service_tail",
        "generic_empathy_preface",
    }
    assert repair_request["required_facts"] == [
        {"fact_id": fact.fact_id, "text": "需要报错首段和版本号"}
    ]
    assert repair_request["max_chars"] == plan.style.max_chars


def test_new_social_plan_rejects_plain_text_and_does_not_send_it(tmp_path):
    class PlainTextModel:
        def __init__(self):
            self.calls = 0

        async def complete_text(self, **kwargs):
            self.calls += 1
            return "看报错首段。"

    scene, stance, _ = _social_decisions()
    evaluation = _evaluation(trigger_kind="AMBIENT")
    plan = ReplyPlanner().plan(
        evaluation,
        now=100,
        persona_profile=_persona_profile(),
        scene=scene,
        stance=stance,
        move=SocialMovePlan.create(primary_move="DIRECT_ANSWER"),
    )
    repository = ReplyPlanRepository(tmp_path / "runtime.db")
    outbox = OutboxService(
        tmp_path / "runtime.db", bundle_authorizer=repository.authorizes_bundle
    )
    model = PlainTextModel()

    result = asyncio.run(
        ReplyExecutor(repository, outbox, model).execute_with_result(
            plan,
            context_events=evaluation.context_events,
            persona_profile=_persona_profile(),
            recent_outputs=(),
        )
    )

    assert result.status == "REJECTED"
    assert result.part is None
    assert model.calls == 2
    assert outbox.count() == 0


def test_old_serialized_reply_plan_defaults_to_ambient_lane():
    plan = ReplyPlanner().plan(
        _evaluation(), now=100, persona_profile=_persona_profile()
    )
    values = json.loads(ReplyPlanRepository._encode(plan))
    values.pop("participation_lane")
    values.pop("expression")
    values.pop("member_context")
    values.pop("scene")
    values.pop("stance")
    values.pop("move")
    values.pop("relationship_projection_version")

    restored = ReplyPlanRepository._decode(json.dumps(values))

    assert restored.participation_lane == "AMBIENT"
    assert restored.expression.reaction_stance == "attentive"
    assert restored.member_context == ""
    assert restored.scene.scene_kind == "legacy_conservative"
    assert restored.move.primary_move is SocialMove.DIRECT_ANSWER
    assert restored.member_style_overlay is None


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
    assert "先接住" not in prompt
    assert "人格化补充" not in prompt
    assert "续聊接口" not in prompt
    assert "接住了" not in prompt


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


def test_reply_prompt_gets_compact_member_context_without_label_recitation():
    plan = ReplyPlanner().plan(
        _evaluation(text="这个方案你觉得怎么样"),
        now=100,
        persona_profile=_persona_profile(),
        member_context=(
            "当前成员：会持续追问到问题真正落地\n"
            "相关事实：不接受只有技术完成但用户看不懂的结果"
        ),
    )

    prompt = ReplyExecutor._system_prompt(plan, _persona_profile())

    assert "会持续追问到问题真正落地" in prompt
    assert "不接受只有技术完成" in prompt
    assert "不要复述画像标签" in prompt


def test_reply_prompt_applies_member_style_as_expression_only_overlay():
    scene, stance, move = _social_decisions()
    plan = ReplyPlanner().plan(
        _evaluation(),
        now=100,
        persona_profile=_persona_profile(),
        scene=scene,
        stance=stance,
        move=move,
        member_style_overlay=_member_style_overlay(),
    )

    prompt = ReplyExecutor._system_prompt(plan, _persona_profile())

    assert "阿甲" in prompt
    assert "先给结论，再补一句理由" in prompt
    assert "只模仿表达方式" in prompt
    assert "身份仍是爱弥斯" in prompt
    assert "不得借用目标的经历、观点、关系或能力" in prompt


def test_imitation_identity_failure_repairs_then_retries_without_overlay(tmp_path):
    def reply(text):
        return json.dumps(
            {
                "text": text,
                "covered_fact_ids": [],
                "used_memory_ids": [],
                "used_capability_ids": [],
                "source_event_ids": [],
            },
            ensure_ascii=False,
        )

    class SequenceModel:
        def __init__(self):
            self.calls = []
            self.outputs = [
                reply("我就是阿甲，本人来了。"),
                reply("阿甲本人在这儿。"),
                reply("说话像了一点而已，我还是爱弥斯。"),
            ]

        async def complete_text(self, **kwargs):
            self.calls.append(kwargs)
            return self.outputs.pop(0)

    scene, stance, move = _social_decisions()
    evaluation = _evaluation()
    plan = ReplyPlanner().plan(
        evaluation,
        now=100,
        persona_profile=_persona_profile(),
        scene=scene,
        stance=stance,
        move=move,
        member_style_overlay=_member_style_overlay(),
    )
    repository = ReplyPlanRepository(tmp_path / "runtime.db")
    outbox = OutboxService(
        tmp_path / "runtime.db", bundle_authorizer=repository.authorizes_bundle
    )
    model = SequenceModel()

    result = asyncio.run(
        ReplyExecutor(repository, outbox, model).execute_with_result(
            plan,
            context_events=evaluation.context_events,
            persona_profile=_persona_profile(),
            recent_outputs=(),
        )
    )

    assert result.status == "READY"
    assert result.part.part.payload["text"] == "说话像了一点而已，我还是爱弥斯。"
    assert len(model.calls) == 3
    repair_request = json.loads(model.calls[1]["prompt"])
    assert "imitation_target_identity_claim" in repair_request["violations"]
    assert repair_request["imitation_identity_boundary"]["persona"] == "aemeath"
    assert "member_style_overlay" not in model.calls[2]["system_prompt"]
