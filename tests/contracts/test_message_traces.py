import json
from dataclasses import replace
from types import SimpleNamespace

from groupmate.social_runtime.attention import AttentionFrame
from groupmate.social_runtime.cognition.contracts import CognitiveObservation
from groupmate.social_runtime.contracts import SocialEventEnvelope
from groupmate.social_runtime.control.message_traces import MessageTraceRepository
from groupmate.social_runtime.governor import GovernorResult
from groupmate.social_runtime.knowledge.contracts import TopicUnderstandingFrame
from groupmate.social_runtime.society.relationship_events import (
    RelationshipEventDecision,
    RelationshipEventProposal,
)


def _platform_event(
    message_id: str,
    *,
    card: str = "",
    nickname: str = "小夏",
    owner: str = "UNKNOWN",
) -> SocialEventEnvelope:
    display_name = card or nickname
    return SocialEventEnvelope.create(
        event_id=f"qq:{message_id}",
        event_type="platform.message",
        occurred_at=10,
        received_at=10,
        persona_id="groupmate:default",
        group_id="g-1",
        actor_id="42",
        source_message_id=message_id,
        correlation_id=f"qq:{message_id}",
        causation_id=None,
        payload={
            "text": "今晚一起打游戏吗？",
            "sender": {"id": "42", "name": display_name},
            "interaction_owner": owner,
            "external_trigger_kind": "command" if owner == "EXTERNAL_PLUGIN" else None,
            "media": [],
        },
    )


def _evaluation(event: SocialEventEnvelope, outcome: str):
    frame = AttentionFrame(
        frame_id="frame-1",
        group_id="g-1",
        scene_version=1,
        trigger_kind="AMBIENT",
        focus_topic_ids=(),
        focus_event_ids=(event.event_id,),
        candidate_audiences=("42",),
        urgency="normal",
        deadline=20,
        requested_workers=(),
        persona_state_version=1,
        config_version=1,
    )
    result = GovernorResult(
        outcome=outcome,
        selected_intention_ids=("intent-1",) if outcome == "ACT" else (),
        rejected=(),
        reason_codes=("no_eligible_intention",) if outcome == "SILENCE" else (),
        reconsider_at=30 if outcome == "DEFER" else None,
        constraints=(),
    )
    return SimpleNamespace(
        source_event=event,
        runtime_mode=SimpleNamespace(value="SHADOW"),
        frame=frame,
        governor_result=result,
        accepted=True,
        status="evaluated",
        candidate_response="我会先看报错第一行。" if outcome == "ACT" else None,
        reply_diagnostic=None,
        participation_lane="DIRECT_FAST",
        participation_diagnostics=("deterministic_direct_fast",),
        candidates=(SimpleNamespace(intention_id="intent-1"),),
        cognition_diagnostics=(
            SimpleNamespace(
                worker="direct_interaction",
                status="SUCCEEDED",
                latency_ms=120,
                diagnostic_code=None,
                queue_wait_ms=4,
                provider_latency_ms=100,
                input_bytes=2048,
                timeout_ms=8000,
                backend="direct_deepseek",
                model="deepseek-v4-flash",
            ),
        ),
    )


def test_trace_updates_one_message_instead_of_appending_projection_rows(tmp_path):
    repo = MessageTraceRepository(tmp_path / "runtime.db")
    event = _platform_event("m-1", card="夏夏", nickname="小夏")

    repo.record_received(event, runtime_mode="SHADOW", now=10)
    repo.mark_entered(event.event_id, now=11)
    repo.record_evaluation(_evaluation(event, outcome="SILENCE"), now=12)

    view = repo.query(persona_id="groupmate:default", group_id="g-1")
    assert len(view["items"]) == 1
    summary = view["items"][0]["summary"]
    assert set(summary) == {
        "actor",
        "message",
        "route",
        "understanding",
        "judgement",
        "decision",
        "delivery",
        "timing",
        "stages",
    }
    assert summary["actor"]["display_name"] == "夏夏"
    assert summary["route"]["owner"] == "GROUPMATE"
    assert summary["decision"]["outcome"] == "SILENCE"
    assert summary["delivery"]["status"] == "SILENT"
    assert "42" not in str(view["items"][0])

    detail = repo.detail(
        persona_id="groupmate:default",
        group_id="g-1",
        trace_ref=view["items"][0]["entity_ref"],
    )
    assert [stage["kind"] for stage in detail["summary"]["stages"]] == [
        "RECEIVED",
        "ROUTED",
        "ATTENDED",
        "UNDERSTOOD",
        "DECIDED",
    ]


def test_trace_exposes_only_compact_relationship_result(tmp_path):
    repo = MessageTraceRepository(tmp_path / "runtime.db")
    event = _platform_event("relationship-trace")
    proposal = RelationshipEventProposal(
        event_id="relationship:r1",
        persona_id="groupmate:default",
        group_id="g-1",
        subject_id="42",
        kind="warm_exchange",
        confidence=0.91,
        severity="ordinary",
        summary="这段模型摘要不应出现在看板",
        source_event_ids=(event.event_id,),
        occurred_at=10,
    )
    decision = RelationshipEventDecision(
        "SUGGEST", ("shadow_observation_only",), proposal, None, 0.0
    )
    evaluation = SimpleNamespace(
        **vars(_evaluation(event, outcome="SILENCE")),
        relationship_decisions=(decision,),
        relationship_stage="陌生",
    )

    repo.record_received(event, runtime_mode="SHADOW", now=10)
    repo.mark_entered(event.event_id, now=11)
    repo.record_evaluation(evaluation, now=12)

    summary = repo.query(
        persona_id="groupmate:default", group_id="g-1"
    )["items"][0]["summary"]
    assert summary["relationship"] == {
        "outcome": "SUGGEST",
        "kind": "友好交流",
        "reason": "SHADOW 仅记录，未更新好感度",
        "stage": "陌生",
    }
    assert proposal.summary not in str(summary)


def test_external_trigger_is_visible_without_claiming_plugin_success(tmp_path):
    repo = MessageTraceRepository(tmp_path / "runtime.db")
    event = _platform_event("m-2", owner="EXTERNAL_PLUGIN")

    repo.record_received(event, runtime_mode="SOCIAL_RUNTIME", now=20)

    item = repo.query(
        persona_id="groupmate:default", group_id="g-1"
    )["items"][0]
    assert item["summary"]["route"] == {
        "owner": "EXTERNAL_PLUGIN",
        "label": "交给外部能力",
        "reason": "匹配已配置的外部触发规则",
    }
    assert item["summary"]["delivery"]["status"] == "HANDED_OFF"
    assert item["summary"]["delivery"]["label"] == "结果由外部插件负责"


def test_query_orders_newest_message_first(tmp_path):
    repo = MessageTraceRepository(tmp_path / "runtime.db")
    repo.record_received(_platform_event("old"), runtime_mode="SHADOW", now=10)
    repo.record_received(_platform_event("new"), runtime_mode="SHADOW", now=20)

    items = repo.query(persona_id="groupmate:default", group_id="g-1")["items"]
    assert [item["summary"]["timing"]["received_at"] for item in items] == [20, 10]


def test_trace_query_reports_total_and_paginates_without_duplicates(tmp_path):
    repo = MessageTraceRepository(tmp_path / "runtime.db")
    for index in range(7):
        repo.record_received(
            _platform_event(f"page-{index}"),
            runtime_mode="SHADOW",
            now=10 + index,
        )

    first = repo.query(
        persona_id="groupmate:default",
        group_id="g-1",
        limit=3,
    )
    second = repo.query(
        persona_id="groupmate:default",
        group_id="g-1",
        limit=3,
        before=first["next_cursor"],
    )

    assert first["total_count"] == 7
    assert len(first["items"]) == 3
    assert first["has_more"] is True
    assert first["next_cursor"]
    assert second["total_count"] == 7
    assert len(second["items"]) == 3
    assert {item["entity_ref"] for item in first["items"]}.isdisjoint(
        item["entity_ref"] for item in second["items"]
    )


def test_ambient_evaluation_closes_all_pending_focus_messages(tmp_path):
    repo = MessageTraceRepository(tmp_path / "runtime.db")
    context = _platform_event("ambient-context")
    source = _platform_event("ambient-source")
    repo.record_received(context, runtime_mode="SHADOW", now=10)
    repo.record_received(source, runtime_mode="SHADOW", now=11)
    evaluation = _evaluation(source, outcome="SILENCE")
    evaluation.frame = replace(
        evaluation.frame,
        focus_event_ids=(context.event_id, source.event_id),
    )

    repo.record_evaluation(evaluation, now=12)

    by_ref = {
        item["entity_ref"]: item["summary"]
        for item in repo.query(
            persona_id="groupmate:default", group_id="g-1"
        )["items"]
    }
    context_summary = by_ref[
        repo._opaque_ref("traces", "groupmate:default", "g-1", context.event_id)
    ]
    assert context_summary["understanding"] == {
        "status": "READY",
        "summary": "已纳入同一轮群聊理解",
        "diagnostics": [],
    }
    assert context_summary["decision"] == {
        "outcome": "OBSERVE",
        "would_reply": False,
        "label": "作为上下文参与判断",
        "reasons": [],
    }
    assert context_summary["delivery"] == {
        "mode": "SHADOW",
        "status": "OBSERVED",
        "label": "已纳入群聊上下文",
    }
    assert [stage["kind"] for stage in context_summary["stages"]] == [
        "RECEIVED",
        "ATTENDED",
        "UNDERSTOOD",
        "DECIDED",
    ]


def test_ambient_evaluation_does_not_overwrite_terminal_context_trace(tmp_path):
    repo = MessageTraceRepository(tmp_path / "runtime.db")
    context = _platform_event("external-context", owner="EXTERNAL_PLUGIN")
    source = _platform_event("ambient-source-after-external")
    repo.record_received(context, runtime_mode="SHADOW", now=10)
    repo.record_received(source, runtime_mode="SHADOW", now=11)
    evaluation = _evaluation(source, outcome="SILENCE")
    evaluation.frame = replace(
        evaluation.frame,
        focus_event_ids=(context.event_id, source.event_id),
    )

    repo.record_evaluation(evaluation, now=12)

    items = repo.query(
        persona_id="groupmate:default", group_id="g-1"
    )["items"]
    context_ref = repo._opaque_ref(
        "traces", "groupmate:default", "g-1", context.event_id
    )
    context_summary = next(
        item["summary"] for item in items if item["entity_ref"] == context_ref
    )
    assert context_summary["route"]["owner"] == "EXTERNAL_PLUGIN"
    assert context_summary["delivery"]["status"] == "HANDED_OFF"


def test_shadow_act_keeps_pre_gate_decision_separate_from_delivery(tmp_path):
    repo = MessageTraceRepository(tmp_path / "runtime.db")
    event = _platform_event("shadow-act")

    repo.record_received(event, runtime_mode="SHADOW", now=10)
    repo.mark_entered(event.event_id, now=11)
    repo.record_evaluation(_evaluation(event, outcome="ACT"), now=12)

    summary = repo.query(
        persona_id="groupmate:default", group_id="g-1"
    )["items"][0]["summary"]
    assert summary["decision"]["pre_gate_outcome"] == "ACT"
    assert summary["decision"]["candidate_response"] == "我会先看报错第一行。"
    assert summary["decision"]["participation_lane"] == "DIRECT_FAST"
    assert summary["decision"]["would_reply"] is True
    assert summary["delivery"]["status"] == "BLOCKED_BY_SHADOW"
    assert summary["delivery"]["label"] == "SHADOW：已完成判断，未发送"
    assert summary["understanding"]["diagnostics"][0]["status"] == "SUCCEEDED"
    assert summary["understanding"]["diagnostics"][0] == {
        "worker": "direct_interaction",
        "status": "SUCCEEDED",
        "latency_ms": 120,
        "diagnostic_code": None,
        "queue_wait_ms": 4,
        "provider_latency_ms": 100,
        "input_bytes": 2048,
        "timeout_ms": 8000,
        "backend": "direct_deepseek",
        "model": "deepseek-v4-flash",
    }
    assert summary["understanding"]["candidate_count"] == 1
    assert summary["understanding"]["candidate_source"] == "deterministic"
    assert "chain_of_thought" not in str(summary)
    assert "prompt" not in str(summary)


def test_trace_projects_only_safe_game_understanding_summary(tmp_path):
    repo = MessageTraceRepository(tmp_path / "runtime.db")
    event = _platform_event("knowledge-summary")
    evaluation = _evaluation(event, outcome="SILENCE")
    evaluation.topic_understanding = TopicUnderstandingFrame.create(
        frame_id="knowledge-frame:private",
        game_ids=("game:genshin-impact",),
        resolved_entities=(
            {
                "entity_id": "entity:genshin:traveler",
                "entity_type": "character",
                "canonical_name": "旅行者",
                "canonical_game_id": "game:genshin-impact",
                "matched_alias": "内部命中别名",
                "confidence": 0.991,
                "supporting_knowledge_ids": ("private:evidence:author-42",),
            },
        ),
        resolved_terms=(
            {
                "term_id": "term:genshin:primogem",
                "canonical_text": "原石",
                "meaning_summary": "这段内部证据摘要不能进入 trace",
                "game_id": "game:genshin-impact",
                "term_kind": "official",
                "confidence": 0.987,
                "supporting_knowledge_ids": ("private:claim:raw",),
            },
        ),
        discourse_referents=(),
        version_reference={
            "game_id": "game:genshin-impact",
            "relative_kind": "new",
            "disclosure_kind": "none",
            "region": None,
            "platform": None,
            "confidence": 0.9,
        },
        conversation_intent_hint="version_question",
        ambiguity_codes=("risk:version_state", "untrusted:raw-diagnostic"),
        confidence=0.99,
        supporting_knowledge_ids=("private:frame:evidence",),
    )
    evaluation.knowledge_diagnostics = (
        "knowledge_local_resolution_failed",
        "untrusted:exception-message",
    )

    repo.record_received(event, runtime_mode="SHADOW", now=10)
    repo.record_evaluation(evaluation, now=12)

    understanding = repo.query(
        persona_id="groupmate:default", group_id="g-1"
    )["items"][0]["summary"]["understanding"]
    assert understanding["games"] == ["原神"]
    assert understanding["entities"] == [
        {"name": "旅行者", "type": "character", "game": "原神"}
    ]
    assert understanding["terms"] == [
        {"name": "原石", "kind": "official", "game": "原神"}
    ]
    assert understanding["version_reference"] == {
        "game": "原神",
        "relative_kind": "new",
        "disclosure_kind": "none",
        "region": None,
        "platform": None,
    }
    assert understanding["need"] == "fresh_evidence_required"
    assert understanding["diagnostic_codes"] == [
        "risk:version_state",
        "knowledge_local_resolution_failed",
    ]
    serialized = json.dumps(understanding, ensure_ascii=False)
    for private_value in (
        "0.991",
        "0.987",
        "内部命中别名",
        "这段内部证据摘要不能进入 trace",
        "private:evidence:author-42",
        "private:claim:raw",
        "private:frame:evidence",
        "untrusted:raw-diagnostic",
        "untrusted:exception-message",
    ):
        assert private_value not in serialized


def test_trace_projects_bounded_game_release_diagnostics(tmp_path):
    cases = (
        ("official_complete", "complete", "official_evidence_fresh"),
        ("official_partial", "partial", "official_probe_partial"),
        (
            "negative_snapshot_valid",
            "complete",
            "official_no_matching_update",
        ),
        ("evidence_disputed", "complete", "evidence_disputed"),
        ("knowledge_stale", "complete", "knowledge_stale"),
        ("official_failed", "failed", "knowledge_job_recovered"),
    )

    for index, (status, probe_status, probe_reason) in enumerate(cases):
        repo = MessageTraceRepository(tmp_path / f"release-{index}.db")
        event = _platform_event(f"release-diagnostic-{index}")
        evaluation = _evaluation(event, outcome="SILENCE")
        evaluation.knowledge_diagnostic = {
            "status": status,
            "probe_status": probe_status,
            "probe_reason": probe_reason,
            "source_domains": (
                "news.mihoyo.com",
                "news.mihoyo.com",
            ),
            "evidence_level": "official",
            "checked_at": 100,
            "fresh_until": 200,
            "release_revision": 3,
            "tracks": {
                "release": "future",
                "official": "preview",
                "rumor": "none_observed",
            },
            "url": "https://news.mihoyo.com/?token=secret",
            "query": "完整搜索查询不应暴露",
            "evidence_excerpt": "网页短证据不应暴露",
            "exception": "provider secret exception",
            "score": 0.991,
        }

        repo.record_received(event, runtime_mode="SHADOW", now=10)
        repo.record_evaluation(evaluation, now=12)

        understanding = repo.query(
            persona_id="groupmate:default", group_id="g-1"
        )["items"][0]["summary"]["understanding"]
        assert understanding["knowledge_diagnostic"] == {
            "status": status,
            "probe_status": probe_status,
            "probe_reason": probe_reason,
            "source_domains": ["news.mihoyo.com"],
            "evidence_level": "official",
            "checked_at": 100,
            "fresh_until": 200,
            "release_revision": 3,
            "tracks": {
                "release": "future",
                "official": "preview",
                "rumor": "none_observed",
            },
        }
        serialized = json.dumps(understanding, ensure_ascii=False)
        for private_value in (
            "token=secret",
            "完整搜索查询不应暴露",
            "网页短证据不应暴露",
            "provider secret exception",
            "0.991",
        ):
            assert private_value not in serialized


def test_reply_plan_projects_only_safe_expression_summary(tmp_path):
    repo = MessageTraceRepository(tmp_path / "runtime.db")
    event = _platform_event("expression")
    repo.record_received(event, runtime_mode="SHADOW", now=10)
    repo.mark_entered(event.event_id, now=11)
    plan = SimpleNamespace(
        expression=SimpleNamespace(
            reaction_stance="acknowledge_relationship",
            core_response_goal="respond_to_direct_interaction",
            followup_hook="optional_if_natural",
            message_count=2,
            capability_request=None,
            persona_cues=("不应展示的人格背景",),
        )
    )

    repo.record_plan(event.event_id, plan, now=12)

    summary = repo.query(
        persona_id="groupmate:default", group_id="g-1"
    )["items"][0]["summary"]
    assert summary["expression"] == {
        "reaction_stance": "acknowledge_relationship",
        "core_response_goal": "respond_to_direct_interaction",
        "followup_hook": "optional_if_natural",
        "message_count": 2,
        "capability_request": None,
        "relationship_stage": "陌生",
        "explicit_material_selected": False,
        "material_reason": "no_relevant_material",
    }
    assert "persona_cues" not in str(summary)
    assert "不应展示的人格背景" not in str(summary)


def test_social_plan_trace_projects_decisions_without_private_inputs(tmp_path):
    repo = MessageTraceRepository(tmp_path / "runtime.db")
    event = _platform_event("social-plan")
    repo.record_received(event, runtime_mode="SHADOW", now=10)
    plan = SimpleNamespace(
        expression=None,
        scene=SimpleNamespace(
            scene_kind="intimacy_request",
            target_scope=SimpleNamespace(value="INDIVIDUAL"),
            chorus_target=SimpleNamespace(value="NONE"),
            chorus_chain_id=None,
            chorus_participant_ids=(),
            profile_fact_ids=("profile:private",),
        ),
        stance=SimpleNamespace(
            attitude=SimpleNamespace(value="GUARDED"),
            willingness=SimpleNamespace(value="LIMITED"),
            boundary=SimpleNamespace(value="SOFT"),
            effort=SimpleNamespace(value="MINIMAL"),
            boundary_pressure=88,
        ),
        move=SimpleNamespace(
            primary_move=SimpleNamespace(value="LIMITED_ACCEPT"),
            ending=SimpleNamespace(value="STOP"),
            realization_mode=SimpleNamespace(value="GENERATED"),
            must_say=(),
            may_say=(SimpleNamespace(category="profile_fact", text="数据库管理员"),),
        ),
        style=SimpleNamespace(
            posture="guarded",
            max_chars=80,
            max_sentences=2,
            max_segments=1,
        ),
        member_context="数据库管理员，最近在维护私有项目",
    )

    repo.record_plan(event.event_id, plan, now=12)

    summary = repo.query(
        persona_id="groupmate:default", group_id="g-1"
    )["items"][0]["summary"]
    serialized = json.dumps(summary, ensure_ascii=False)
    assert summary["social_scene"] == {
        "scene_kind": "intimacy_request",
        "target_scope": "INDIVIDUAL",
        "chorus_target": "NONE",
        "chorus_chain_id": None,
        "chorus_participant_count": 0,
    }
    assert summary["stance"] == {
        "attitude": "GUARDED",
        "willingness": "LIMITED",
        "boundary": "SOFT",
        "effort": "MINIMAL",
    }
    assert summary["social_move"]["primary_move"] == "LIMITED_ACCEPT"
    assert summary["social_move"]["fact_categories"] == ["profile_fact"]
    assert "profile_fact_ids" not in serialized
    assert "boundary_pressure" not in serialized
    assert "数据库管理员" not in serialized


def test_silent_social_move_overrides_pre_gate_act_without_creating_reply_plan(tmp_path):
    repo = MessageTraceRepository(tmp_path / "runtime.db")
    event = _platform_event("social-silence")
    repo.record_received(event, runtime_mode="SHADOW", now=10)
    repo.record_evaluation(_evaluation(event, outcome="ACT"), now=11)

    repo.record_social_decision(
        event.event_id,
        scene=SimpleNamespace(
            scene_kind="group_chorus",
            target_scope=SimpleNamespace(value="GROUP"),
            chorus_target=SimpleNamespace(value="UNKNOWN"),
            chorus_chain_id="chorus:safe-id",
            chorus_participant_ids=("u1", "u2"),
        ),
        stance=SimpleNamespace(
            attitude=SimpleNamespace(value="GUARDED"),
            willingness=SimpleNamespace(value="UNWILLING"),
            boundary=SimpleNamespace(value="SOFT"),
            effort=SimpleNamespace(value="MINIMAL"),
        ),
        move=SimpleNamespace(
            primary_move=SimpleNamespace(value="SILENCE"),
            ending=SimpleNamespace(value="STOP"),
            realization_mode=SimpleNamespace(value="GENERATED"),
            must_say=(),
            may_say=(),
        ),
        diagnostic_code="chorus_member_invalid",
        now=12,
    )

    summary = repo.query(
        persona_id="groupmate:default", group_id="g-1"
    )["items"][0]["summary"]
    assert summary["decision"]["pre_gate_outcome"] == "ACT"
    assert summary["decision"]["would_reply"] is False
    assert summary["decision"]["reply_diagnostic"] == "chorus_member_invalid"
    assert summary["social_move"]["primary_move"] == "SILENCE"
    assert summary["delivery"]["status"] == "SILENT"


def test_ambient_model_judgement_projects_reason_and_public_evidence(tmp_path):
    repo = MessageTraceRepository(tmp_path / "runtime.db")
    evidence_event = _platform_event("evidence", card="夏夏")
    source_event = _platform_event("ambient-result", card="小林")
    repo.record_received(evidence_event, runtime_mode="SHADOW", now=10)
    repo.record_received(source_event, runtime_mode="SHADOW", now=11)
    repo.mark_entered(source_event.event_id, now=12)
    evaluation = _evaluation(source_event, outcome="OBSERVE")
    evaluation.participation_lane = "AMBIENT"
    evaluation.context_events = (evidence_event, source_event)
    evaluation.cognitive_observations = (
        CognitiveObservation.create(
            worker="ambient_social_assessor",
            kind="participation_assessment",
            proposition={
                "should_participate": False,
                "decision": "silence",
                "disruption_cost": 0.62,
                "novelty": 0.18,
                "reason": "成员正在自然交流，现在插话会打断对话。",
            },
            confidence=0.74,
            evidence_event_ids=(evidence_event.event_id,),
            scene_version=1,
            expires_at=30,
            uncertainty=(),
        ),
    )
    evaluation.cognition_diagnostics = (
        SimpleNamespace(
            worker="ambient_social_assessor",
            status="SUCCEEDED",
            latency_ms=900,
            diagnostic_code=None,
        ),
    )

    repo.record_evaluation(evaluation, now=13)

    summary = repo.query(
        persona_id="groupmate:default", group_id="g-1"
    )["items"][0]["summary"]
    assert summary["judgement"] == {
        "source": "model",
        "status": "accepted",
        "decision": "silence",
        "would_reply": False,
        "label": "继续观察",
        "reason": "成员正在自然交流，现在插话会打断对话。",
        "evidence": {
            "actor": summary["judgement"]["evidence"]["actor"],
            "message": summary["judgement"]["evidence"]["message"],
        },
    }
    assert summary["judgement"]["evidence"]["actor"]["display_name"] == "夏夏"
    assert (
        summary["judgement"]["evidence"]["message"]["summary"]
        == "今晚一起打游戏吗？"
    )
    assert "qq:evidence" not in str(summary["judgement"])


def test_unavailable_model_judgement_does_not_invent_reason_or_evidence(tmp_path):
    repo = MessageTraceRepository(tmp_path / "runtime.db")
    event = _platform_event("ambient-timeout")
    repo.record_received(event, runtime_mode="SHADOW", now=10)
    evaluation = _evaluation(event, outcome="OBSERVE")
    evaluation.participation_lane = "AMBIENT"
    evaluation.context_events = (event,)
    evaluation.cognitive_observations = ()
    evaluation.cognition_diagnostics = (
        SimpleNamespace(
            worker="ambient_social_assessor",
            status="TIMED_OUT",
            latency_ms=6000,
            diagnostic_code="direct_timeout",
        ),
    )

    repo.record_evaluation(evaluation, now=12)

    judgement = repo.query(
        persona_id="groupmate:default", group_id="g-1"
    )["items"][0]["summary"]["judgement"]
    assert judgement == {
        "source": "model",
        "status": "unavailable",
        "decision": None,
        "would_reply": False,
        "label": "模型判断未采用",
    }


def test_policy_judgement_is_marked_as_not_requiring_model(tmp_path):
    repo = MessageTraceRepository(tmp_path / "runtime.db")
    event = _platform_event("direct-policy")
    repo.record_received(event, runtime_mode="SHADOW", now=10)
    repo.record_evaluation(_evaluation(event, outcome="ACT"), now=12)

    judgement = repo.query(
        persona_id="groupmate:default", group_id="g-1"
    )["items"][0]["summary"]["judgement"]
    assert judgement == {
        "source": "policy",
        "status": "not_required",
        "decision": "speak",
        "would_reply": True,
        "label": "准备回复",
    }


def test_non_text_segments_keep_order_without_exposing_platform_sources(tmp_path):
    repo = MessageTraceRepository(tmp_path / "runtime.db")
    event = SocialEventEnvelope.create(
        event_id="qq:media-1",
        event_type="platform.message",
        occurred_at=10,
        received_at=10,
        persona_id="groupmate:default",
        group_id="g-1",
        actor_id="42",
        source_message_id="media-1",
        correlation_id="qq:media-1",
        causation_id=None,
        payload={
            "text": "看看",
            "sender": {"id": "42", "name": "夏夏"},
            "interaction_owner": "UNKNOWN",
            "segments": [
                {"type": "text", "data": {"text": "看看"}},
                {
                    "type": "image",
                    "data": {
                        "url": "https://multimedia.nt.qq.com.cn/demo.jpg",
                        "summary": "猫猫照片",
                        "file_size": 2048,
                    },
                },
                {"type": "record", "data": {"file": "voice.amr", "file_size": 4096}},
                {"type": "video", "data": {"url": "https://example.com/demo.mp4"}},
                {"type": "file", "data": {"file": "发布清单.pdf", "file_size": 8192}},
                {"type": "face", "data": {"id": "14"}},
            ],
            "media": [
                {"type": "image", "url": "https://multimedia.nt.qq.com.cn/demo.jpg"},
                {"type": "record", "file": "voice.amr"},
                {"type": "video", "url": "https://example.com/demo.mp4"},
                {"type": "file", "file": "发布清单.pdf"},
            ],
        },
    )

    repo.record_received(event, runtime_mode="SHADOW", now=10)

    message = repo.query(
        persona_id="groupmate:default", group_id="g-1"
    )["items"][0]["summary"]["message"]
    assert message["summary"] == "看看 · 图片 · 语音 · 视频 · 文件 · QQ 表情"
    assert [part["kind"] for part in message["parts"]] == [
        "text",
        "image",
        "record",
        "video",
        "file",
        "face",
    ]
    assert message["parts"][1] == {
        "kind": "image",
        "label": "图片",
        "media_ref": message["parts"][1]["media_ref"],
        "name": "猫猫照片",
        "size": 2048,
        "preview": "image",
    }
    assert message["parts"][4]["name"] == "发布清单.pdf"
    public_text = str(message)
    assert "multimedia.nt.qq.com.cn" not in public_text
    assert "example.com" not in public_text
    assert "voice.amr" not in public_text
    assert '"14"' not in public_text


def test_at_segment_uses_known_member_name_without_exposing_qq_id(tmp_path):
    repo = MessageTraceRepository(tmp_path / "runtime.db")
    mentioned = _platform_event("known-member", card="夏夏")
    repo.record_received(mentioned, runtime_mode="SHADOW", now=10)
    event = SocialEventEnvelope.create(
        event_id="qq:mention-1",
        event_type="platform.message",
        occurred_at=11,
        received_at=11,
        persona_id="groupmate:default",
        group_id="g-1",
        actor_id="84",
        source_message_id="mention-1",
        correlation_id="qq:mention-1",
        causation_id=None,
        payload={
            "text": "@夏夏 看这里",
            "sender": {"id": "84", "name": "小林"},
            "interaction_owner": "UNKNOWN",
            "segments": [
                {"type": "at", "data": {"qq": "42"}},
                {"type": "text", "data": {"text": " 看这里"}},
            ],
        },
    )

    repo.record_received(event, runtime_mode="SHADOW", now=11)

    message = repo.query(
        persona_id="groupmate:default", group_id="g-1"
    )["items"][0]["summary"]["message"]
    mention = message["parts"][0]
    assert mention["kind"] == "at"
    assert mention["label"] == "@夏夏"
    assert mention["display_name"] == "夏夏"
    assert mention["member_ref"].startswith("member:")
    assert mention["avatar_ref"].startswith("participant:")
    assert "42" not in str(message)


def test_at_all_and_unknown_member_have_safe_labels(tmp_path):
    repo = MessageTraceRepository(tmp_path / "runtime.db")
    event = SocialEventEnvelope.create(
        event_id="qq:mention-2",
        event_type="platform.message",
        occurred_at=10,
        received_at=10,
        persona_id="groupmate:default",
        group_id="g-1",
        actor_id="84",
        source_message_id="mention-2",
        correlation_id="qq:mention-2",
        causation_id=None,
        payload={
            "text": "",
            "sender": {"id": "84", "name": "小林"},
            "interaction_owner": "UNKNOWN",
            "segments": [
                {"type": "at", "data": {"qq": "all"}},
                {"type": "at", "data": {"qq": "99887766"}},
            ],
        },
    )

    repo.record_received(event, runtime_mode="SHADOW", now=10)

    parts = repo.query(
        persona_id="groupmate:default", group_id="g-1"
    )["items"][0]["summary"]["message"]["parts"]
    assert parts == [
        {"kind": "at", "label": "@全体成员", "display_name": "全体成员"},
        {"kind": "at", "label": "@未知成员", "display_name": "未知成员"},
    ]
    assert "99887766" not in str(parts)
