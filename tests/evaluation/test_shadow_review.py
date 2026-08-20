from __future__ import annotations

import asyncio
import json

import pytest

from eval.runner import EvaluationRunner
from eval.shadow import (
    FrozenHoldoutError,
    ShadowCalibrationRejected,
    ShadowCalibrationService,
    ShadowDecisionCapture,
    ShadowReleaseConfig,
    ShadowReviewRepository,
)
from groupmate.adapters.astrbot_bridge import AstrBotSocialRuntimeBridge
from groupmate.settings import SocialRuntimeSettings
from groupmate.social_runtime.control.commands import (
    ApproveCalibration,
    CommandContext,
    CommandService,
    RestoreConfig,
    ReviewShadowDecision,
)
from groupmate.social_runtime.control.config_versions import ConfigStatus
from groupmate.social_runtime.control.projections import ProjectionConsumer
from groupmate.social_runtime.control.queries import ProjectionQueries


SCENES = (
    "direct_interaction",
    "consecutive_messages",
    "parallel_topics",
    "public_help",
    "humor",
    "care",
    "shared_experience",
    "media_reaction",
    "task_progress",
    "boundary",
    "sleep_wake",
    "autonomous_initiation",
    "expired_opportunity",
    "task_topic_change",
    "ambiguous_target",
    "correct_silence",
)


def _capture(index: int, *, category: str = "direct_interaction", installed=True):
    action = index % 2 == 0
    return ShadowDecisionCapture.create(
        persona_id="aemeath",
        group_id="group-1",
        frame_id=f"frame:{index}",
        source_event_id=f"event:{index}",
        correlation_id=f"corr:{index}",
        occurred_at=1_000 + index,
        config_version=1,
        history=(
            {
                "occurred_at": 999 + index,
                "actor_ref": "member:alpha",
                "summary": "前一条安全摘要",
            },
        ),
        focus={
            "occurred_at": 1_000 + index,
            "actor_ref": "member:beta",
            "summary": "当前判断点",
        },
        attention={
            "trigger_kind": "FAST",
            "urgency": "high",
            "deadline": 1_000 + index,
        },
        target="member:beta" if action else None,
        candidate_response="候选回复" if action else None,
        candidate_actions=(
            {"kind": "HELP", "proposed_act": "answer_help_request"},
        ) if action else (),
        governor={
            "outcome": "ACT" if action else "SILENCE",
            "reason_codes": [
                "selected_by_social_utility" if action else "no_eligible_intention"
            ],
            "constraints": ["hard_gate_v1"],
        },
        expires_at=1_030 + index,
        prediction={
            "attention": True,
            "action": action,
            "target": "member:beta" if action else None,
            "intent": "answer_help_request" if action else None,
            "modalities": ["text"] if action else [],
            "text": "候选回复" if action else "",
        },
        suggested_categories=(category,),
        evaluation_lane="SOCIAL_CONVERSATION",
        ownership="GROUPMATE",
        installed=installed,
        runtime_mode="SHADOW",
    )


def _release(**overrides):
    values = {
        "false_positive_rate_cap": 0.05,
        "scene_minimums": {scene: 1 for scene in SCENES},
        "holdout_minimums": {
            "attention_precision": 0.9,
            "action_precision": 0.9,
            "target_precision": 0.9,
        },
        "attention_window_ms_bounds": (1_000, 30_000),
        "participation_weight_bounds": (0.0, 2.0),
    }
    values.update(overrides)
    return ShadowReleaseConfig(**values)


def _reviewed_repository(tmp_path, *, count=100):
    repository = ShadowReviewRepository(
        tmp_path / "groupmate-social-runtime-v2.db"
    )
    for index in range(count):
        category = SCENES[index % len(SCENES)]
        item = repository.record(_capture(index, category=category))
        repository.review(
            item.entity_ref,
            reviewer_id="admin:root",
            decision="reasonable",
            reviewed_at=2_000 + index,
        )
    return repository


def test_installed_astrbot_shadow_is_captured_projected_and_never_sent(tmp_path):
    async def scenario():
        path = tmp_path / "groupmate-social-runtime-v2.db"
        reviews = ShadowReviewRepository(path)
        bridge = AstrBotSocialRuntimeBridge(
            context=object(),
            settings=SocialRuntimeSettings.from_mapping(
                {
                    "runtime_mode": "SHADOW",
                    "enabled_groups": ["group-1"],
                }
            ),
            data_dir=tmp_path,
            shadow_reviews=reviews,
        )
        await bridge.start()
        await bridge.handle_event(
            {
                "message_id": "raw-platform-message-998877",
                "group_id": "group-1",
                "user_id": "99887766",
                "time": 1_700_000_000,
                "sender": {"nickname": "管理员不应看到原始 ID"},
                "message": [
                    {"type": "text", "data": {"text": "@你 看一下 https://secret.invalid/99887766"}},
                    {"type": "at", "data": {"qq": "323537051"}},
                ],
            }
        )
        ProjectionConsumer(path, "evaluation").consume(100)
        projection = ProjectionQueries(path).evaluation(
            persona_id="aemeath", group_id="group-1"
        )
        items = reviews.list_items(persona_id="aemeath", group_id="group-1")
        scene_version = (await bridge.manager.group_snapshot("group-1")).scene_version
        outbox_count = bridge.manager.event_store.outbox_count()
        calls = bridge.manager.execution_port.calls
        await bridge.close()
        return projection, items, scene_version, outbox_count, calls

    projection, items, scene_version, outbox_count, calls = asyncio.run(scenario())

    assert len(items) == 1
    assert items[0].source_kind == "installed_live_shadow"
    assert items[0].runtime_mode == "SHADOW"
    assert items[0].installed is True
    assert scene_version == 1
    assert outbox_count == 0
    assert calls == ()
    shadow = next(
        item for item in projection["items"]
        if item["kind"] == "evaluation.shadow_decision_captured"
    )
    summary = shadow["summary"]
    assert len(summary["focus"]) == 1
    for field in (
        "history",
        "focus",
        "attention",
        "target",
        "candidate_response",
        "candidate_actions",
        "governor",
        "suggested_categories",
        "expires_at",
    ):
        assert field in summary
    encoded = json.dumps(projection, ensure_ascii=False)
    assert "raw-platform-message-998877" not in encoded
    assert "99887766" not in encoded
    assert "https://secret.invalid" not in encoded
    assert "chain_of_thought" not in encoded.casefold()
    assert "prompt" not in encoded.casefold()


def test_review_primary_verdicts_require_human_labels_and_corrections(tmp_path):
    repository = ShadowReviewRepository(
        tmp_path / "groupmate-social-runtime-v2.db"
    )
    reasonable = repository.record(_capture(1))
    unreasonable = repository.record(_capture(2))
    insufficient = repository.record(_capture(3))

    accepted = repository.review(
        reasonable.entity_ref,
        reviewer_id="admin:root",
        decision="reasonable",
        reviewed_at=2_001,
    )
    corrected = repository.review(
        unreasonable.entity_ref,
        reviewer_id="admin:root",
        decision="unreasonable",
        categories=("ambiguous_target",),
        correction={
            "attention": True,
            "action": False,
            "target": None,
            "acceptable_intents": [],
            "unacceptable_intents": ["interrupt"],
            "modalities": [],
            "sensitivity": "group",
            "expires_after_ms": 0,
        },
        reviewed_at=2_002,
    )
    skipped = repository.review(
        insufficient.entity_ref,
        reviewer_id="admin:root",
        decision="insufficient",
        reviewed_at=2_003,
    )

    assert accepted.label["action"] is False
    assert corrected.label["action"] is False
    assert corrected.categories == ("ambiguous_target",)
    assert skipped.label is None
    with pytest.raises(ValueError, match="correction"):
        repository.review(
            repository.record(_capture(4)).entity_ref,
            reviewer_id="admin:root",
            decision="unreasonable",
            reviewed_at=2_004,
        )


def test_freeze_requires_100_real_human_reviews_coverage_and_temporal_holdout(tmp_path):
    repository = _reviewed_repository(tmp_path, count=99)
    bootstrap = repository.record(_capture(999, installed=False))
    repository.review(
        bootstrap.entity_ref,
        reviewer_id="admin:root",
        decision="reasonable",
        reviewed_at=9_999,
    )

    with pytest.raises(ValueError, match="100 real human-reviewed"):
        repository.freeze(
            persona_id="aemeath", group_id="group-1", release_config=_release()
        )

    final = repository.record(_capture(100, category=SCENES[100 % len(SCENES)]))
    repository.review(
        final.entity_ref,
        reviewer_id="admin:root",
        decision="reasonable",
        reviewed_at=10_000,
    )
    frozen = repository.freeze(
        persona_id="aemeath", group_id="group-1", release_config=_release()
    )

    assert len(frozen.records) == 100
    calibration_times = [
        item["decision_occurred_at"]
        for item in frozen.records
        if item["split"] == "calibration"
    ]
    holdout_times = [
        item["decision_occurred_at"]
        for item in frozen.records
        if item["split"] == "holdout"
    ]
    assert max(calibration_times) < min(holdout_times)
    assert all(item["labels_frozen"] is True for item in frozen.records)
    report = EvaluationRunner().run(frozen.records, _FixedRuntime(), "fixed")
    assert report.production_readiness_eligible is True
    holdout = next(item for item in repository.list_items(persona_id="aemeath", group_id="group-1") if item.split == "holdout")
    with pytest.raises(FrozenHoldoutError):
        repository.review(
            holdout.entity_ref,
            reviewer_id="admin:root",
            decision="reasonable",
            reviewed_at=20_000,
        )


class _FixedRuntime:
    def evaluate(self, scenario, worker_mode):
        prediction = dict(scenario["prediction"])
        result = {
            "prediction": prediction,
            "events": scenario["context"],
            "observations": (),
            "plans": (),
            "outbox": (),
            "projections": (),
            "latency_ms": 1,
            "cost": {"tokens": 1, "usd": 0.0},
            "candidate_owner": "GROUPMATE",
        }
        if worker_mode == "live":
            result["model"] = {
                "provider": "installed-provider",
                "model": "live-shadow",
                "config": {"temperature": 0},
                "input_tokens": 1,
                "output_tokens": 1,
                "latency_ms": 1,
            }
        return result


class _UnsafeRuntime(_FixedRuntime):
    def evaluate(self, scenario, worker_mode):
        result = super().evaluate(scenario, worker_mode)
        result["projections"] = ({"chain_of_thought": "hidden"},)
        return result


def test_calibration_runs_live_both_splits_rejects_safety_and_publishes_rollback_version(tmp_path):
    repository = _reviewed_repository(tmp_path)
    repository.freeze(
        persona_id="aemeath", group_id="group-1", release_config=_release()
    )
    service = ShadowCalibrationService(repository)
    proposed = {
        "attention_window_ms": 4_000,
        "reply_length_tendency": "short",
        "media_preference": "contextual",
        "participation_weights": {"direct": 1.1, "ambient": 0.8},
    }

    with pytest.raises(ValueError, match="calibratable"):
        service.run(
            persona_id="aemeath",
            group_id="group-1",
            proposed_config={**proposed, "capability_allowlist": ["forbidden"]},
            release_config=_release(),
            baseline_runtime=_FixedRuntime(),
            candidate_runtime=_FixedRuntime(),
        )
    rejected = service.run(
        persona_id="aemeath",
        group_id="group-1",
        proposed_config=proposed,
        release_config=_release(),
        baseline_runtime=_FixedRuntime(),
        candidate_runtime=_UnsafeRuntime(),
    )
    assert rejected.status == "REJECTED"
    assert "safety_regression" in rejected.reason_codes

    pending = service.run(
        persona_id="aemeath",
        group_id="group-1",
        proposed_config=proposed,
        release_config=_release(),
        baseline_runtime=_FixedRuntime(),
        candidate_runtime=_FixedRuntime(),
    )
    assert pending.status == "PENDING_APPROVAL"
    assert set(pending.comparison) == {"calibration", "holdout"}
    assert all(
        side["worker_mode"] == "live"
        for split in pending.comparison.values()
        for side in split.values()
    )
    ProjectionConsumer(repository.path, "governance").consume(1_000)
    governance = ProjectionQueries(repository.path).governance(
        persona_id="aemeath", group_id="group-1"
    )
    projected = next(
        item for item in governance["items"]
        if item["entity_ref"] == pending.entity_ref
    )
    assert projected["kind"] == "calibration.shadow_candidate_evaluated"
    assert projected["summary"]["status"] == "PENDING_APPROVAL"
    assert set(projected["summary"]["comparison"]) == {
        "calibration", "holdout"
    }
    assert "candidate_digest" not in json.dumps(projected, ensure_ascii=False)

    commands = CommandService(
        repository.path,
        persona_id="aemeath",
        group_ids=("group-1",),
        admin_ids=("admin:root",),
        shadow_repository=repository,
    )
    context = CommandContext(
        admin_id="admin:root",
        persona_id="aemeath",
        group_id="group-1",
        expected_version=0,
        reason="reviewed live calibration and holdout",
        confirmed=True,
    )
    with pytest.raises(ShadowCalibrationRejected):
        commands.execute(ApproveCalibration(rejected.entity_ref), context)
    approved = commands.execute(ApproveCalibration(pending.entity_ref), context)
    assert approved.data["status"] == "APPROVED"
    assert approved.data["config_version"] == 1
    published = commands.config_repository.load("shadow-calibration:group-1", 1)
    assert published.status is ConfigStatus.PUBLISHED
    assert set(published.config) == set(proposed)

    rollback = commands.execute(
        RestoreConfig("shadow-calibration:group-1", 1),
        CommandContext(
            admin_id="admin:root",
            persona_id="aemeath",
            group_id="group-1",
            expected_version=1,
            reason="rollback drill",
            confirmed=True,
        ),
    )
    assert rollback.data["status"] == "PUBLISHED"
    assert rollback.data["version"] == 2


def test_shadow_review_command_updates_only_scoped_pending_item(tmp_path):
    repository = ShadowReviewRepository(
        tmp_path / "groupmate-social-runtime-v2.db"
    )
    item = repository.record(_capture(1))
    service = CommandService(
        repository.path,
        persona_id="aemeath",
        group_ids=("group-1",),
        admin_ids=("admin:root",),
        shadow_repository=repository,
    )
    result = service.execute(
        ReviewShadowDecision(item.entity_ref, "reasonable"),
        CommandContext(
            admin_id="admin:root",
            persona_id="aemeath",
            group_id="group-1",
            expected_version=0,
            reason="decision is socially reasonable",
            confirmed=True,
        ),
    )

    assert result.event.event_type == "control.shadow_decision_reviewed"
    assert result.data["decision"] == "reasonable"
    assert repository.load(item.entity_ref).reviewer_id == "admin:root"
