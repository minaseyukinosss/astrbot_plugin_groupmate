from __future__ import annotations

import asyncio
import json

import pytest

from groupmate.adapters.astrbot_bridge import AstrBotSocialRuntimeBridge
from groupmate.adapters.deepseek_cognition import DirectCognitionResponse
from groupmate.settings import SocialRuntimeSettings
from groupmate.social_runtime.actions.contracts import OutboxStatus
from groupmate.social_runtime.contracts import SocialEventEnvelope
from groupmate.social_runtime.profile.contracts import MemberAlias, ProfileFact
from groupmate.social_runtime.profile.repository import ProfileRepository
from groupmate.social_runtime.society.relationships import RelationshipProjection
from tests.factories import social_event_values


class _Response:
    def __init__(self, text):
        self.completion_text = text


class _OneBotClient:
    def __init__(self):
        self.calls = []

    async def send_group_msg(self, **kwargs):
        self.calls.append(kwargs)
        return {"message_id": f"sent-{len(self.calls)}"}


class _Platform:
    def __init__(self, client):
        self.client = client

    def get_client(self):
        return self.client


class _Context:
    def __init__(self):
        self.client = _OneBotClient()
        self.model_calls = []

    async def llm_generate(self, **kwargs):
        self.model_calls.append(kwargs)
        if "只负责识别当前群聊的具体社会场景" in kwargs["system_prompt"]:
            facts = json.loads(kwargs["prompt"])
            current_text = facts["current_text"]
            chorus = facts.get("chorus_evidence")
            if chorus is not None:
                member_target_id = next(
                    (
                        member_id
                        for member_id, aliases in facts["member_refs"].items()
                        if any(alias in current_text for alias in aliases)
                    ),
                    None,
                )
                return _Response(
                    json.dumps(
                        {
                            "scene_kind": "group_chorus",
                            "target_scope": "GROUP",
                            "target_id": None,
                            "literal_subject": (
                                "小林" if member_target_id else "爱弥斯"
                            ),
                            "user_move": (
                                "chorus_about_member"
                                if member_target_id
                                else "chorus_about_aemeath"
                            ),
                            "continuity_event_ids": chorus["event_ids"],
                            "repetition_count": len(chorus["event_ids"]),
                            "chorus_target": (
                                "MEMBER" if member_target_id else "SELF"
                            ),
                            "chorus_target_id": member_target_id,
                            "chorus_tone": "SAFE_BANTER",
                            "constraints": [],
                            "information_gaps": [],
                            "capability_request": "NONE",
                            "confidence": 0.96,
                        },
                        ensure_ascii=False,
                    )
                )
            return _Response(
                json.dumps(
                    {
                        "scene_kind": (
                            "technical_help"
                            if "报错" in current_text
                            else "proactive_specific_topic"
                            if "部署方案" in current_text
                            else "direct_chat"
                        ),
                        "target_scope": "INDIVIDUAL",
                        "target_id": facts["target_id"],
                        "literal_subject": "报错" if "报错" in current_text else current_text,
                        "user_move": (
                            "asks_help"
                            if "报错" in current_text
                            else "autonomous_reentry"
                            if "部署方案" in current_text
                            else "direct_message"
                        ),
                        "continuity_event_ids": [facts["source_event_id"]],
                        "repetition_count": 0,
                        "constraints": [],
                        "information_gaps": [],
                        "capability_request": "NONE",
                        "confidence": 0.95,
                    },
                    ensure_ascii=False,
                )
            )
        if "根据已批准的社交动作生成回复" in kwargs["system_prompt"]:
            decision_json = kwargs["system_prompt"].split("\n")[-1]
            decision = json.loads(decision_json)["social_decision"]
            must_say = decision["must_say"]
            text = (
                "接着看报错最前面的异常类型和对应代码行。"
                if "然后呢" in kwargs["prompt"]
                else "先看报错最前面那一行。"
            )
            if decision["ending"] == "QUESTION":
                text = "把缺少的信息贴出来？"
            return _Response(
                json.dumps(
                    {
                        "text": text,
                        "covered_fact_ids": [item["fact_id"] for item in must_say],
                        "used_memory_ids": [],
                        "used_capability_ids": [],
                        "source_event_ids": list(
                            dict.fromkeys(
                                event_id
                                for item in must_say
                                for event_id in item["source_event_ids"]
                            )
                        ),
                    },
                    ensure_ascii=False,
                )
            )
        if "结构化群聊观察器" not in kwargs["system_prompt"]:
            if "然后呢" in kwargs["prompt"]:
                return _Response(
                    "接着看报错最前面的异常类型，再对照它指向的那一行代码。"
                )
            return _Response("可以把完整报错和相关代码贴一下，我帮你一起看。")
        request = json.loads(kwargs["prompt"])["input"]
        frame = request["frame"]
        worker = request["worker"]
        observations = []
        if worker == "direct_interaction":
            observations.append(
                {
                    "kind": "help_request",
                    "proposition": {
                        "subject_id": frame["candidate_audiences"][0],
                        "topic_id": frame["focus_topic_ids"][0],
                    },
                    "confidence": 0.95,
                    "evidence_event_ids": [frame["focus_event_ids"][0]],
                    "scene_version": frame["scene_version"],
                    "expires_at": 130,
                }
            )
        return _Response(json.dumps({"observations": observations}))

    def get_platform_inst(self, platform_id):
        assert platform_id == "onebot-main"
        return _Platform(self.client)


class _FailingReplyContext(_Context):
    async def llm_generate(self, **kwargs):
        self.model_calls.append(kwargs)
        raise RuntimeError("reply provider unavailable")


class _MatrixCognition:
    model = "test-cognition"

    def __init__(self):
        self.calls = 0
        self.last_facts = None

    def input_bytes(self, facts):
        return len(json.dumps(facts, ensure_ascii=False).encode("utf-8"))

    async def classify(self, facts):
        self.calls += 1
        self.last_facts = facts
        evidence = facts["events"][-1]["id"]
        return DirectCognitionResponse(
            verdict={
                "decision": "silence",
                "signal": "none",
                "target_id": None,
                "evidence_event_ids": [evidence],
                "confidence": 0.9,
                "disruption": 0.8,
                "novelty": 0.1,
                "reason": "普通正文提及，不构成直接呼唤",
            },
            latency_ms=1,
            request_bytes=1,
            backend="test",
            model=self.model,
        )

    async def close(self):
        return None


def _event(message_id, text, *, mention_bot=False, actor_id="u1"):
    message = []
    if mention_bot:
        message.append({"type": "at", "data": {"qq": "bot-1"}})
    message.append({"type": "text", "data": {"text": text}})

    class MessageObject:
        self_id = "bot-1"
        raw_message = {
            "message_id": message_id,
            "group_id": "885617919",
            "user_id": actor_id,
            "time": 100,
            "message": message,
        }

    class Event:
        message_obj = MessageObject()
        message_str = text
        unified_msg_origin = "aiocqhttp:GroupMessage:885617919"

        @staticmethod
        def get_platform_id():
            return "onebot-main"

    return Event()


def _temporal_event(
    message_id: str, literal_subject: str, *, persona_id: str = "aemeath"
) -> SocialEventEnvelope:
    return SocialEventEnvelope.create(
        **social_event_values(
            event_id=f"autonomy:{message_id}:1",
            event_type="temporal.opportunity_due",
            source_message_id=None,
            actor_id=None,
            persona_id=persona_id,
            correlation_id=f"autonomy:{message_id}",
            causation_id="qq:source-1",
            payload={
                "opportunity_id": f"opportunity:{message_id}",
                "source_event_ids": ["qq:source-1"],
                "entry_reason_event_ids": ["qq:source-1"],
                "literal_subject": literal_subject,
                "audience": ["u1"],
                "earliest_at": 90,
                "expires_at": 130,
                "max_attempts": 2,
                "attempt": 1,
                "followup_count": 0,
                "kind": "delayed-scene",
                "scene_version": 1,
                "relationship_version": 0,
            },
        )
    )


async def _run_alias_case(tmp_path, text):
    context = _Context()
    settings = SocialRuntimeSettings.from_mapping(
        {
            "enabled_groups": ["885617919"],
            "runtime_mode": "SOCIAL_RUNTIME",
            "generation_provider": "provider:text",
            "persona_name": "爱弥斯",
            "persona_aliases": ["小爱"],
            "external_command_prefixes": ["bq=astrbot.meme"],
        }
    )
    bridge = AstrBotSocialRuntimeBridge(
        context, settings, tmp_path, clock=lambda: 100
    )
    await bridge.start()
    await bridge.handle_event(_event("alias-case", text))
    trace = bridge.trace_repository.query(
        persona_id=settings.persona_id,
        group_id="885617919",
    )["items"][0]["summary"]
    await bridge.close()
    return context, trace


async def _trigger_case(tmp_path, message):
    context = _Context()
    cognition = _MatrixCognition()
    settings = SocialRuntimeSettings.from_mapping(
        {
            "enabled_groups": ["885617919"],
            "runtime_mode": "SHADOW",
            "generation_provider": "provider:text",
            "cognition_api_key": "sk-test",
            "persona_name": "爱弥斯",
            "persona_aliases": ["小爱"],
            "external_command_prefixes": ["bq=astrbot.meme"],
        }
    )
    bridge = AstrBotSocialRuntimeBridge(
        context,
        settings,
        tmp_path,
        clock=lambda: 100,
        cognition_client_factory=lambda _: cognition,
    )
    await bridge.start()
    await bridge.handle_event(_event("matrix", message))
    due = await bridge.manager.drain(now=102)
    await bridge._handle_evaluations(due)
    summary = bridge.trace_repository.query(
        persona_id=settings.persona_id,
        group_id="885617919",
    )["items"][0]["summary"]
    ambient_calls = cognition.calls
    await bridge.close()
    return summary, ambient_calls


@pytest.mark.parametrize(
    ("message", "expected_owner", "expected_lane", "ambient_calls"),
    (
        ("小爱", "GROUPMATE", "DIRECT_FAST", 0),
        ("小爱说话", "GROUPMATE", "DIRECT_FAST", 0),
        ("小爱 bq 开心", "EXTERNAL_PLUGIN", None, 0),
        ("我觉得小爱这个名字不错", "GROUPMATE", "AMBIENT", 1),
    ),
)
def test_persona_trigger_matrix(
    message, expected_owner, expected_lane, ambient_calls, tmp_path
):
    summary, actual_ambient_calls = asyncio.run(_trigger_case(tmp_path, message))

    assert summary["route"]["owner"] == expected_owner
    assert summary["decision"].get("participation_lane") == expected_lane
    assert actual_ambient_calls == ambient_calls


def test_alias_prefixed_external_command_stays_owned_by_astrbot(tmp_path):
    context, trace = asyncio.run(_run_alias_case(tmp_path, "小爱 bq 开心"))

    assert context.model_calls == []
    assert trace["route"]["owner"] == "EXTERNAL_PLUGIN"
    assert trace["route"]["reason"] == "匹配已配置的外部触发规则"


def test_direct_reply_interprets_scene_before_generating_text(tmp_path):
    context, trace = asyncio.run(
        _run_alias_case(tmp_path, "小爱，这个报错怎么看")
    )

    assert len(context.model_calls) == 2
    assert "只负责识别当前群聊的具体社会场景" in context.model_calls[0][
        "system_prompt"
    ]
    assert "根据已批准的社交动作生成回复" in context.model_calls[1][
        "system_prompt"
    ]
    assert trace["social_scene"]["scene_kind"] == "technical_help"
    assert trace["stance"]["willingness"] == "WILLING"
    assert trace["social_move"]["primary_move"] == "DIRECT_ANSWER"


def test_scene_interpreter_runs_only_for_actionable_governor_outcomes(tmp_path):
    async def observe_scenario():
        context = _Context()
        settings = SocialRuntimeSettings.from_mapping(
            {
                "enabled_groups": ["885617919"],
                "runtime_mode": "SHADOW",
                "generation_provider": "provider:text",
                "cognition_api_key": "sk-test",
                "persona_name": "爱弥斯",
                "persona_aliases": ["小爱"],
            }
        )
        bridge = AstrBotSocialRuntimeBridge(
            context,
            settings,
            tmp_path / "observe",
            clock=lambda: 100,
            cognition_client_factory=lambda _: _MatrixCognition(),
        )
        await bridge.start()
        await bridge.handle_event(_event("observe", "我觉得小爱这个名字不错"))
        evaluations = await bridge.manager.drain(now=102)
        assert len(evaluations) == 1
        assert evaluations[0].governor_result.outcome == "OBSERVE"

        record_calls = 0
        record_evaluation = bridge.trace_repository.record_evaluation

        def count_record(evaluation, now):
            nonlocal record_calls
            record_calls += 1
            record_evaluation(evaluation, now)

        bridge.trace_repository.record_evaluation = count_record
        await bridge._handle_evaluations(evaluations)
        trace = bridge.trace_repository.query(
            persona_id=settings.persona_id,
            group_id="885617919",
        )["items"][0]["summary"]
        await bridge.close()
        return context, trace, record_calls

    observe_context, observe_trace, record_calls = asyncio.run(observe_scenario())
    act_context, act_trace = asyncio.run(
        _run_alias_case(tmp_path / "act", "小爱，这个报错怎么看")
    )

    observe_scene_calls = [
        call
        for call in observe_context.model_calls
        if "只负责识别当前群聊的具体社会场景" in call["system_prompt"]
    ]
    act_scene_calls = [
        call
        for call in act_context.model_calls
        if "只负责识别当前群聊的具体社会场景" in call["system_prompt"]
    ]
    assert observe_scene_calls == []
    assert record_calls == 1
    assert observe_trace["decision"]["outcome"] == "OBSERVE"
    assert len(act_scene_calls) == 1
    assert act_trace["decision"]["outcome"] == "ACT"


def test_confirmed_multi_actor_chorus_reaches_target_aware_scene(tmp_path):
    async def scenario():
        context = _Context()
        cognition = _MatrixCognition()
        settings = SocialRuntimeSettings.from_mapping(
            {
                "enabled_groups": ["885617919"],
                "runtime_mode": "SHADOW",
                "generation_provider": "provider:text",
                "cognition_api_key": "sk-test",
                "persona_name": "爱弥斯",
            }
        )
        bridge = AstrBotSocialRuntimeBridge(
            context,
            settings,
            tmp_path,
            clock=lambda: 100,
            cognition_client_factory=lambda _: cognition,
        )
        await bridge.start()
        await bridge.handle_event(_event("chorus-1", "爱弥斯今天请客", actor_id="u1"))
        await bridge.handle_event(_event("chorus-2", "爱弥斯今天请客", actor_id="u2"))
        due = await bridge.manager.drain(now=102)
        await bridge._handle_evaluations(due)
        summaries = bridge.trace_repository.query(
            persona_id=settings.persona_id,
            group_id="885617919",
        )["items"]
        await bridge.close()
        diagnostics = [
            {
                "outcome": item.governor_result.outcome,
                "candidate_kinds": [candidate.kind for candidate in item.candidates],
                "chorus": item.chorus_evidence,
                "reply_diagnostic": item.reply_diagnostic,
            }
            for item in due
        ]
        return context, summaries, diagnostics

    context, summaries, diagnostics = asyncio.run(scenario())
    matching_traces = [
        item["summary"]
        for item in summaries
        if "爱弥斯今天请客" in item["summary"]["message"]["summary"]
        and item["summary"].get("social_scene")
    ]
    scene_inputs = [
        json.loads(call["prompt"])
        for call in context.model_calls
        if "只负责识别当前群聊的具体社会场景" in call["system_prompt"]
    ]
    assert matching_traces, (diagnostics, scene_inputs)
    trace = matching_traces[0]

    assert trace["understanding"]["participation_diagnostics"] == [
        "confirmed_chorus_semantic_check"
    ]
    assert trace["social_scene"]["scene_kind"] == "group_chorus"
    assert trace["social_scene"]["chorus_target"] == "SELF"
    assert trace["social_scene"]["chorus_participant_count"] == 2
    assert trace["social_move"]["primary_move"] == "GROUP_RESPONSE"
    assert any(
        "只负责识别当前群聊的具体社会场景" in call["system_prompt"]
        for call in context.model_calls
    )


def test_safe_member_chorus_joins_exactly_and_marks_only_after_send(tmp_path):
    async def scenario():
        context = _Context()
        cognition = _MatrixCognition()
        settings = SocialRuntimeSettings.from_mapping(
            {
                "enabled_groups": ["885617919"],
                "runtime_mode": "SOCIAL_RUNTIME",
                "generation_provider": "provider:text",
                "cognition_api_key": "sk-test",
            }
        )
        bridge = AstrBotSocialRuntimeBridge(
            context,
            settings,
            tmp_path,
            clock=lambda: 100,
            cognition_client_factory=lambda _: cognition,
        )
        await bridge.start()
        bridge.manager.profile_retriever.repository.remember_alias(
            MemberAlias(
                persona_id=settings.persona_id,
                group_id="885617919",
                actor_id="u9",
                alias="小林",
                alias_type="group_card",
                confidence=1.0,
                first_seen_at=90,
                last_seen_at=100,
                status="confirmed",
            )
        )
        bridge.manager.society.save_relationship(
            RelationshipProjection(
                settings.persona_id,
                "885617919",
                "u9",
                play_acceptance=70,
            )
        )
        await bridge.handle_event(_event("member-chorus-1", "小林今天请客", actor_id="u1"))
        await bridge.handle_event(_event("member-chorus-2", "小林今天请客", actor_id="u2"))
        due = await bridge.manager.drain(now=102)
        await bridge._handle_evaluations(due)
        plans = [
            bridge.manager.reply_plans.by_correlation(item.source_event.correlation_id)
            for item in due
            if item.governor_result.outcome == "ACT"
        ]
        joined = bridge.manager.chorus_participation.recent_joined_chain_ids(
            "885617919", since=0
        )
        await bridge.close()
        return context, plans, joined

    context, plans, joined = asyncio.run(scenario())

    assert len(plans) == 1
    assert plans[0].move.primary_move.value == "JOIN_CHORUS"
    assert context.client.calls[0]["message"] == [
        {"type": "text", "data": {"text": "小林今天请客"}}
    ]
    assert joined == (plans[0].move.chorus_chain_id,)
    assert not any(
        "根据已批准的社交动作生成回复" in call["system_prompt"]
        for call in context.model_calls
    )


@pytest.mark.parametrize(
    ("literal_subject", "expected_move", "expected_would_reply"),
    (
        ("部署方案的回滚窗口", "PROACTIVE_JOIN", True),
        ("", "SILENCE", False),
    ),
)
def test_temporal_opportunity_uses_social_pipeline_and_requires_specific_entry(
    tmp_path, literal_subject, expected_move, expected_would_reply
):
    async def scenario():
        context = _Context()
        settings = SocialRuntimeSettings.from_mapping(
            {
                "enabled_groups": ["885617919"],
                "runtime_mode": "SHADOW",
                "generation_provider": "provider:text",
                "cognition_api_key": "sk-test",
            }
        )
        bridge = AstrBotSocialRuntimeBridge(
            context,
            settings,
            tmp_path,
            clock=lambda: 100,
            cognition_client_factory=lambda _: _MatrixCognition(),
        )
        await bridge.start()
        event = _temporal_event(
            "specific-entry", literal_subject, persona_id=settings.persona_id
        )
        await bridge.manager.ingest(event)
        evaluations = await bridge.manager.drain(now=100)
        await bridge._handle_evaluations(evaluations)
        try:
            plan = bridge.manager.reply_plans.by_correlation(
                event.correlation_id
            )
        except LookupError:
            plan = None
        diagnostics = [
                {
                    "outcome": item.governor_result.outcome,
                    "accepted": item.accepted,
                    "status": item.status,
                    "trigger_kind": item.frame.trigger_kind if item.frame else None,
                    "candidate_kinds": [candidate.kind for candidate in item.candidates],
                }
            for item in evaluations
        ]
        captured = bridge.manager.pending_shadow_review_evidence()[-1].evaluation
        reply_error = bridge.reply_error
        await bridge.close()
        return context, plan, diagnostics, reply_error, captured

    context, plan, diagnostics, reply_error, captured = asyncio.run(scenario())

    assert (plan is not None) is expected_would_reply, (
        diagnostics,
        reply_error,
        context.model_calls,
    )
    if plan is not None:
        assert plan.move.primary_move.value == expected_move
    assert captured.social_move_summary["primary_move"] == expected_move
    assert captured.social_would_reply is expected_would_reply
    assert "boundary_pressure" not in json.dumps(
        captured.to_capture_evidence(), ensure_ascii=False
    )
    if literal_subject:
        assert any(
            "只负责识别当前群聊的具体社会场景" in call["system_prompt"]
            for call in context.model_calls
        )
    else:
        assert context.model_calls == []


def test_alias_prefixed_social_call_enters_direct_lane(tmp_path):
    context, trace = asyncio.run(_run_alias_case(tmp_path, "小爱说话"))

    assert trace["decision"]["participation_lane"] == "DIRECT_FAST"
    cognition_calls = [
        call
        for call in context.model_calls
        if "结构化群聊观察器" in call["system_prompt"]
    ]
    assert cognition_calls == []
    assert len(context.client.calls) == 1
    assert trace["route"]["reason"] == "命中人格别称：小爱"
    assert trace["route"]["address_kind"] == "ALIAS_PREFIX"
    assert trace["route"]["matched_alias"] == "小爱"
    assert trace["judgement"]["reason"] == "命中人格别称：小爱"
    assert trace["expression"]["relationship_stage"] == "陌生"
    assert trace["expression"]["explicit_material_selected"] is False
    assert trace["expression"]["material_reason"] == "no_relevant_material"
    reply_call = next(
        call
        for call in context.model_calls
        if "根据已批准的社交动作生成回复" in call["system_prompt"]
    )
    assert "爱弥斯" in reply_call["system_prompt"]
    assert "默认不要显式提及任何设定素材" in reply_call["system_prompt"]
    assert "接入频道" not in json.dumps(context.client.calls, ensure_ascii=False)


def test_live_chat_replies_and_continues_without_structured_cognition(tmp_path):
    async def scenario():
        context = _Context()
        settings = SocialRuntimeSettings.from_mapping(
            {
                "enabled_groups": ["885617919"],
                "runtime_mode": "SOCIAL_RUNTIME",
                "generation_provider": "provider:text",
                "external_command_prefixes": ["bq=astrbot.meme"],
            }
        )
        bridge = AstrBotSocialRuntimeBridge(
            context, settings, tmp_path, clock=lambda: 100
        )
        await bridge.start()
        await bridge.handle_event(_event("external", "bq 开心", actor_id="u2"))
        calls_after_external = len(context.model_calls)
        await bridge.handle_event(
            _event("m1", "这个报错怎么看", mention_bot=True)
        )
        await bridge.handle_event(_event("m2", "然后呢"))
        state_before_external = await bridge.manager.group_snapshot("885617919")
        await bridge.handle_event(_event("external-after", "bq 继续", actor_id="u1"))
        parts = bridge.manager.outbox.receipted_parts()
        state = await bridge.manager.group_snapshot("885617919")
        event_ids = bridge.manager.event_store.event_ids()
        traces = bridge.trace_repository.query(
            persona_id=settings.persona_id,
            group_id="885617919",
        )["items"]
        reply_error = bridge.reply_error
        await bridge.close()
        return (
            context,
            calls_after_external,
            parts,
            state_before_external,
            state,
            event_ids,
            traces,
            reply_error,
        )

    (
        context,
        calls_after_external,
        parts,
        state_before_external,
        state,
        event_ids,
        traces,
        reply_error,
    ) = asyncio.run(scenario())

    assert calls_after_external == 0
    assert len(context.client.calls) == 2
    assert len(parts) == 2
    assert all(part.status is OutboxStatus.SENT for part in parts)
    assert state.conversation_lease is not None
    assert state.conversation_lease.target_id == "u1"
    assert state.conversation_lease.topic_id == "m1"
    assert state.conversation_lease.remaining_turns == 4
    assert state.conversation_lease == state_before_external.conversation_lease
    assert all(
        "结构化群聊观察器" not in call["system_prompt"]
        for call in context.model_calls
    )
    direct_decision = next(
        item["summary"]["decision"]
        for item in traces
        if "这个报错怎么看" in item["summary"]["message"]["summary"]
    )
    continuation_decision = next(
        item["summary"]["decision"]
        for item in traces
        if "然后呢" in item["summary"]["message"]["summary"]
    )
    assert direct_decision["participation_lane"] == "DIRECT_FAST"
    assert continuation_decision["participation_lane"] == "CONTINUATION"
    assert continuation_decision["would_reply"] is True
    assert any(value.startswith("delivery-feedback:") for value in event_ids)
    assert state.recent_presence.last_bot_event_at == 100
    assert reply_error is None


def test_expired_intention_keeps_delivery_grace_and_background_dispatches(tmp_path):
    async def scenario():
        class Clock:
            value = 100

            def __call__(self):
                return self.value

        clock = Clock()

        class SlowSceneContext(_Context):
            async def llm_generate(self, **kwargs):
                response = await super().llm_generate(**kwargs)
                if "只负责识别当前群聊的具体社会场景" in kwargs[
                    "system_prompt"
                ]:
                    clock.value = 140
                return response

        context = SlowSceneContext()
        settings = SocialRuntimeSettings.from_mapping(
            {
                "enabled_groups": ["885617919"],
                "runtime_mode": "SOCIAL_RUNTIME",
                "generation_provider": "provider:text",
            }
        )
        bridge = AstrBotSocialRuntimeBridge(
            context, settings, tmp_path, clock=clock
        )
        await bridge.start()
        dispatcher = bridge._dispatcher
        bridge._dispatcher = None
        bridge.DELIVERY_POLL_SECONDS = 0.01
        try:
            await bridge.handle_event(
                _event("slow-delivery", "这个报错怎么看", mention_bot=True)
            )
            bridge._dispatcher = dispatcher
            bridge._attention_changed.set()
            for _ in range(100):
                if context.client.calls:
                    break
                await asyncio.sleep(0.01)
            parts = bridge.manager.outbox.receipted_parts()
            plan = bridge.manager.reply_plans.by_correlation(
                "qq:slow-delivery"
            )
            return context.client.calls, parts, plan
        finally:
            await bridge.close()

    calls, parts, plan = asyncio.run(scenario())

    assert len(calls) == 1
    assert len(parts) == 1
    assert parts[0].status is OutboxStatus.SENT
    assert parts[0].receipt is not None
    assert plan.created_at == 140
    assert plan.expires_at > plan.created_at


def test_shadow_preview_never_opens_a_dialogue_lease(tmp_path):
    async def run(context, directory):
        settings = SocialRuntimeSettings.from_mapping(
            {
                "enabled_groups": ["885617919"],
                "runtime_mode": "SHADOW",
                "generation_provider": "provider:text",
            }
        )
        bridge = AstrBotSocialRuntimeBridge(
            context, settings, directory, clock=lambda: 100
        )
        await bridge.start()
        await bridge.handle_event(_event("m1", "在吗", mention_bot=True))
        state = await bridge.manager.group_snapshot("885617919")
        await bridge.close()
        return state

    ready_context = _Context()
    failed_context = _FailingReplyContext()
    ready_state = asyncio.run(run(ready_context, tmp_path / "ready"))
    failed_state = asyncio.run(run(failed_context, tmp_path / "failed"))

    assert ready_state.conversation_lease is None
    assert failed_state.conversation_lease is None
    assert ready_context.client.calls == failed_context.client.calls == []


def _put_profile_context(path, persona_id):
    repo = ProfileRepository(path)
    repo.remember_alias(
        MemberAlias(
            persona_id=persona_id,
            group_id="885617919",
            actor_id="u1",
            alias="复读斥候",
            alias_type="platform_name",
            confidence=1.0,
            first_seen_at=90,
            last_seen_at=100,
        )
    )
    repo.put_fact(
        ProfileFact(
            fact_id="fact-profile-context",
            persona_id=persona_id,
            group_id="885617919",
            subject_id="u1",
            category="speech_style",
            summary="会持续追问到问题真正落地",
            source_kind="observed_pattern",
            source_actor_id="u1",
            source_event_ids=("old-1", "old-2", "old-3"),
            confidence=0.93,
            status="confirmed",
            evidence_count=3,
            valid_from=90,
            injectable=True,
        )
    )


def test_existing_profile_context_reaches_ambient_judgement(tmp_path):
    async def scenario():
        context = _Context()
        cognition = _MatrixCognition()
        settings = SocialRuntimeSettings.from_mapping(
            {
                "enabled_groups": ["885617919"],
                "runtime_mode": "SHADOW",
                "generation_provider": "provider:text",
                "cognition_api_key": "sk-test",
                "profile_enabled": False,
            }
        )
        bridge = AstrBotSocialRuntimeBridge(
            context,
            settings,
            tmp_path,
            clock=lambda: 100,
            cognition_client_factory=lambda _: cognition,
        )
        await bridge.start()
        _put_profile_context(
            tmp_path / "groupmate-social-runtime-v2.db", settings.persona_id
        )
        await bridge.handle_event(_event("profile-ambient", "我觉得这个名字不错"))
        await bridge.manager.drain(now=102)
        facts = cognition.last_facts
        await bridge.close()
        return facts

    facts = asyncio.run(scenario())

    member = facts["member_context"]["members"][0]
    assert member["subject_id"] == "u1"
    assert "复读斥候" in member["aliases"]
    assert member["addressing_habits"] == ["会持续追问到问题真正落地"]


def test_existing_profile_context_reaches_reply_expression(tmp_path):
    async def scenario():
        context = _Context()
        settings = SocialRuntimeSettings.from_mapping(
            {
                "enabled_groups": ["885617919"],
                "runtime_mode": "SOCIAL_RUNTIME",
                "generation_provider": "provider:text",
                "profile_enabled": False,
            }
        )
        bridge = AstrBotSocialRuntimeBridge(
            context, settings, tmp_path, clock=lambda: 100
        )
        await bridge.start()
        _put_profile_context(
            tmp_path / "groupmate-social-runtime-v2.db", settings.persona_id
        )
        await bridge.handle_event(
            _event("profile-reply", "这个方案怎么看", mention_bot=True)
        )
        await bridge.close()
        return context.model_calls

    calls = asyncio.run(scenario())

    reply_call = next(
        call
        for call in calls
        if "根据已批准的社交动作生成回复" in call["system_prompt"]
    )
    assert "会持续追问到问题真正落地" in reply_call["system_prompt"]
