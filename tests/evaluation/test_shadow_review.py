from __future__ import annotations

import asyncio
import hashlib
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
from groupmate.social_runtime.control.config_versions import (
    ConfigStatus,
    ConfigVersionRepository,
)
from groupmate.social_runtime.control.projections import ProjectionConsumer
from groupmate.social_runtime.control.queries import ProjectionQueries
from groupmate.social_runtime.persistence.schema import connect_database


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


def _capture(
    index: int,
    *,
    category: str = "direct_interaction",
    installed=True,
    lane: str = "SOCIAL_CONVERSATION",
    ownership: str = "GROUPMATE",
):
    action = index % 2 == 0 and lane != "EXTERNAL_PLUGIN_COMPATIBILITY"
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
        evaluation_lane=lane,
        ownership=ownership,
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
        "lane_minimums": {
            "SOCIAL_CONVERSATION": 1,
            "GROUPMATE_CAPABILITY": 0,
            "EXTERNAL_PLUGIN_COMPATIBILITY": 0,
        },
        "capability_minimums": {
            "task": 0.0,
            "delivery": 0.0,
            "recovery": 0.0,
        },
        "compatibility_minimums": {
            "no_steal": 0.0,
            "no_duplicate": 0.0,
            "no_self_attribution": 0.0,
        },
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


def test_social_runtime_group_mode_cannot_be_recorded_as_installed_live_shadow(tmp_path):
    async def scenario():
        path = tmp_path / "groupmate-social-runtime-v2.db"
        reviews = ShadowReviewRepository(path)
        bridge = AstrBotSocialRuntimeBridge(
            context=object(),
            settings=SocialRuntimeSettings.from_mapping(
                {
                    "runtime_mode": "SOCIAL_RUNTIME",
                    "enabled_groups": ["group-1"],
                    "social_runtime_test_groups": ["group-1"],
                }
            ),
            data_dir=tmp_path,
            shadow_reviews=reviews,
        )
        await bridge.start()
        await bridge.handle_event(
            {
                "message_id": "social-runtime-event",
                "group_id": "group-1",
                "user_id": "42",
                "time": 1_700_000_010,
                "message": [
                    {"type": "text", "data": {"text": "@你 看一下"}},
                    {"type": "at", "data": {"qq": "323537051"}},
                ],
            }
        )
        items = reviews.list_items(persona_id="aemeath", group_id="group-1")
        await bridge.close()
        return items

    items = asyncio.run(scenario())
    assert len(items) == 1
    assert items[0].runtime_mode == "SOCIAL_RUNTIME"
    assert items[0].source_kind != "installed_live_shadow"


def test_unknown_owner_remains_unknown_in_runtime_capture(tmp_path):
    async def scenario():
        path = tmp_path / "groupmate-social-runtime-v2.db"
        reviews = ShadowReviewRepository(path)
        bridge = AstrBotSocialRuntimeBridge(
            context=object(),
            settings=SocialRuntimeSettings.from_mapping(
                {"runtime_mode": "SHADOW", "enabled_groups": ["group-1"]}
            ),
            data_dir=tmp_path,
            shadow_reviews=reviews,
        )
        await bridge.start()
        await bridge.handle_event(
            {
                "message_id": "unknown-owner-event",
                "group_id": "group-1",
                "user_id": "42",
                "time": 1_700_000_020,
                "message": [
                    {"type": "text", "data": {"text": "@你 普通消息"}},
                    {"type": "at", "data": {"qq": "323537051"}},
                ],
            }
        )
        item = reviews.list_items(
            persona_id="aemeath", group_id="group-1"
        )[0]
        await bridge.close()
        return item

    assert asyncio.run(scenario()).capture.ownership == "UNKNOWN"


def test_external_plugin_trigger_projects_no_attention_compatibility_decision(tmp_path):
    async def scenario():
        path = tmp_path / "groupmate-social-runtime-v2.db"
        reviews = ShadowReviewRepository(path)
        bridge = AstrBotSocialRuntimeBridge(
            context=object(),
            settings=SocialRuntimeSettings.from_mapping(
                {
                    "runtime_mode": "SHADOW",
                    "enabled_groups": ["group-1"],
                    "external_command_prefixes": ["xw=astrbot.waves"],
                }
            ),
            data_dir=tmp_path,
            shadow_reviews=reviews,
        )
        await bridge.start()
        await bridge.handle_event(
            {
                "message_id": "external-command-event",
                "group_id": "group-1",
                "user_id": "42",
                "time": 1_700_000_030,
                "message": [{"type": "text", "data": {"text": "xw帮助"}}],
            }
        )
        ProjectionConsumer(path, "evaluation").consume(100)
        item = reviews.list_items(
            persona_id="aemeath", group_id="group-1"
        )[0]
        projection = ProjectionQueries(path).evaluation(
            persona_id="aemeath", group_id="group-1"
        )
        scene_version = (
            await bridge.manager.group_snapshot("group-1")
        ).scene_version
        calls = bridge.manager.execution_port.calls
        outbox_count = bridge.manager.event_store.outbox_count()
        await bridge.close()
        return item, projection, scene_version, calls, outbox_count

    item, projection, scene_version, calls, outbox_count = asyncio.run(scenario())
    assert item.capture.evaluation_lane == "EXTERNAL_PLUGIN_COMPATIBILITY"
    assert item.capture.ownership == "EXTERNAL_PLUGIN"
    assert item.capture.prediction["attention"] is False
    assert item.capture.prediction["action"] is False
    assert item.capture.attention["trigger_kind"] == "EXTERNAL_COMPATIBILITY"
    assert item.capture.candidate_actions == ()
    assert item.capture.governor["reason_codes"] == ["external_plugin_owned"]
    assert any(
        value["entity_ref"] == item.entity_ref
        for value in projection["items"]
    )
    assert scene_version == 1
    assert calls == ()
    assert outbox_count == 0


class _FailFirstRuntimeCapture(ShadowReviewRepository):
    def __init__(self, path):
        super().__init__(path)
        self.failed = False

    def capture_runtime(self, evaluation):
        if not self.failed:
            self.failed = True
            raise RuntimeError("injected capture outage")
        return super().capture_runtime(evaluation)


class _FailFirstShadowProjection(ShadowReviewRepository):
    def __init__(self, path):
        super().__init__(path)
        self.failed = False

    def _append_projection_effect(self, event, item):
        if not self.failed:
            self.failed = True
            raise RuntimeError("injected projection outage")
        return super()._append_projection_effect(event, item)


@pytest.mark.parametrize("external", [False, True])
def test_restart_recovers_durable_shadow_capture_without_replaying_runtime(
    tmp_path, external
):
    async def scenario():
        path = tmp_path / "groupmate-social-runtime-v2.db"
        settings_values = {
            "runtime_mode": "SHADOW",
            "enabled_groups": ["group-1"],
        }
        message = [
            {"type": "text", "data": {"text": "@你 看一下"}},
            {"type": "at", "data": {"qq": "323537051"}},
        ]
        if external:
            settings_values["external_command_prefixes"] = ["xw=astrbot.waves"]
            message = [{"type": "text", "data": {"text": "xw帮助"}}]
        settings = SocialRuntimeSettings.from_mapping(settings_values)
        event = {
            "message_id": "recover-external" if external else "recover-normal",
            "group_id": "group-1",
            "user_id": "42",
            "time": 1_700_000_040,
            "message": message,
        }

        failing = _FailFirstRuntimeCapture(path)
        first = AstrBotSocialRuntimeBridge(
            object(), settings, tmp_path, shadow_reviews=failing
        )
        await first.start()
        await first.handle_event(event)
        assert failing.list_items(persona_id="aemeath", group_id="group-1") == ()
        assert first.shadow_review_error == "RuntimeError: injected capture outage"
        assert (await first.manager.group_snapshot("group-1")).scene_version == 1
        await first.close()

        reviews = ShadowReviewRepository(path)
        restarted = AstrBotSocialRuntimeBridge(
            object(), settings, tmp_path, shadow_reviews=reviews
        )
        await restarted.start()
        recovered_without_new_chat = reviews.list_items(
            persona_id="aemeath", group_id="group-1"
        )
        duplicate = await restarted.handle_event(event)
        items = reviews.list_items(persona_id="aemeath", group_id="group-1")
        scene_version = (
            await restarted.manager.group_snapshot("group-1")
        ).scene_version
        calls = restarted.manager.execution_port.calls
        outbox_count = restarted.manager.event_store.outbox_count()
        await restarted.close()

        with connect_database(path) as db:
            world_effects = db.execute(
                "SELECT COUNT(*) FROM journal WHERE effect_type='group_world.projected'"
            ).fetchone()[0]
            governor_results = db.execute(
                "SELECT COUNT(*) FROM governor_results"
            ).fetchone()[0]
            capture_effects = db.execute(
                "SELECT COUNT(*) FROM journal "
                "WHERE effect_type='evaluation.shadow_decision_captured'"
            ).fetchone()[0]
        return (
            recovered_without_new_chat,
            duplicate,
            items,
            scene_version,
            calls,
            outbox_count,
            world_effects,
            governor_results,
            capture_effects,
        )

    (
        recovered_without_new_chat,
        duplicate,
        items,
        scene_version,
        calls,
        outbox_count,
        world_effects,
        governor_results,
        capture_effects,
    ) = asyncio.run(scenario())
    assert len(recovered_without_new_chat) == 1
    assert duplicate.inserted is False
    assert len(items) == 1
    assert items[0].capture.evaluation_lane == (
        "EXTERNAL_PLUGIN_COMPATIBILITY" if external else "SOCIAL_CONVERSATION"
    )
    assert scene_version == 1
    assert calls == ()
    assert outbox_count == 0
    assert world_effects == 1
    assert governor_results == (0 if external else 1)
    assert capture_effects == 1


def test_restart_repairs_projection_after_shadow_record_was_committed(tmp_path):
    async def scenario():
        path = tmp_path / "groupmate-social-runtime-v2.db"
        settings = SocialRuntimeSettings.from_mapping(
            {"runtime_mode": "SHADOW", "enabled_groups": ["group-1"]}
        )
        event = {
            "message_id": "recover-partial-projection",
            "group_id": "group-1",
            "user_id": "42",
            "time": 1_700_000_050,
            "message": [
                {"type": "text", "data": {"text": "@你 帮忙看看"}},
                {"type": "at", "data": {"qq": "323537051"}},
            ],
        }
        failing = _FailFirstShadowProjection(path)
        first = AstrBotSocialRuntimeBridge(
            object(), settings, tmp_path, shadow_reviews=failing
        )
        await first.start()
        await first.handle_event(event)
        assert len(failing.list_items(persona_id="aemeath", group_id="group-1")) == 1
        await first.close()

        with connect_database(path) as db:
            assert db.execute(
                "SELECT COUNT(*) FROM journal "
                "WHERE effect_type='evaluation.shadow_decision_captured'"
            ).fetchone()[0] == 0

        reviews = ShadowReviewRepository(path)
        restarted = AstrBotSocialRuntimeBridge(
            object(), settings, tmp_path, shadow_reviews=reviews
        )
        await restarted.start()
        items = reviews.list_items(persona_id="aemeath", group_id="group-1")
        await restarted.close()
        with connect_database(path) as db:
            capture_effects = db.execute(
                "SELECT COUNT(*) FROM journal "
                "WHERE effect_type='evaluation.shadow_decision_captured'"
            ).fetchone()[0]
        return items, capture_effects

    items, capture_effects = asyncio.run(scenario())
    assert len(items) == 1
    assert capture_effects == 1


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
            "quality": {"task": True, "delivery": True, "recovery": True},
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


class _ExternalStealRuntime(_FixedRuntime):
    def evaluate(self, scenario, worker_mode):
        result = dict(super().evaluate(scenario, worker_mode))
        if scenario["evaluation_lane"] == "EXTERNAL_PLUGIN_COMPATIBILITY":
            result["outbox"] = (
                {
                    "correlation_id": scenario["external_response_correlation"],
                    "part": "stolen-response",
                },
            )
        return result


class _CapabilityDeliveryRegressionRuntime(_FixedRuntime):
    def evaluate(self, scenario, worker_mode):
        result = super().evaluate(scenario, worker_mode)
        if scenario["evaluation_lane"] == "GROUPMATE_CAPABILITY":
            result["quality"] = {
                "task": True,
                "delivery": False,
                "recovery": True,
            }
        return result


class _MissingQualityRuntime(_FixedRuntime):
    def evaluate(self, scenario, worker_mode):
        result = super().evaluate(scenario, worker_mode)
        result.pop("quality")
        return result


def _all_lane_repository(tmp_path):
    repository = ShadowReviewRepository(
        tmp_path / "groupmate-social-runtime-v2.db"
    )
    lanes = (
        ("SOCIAL_CONVERSATION", "GROUPMATE"),
        ("GROUPMATE_CAPABILITY", "GROUPMATE"),
        ("EXTERNAL_PLUGIN_COMPATIBILITY", "EXTERNAL_PLUGIN"),
    )
    for index in range(120):
        lane, ownership = lanes[index % len(lanes)]
        item = repository.record(
            _capture(
                index,
                category=SCENES[index % len(SCENES)],
                lane=lane,
                ownership=ownership,
            )
        )
        repository.review(
            item.entity_ref,
            reviewer_id="admin:root",
            decision="reasonable",
            reviewed_at=2_000 + index,
        )
    return repository


def test_calibration_fails_closed_without_required_lane_coverage_and_diffs_each_lane(tmp_path):
    social_only = _reviewed_repository(tmp_path / "social-only")
    required = _release(
        lane_minimums={
            "SOCIAL_CONVERSATION": 1,
            "GROUPMATE_CAPABILITY": 1,
            "EXTERNAL_PLUGIN_COMPATIBILITY": 1,
        },
        capability_minimums={"task": 1.0, "delivery": 1.0, "recovery": 1.0},
        compatibility_minimums={
            "no_steal": 1.0,
            "no_duplicate": 1.0,
            "no_self_attribution": 1.0,
        },
    )
    social_only.freeze(
        persona_id="aemeath", group_id="group-1", release_config=required
    )
    proposed = {
        "attention_window_ms": 4_000,
        "reply_length_tendency": "short",
        "media_preference": "contextual",
        "participation_weights": {"direct": 1.1},
    }
    unavailable = ShadowCalibrationService(social_only).run(
        persona_id="aemeath",
        group_id="group-1",
        proposed_config=proposed,
        release_config=required,
        baseline_runtime=_FixedRuntime(),
        candidate_runtime=_FixedRuntime(),
    )
    assert unavailable.status == "REJECTED"
    assert "capability_coverage_unavailable" in unavailable.reason_codes
    assert "external_compatibility_coverage_unavailable" in unavailable.reason_codes

    mixed = _all_lane_repository(tmp_path / "mixed")
    mixed.freeze(
        persona_id="aemeath", group_id="group-1", release_config=required
    )
    degraded = ShadowCalibrationService(mixed).run(
        persona_id="aemeath",
        group_id="group-1",
        proposed_config=proposed,
        release_config=required,
        baseline_runtime=_FixedRuntime(),
        candidate_runtime=_ExternalStealRuntime(),
    )
    assert degraded.status == "REJECTED"
    assert "holdout_external_no_steal_regression" in degraded.reason_codes
    assert set(degraded.comparison["holdout"]["candidate"]["lanes"]) == {
        "SOCIAL_CONVERSATION",
        "GROUPMATE_CAPABILITY",
        "EXTERNAL_PLUGIN_COMPATIBILITY",
    }

    capability_degraded = ShadowCalibrationService(mixed).run(
        persona_id="aemeath",
        group_id="group-1",
        proposed_config=proposed,
        release_config=required,
        baseline_runtime=_FixedRuntime(),
        candidate_runtime=_CapabilityDeliveryRegressionRuntime(),
    )
    assert capability_degraded.status == "REJECTED"
    assert (
        "holdout_capability_delivery_regression"
        in capability_degraded.reason_codes
    )
    assert "holdout_capability_delivery_failed" in capability_degraded.reason_codes

    unavailable_baseline = ShadowCalibrationService(mixed).run(
        persona_id="aemeath",
        group_id="group-1",
        proposed_config=proposed,
        release_config=required,
        baseline_runtime=_MissingQualityRuntime(),
        candidate_runtime=_FixedRuntime(),
    )
    assert unavailable_baseline.status == "REJECTED"
    assert "capability_coverage_unavailable" in unavailable_baseline.reason_codes


@pytest.mark.parametrize(
    "overrides",
    (
        {"lane_minimums": {
            "SOCIAL_CONVERSATION": 0,
            "GROUPMATE_CAPABILITY": 0,
            "EXTERNAL_PLUGIN_COMPATIBILITY": 0,
        }},
        {
            "lane_minimums": {
                "SOCIAL_CONVERSATION": 1,
                "GROUPMATE_CAPABILITY": 0,
                "EXTERNAL_PLUGIN_COMPATIBILITY": 0,
            },
            "capability_minimums": {
                "task": 0.1, "delivery": 0.0, "recovery": 0.0,
            },
        },
        {
            "lane_minimums": {
                "SOCIAL_CONVERSATION": 1,
                "GROUPMATE_CAPABILITY": 1,
                "EXTERNAL_PLUGIN_COMPATIBILITY": 0,
            },
            "capability_minimums": {
                "task": 0.0, "delivery": 0.0, "recovery": 0.0,
            },
        },
        {
            "lane_minimums": {
                "SOCIAL_CONVERSATION": 1,
                "GROUPMATE_CAPABILITY": 0,
                "EXTERNAL_PLUGIN_COMPATIBILITY": 0,
            },
            "compatibility_minimums": {
                "no_steal": 0.1,
                "no_duplicate": 0.0,
                "no_self_attribution": 0.0,
            },
        },
    ),
)
def test_release_config_requires_explicit_consistent_lane_applicability(overrides):
    with pytest.raises(ValueError, match="lane|minimum|applicability"):
        _release(**overrides)


def test_frozen_manifest_detects_any_evaluation_content_mutation(tmp_path):
    repository = _reviewed_repository(tmp_path)
    repository.freeze(
        persona_id="aemeath", group_id="group-1", release_config=_release()
    )
    with connect_database(repository.path) as db:
        db.execute(
            "UPDATE shadow_review_items SET categories_json='[\"care\"]' "
            "WHERE decision_id=(SELECT decision_id FROM shadow_review_items "
            "ORDER BY occurred_at LIMIT 1)"
        )
    with pytest.raises(ValueError, match="content"):
        repository.frozen_corpus(persona_id="aemeath", group_id="group-1")


def test_calibration_approval_rejects_stale_baseline_and_merges_full_config(tmp_path):
    repository = _reviewed_repository(tmp_path)
    repository.freeze(
        persona_id="aemeath", group_id="group-1", release_config=_release()
    )
    configs = ConfigVersionRepository(repository.path)
    base = {
        "retention_days": 30,
        "attention_window_ms": 3_000,
        "reply_length_tendency": "balanced",
        "media_preference": "text_only",
        "participation_weights": {"direct": 1.0},
    }
    configs.create_draft(
        "group-behavior", base,
        persona_id="aemeath", group_id="group-1", now=1,
    )
    configs.validate(
        "group-behavior", persona_id="aemeath", group_id="group-1"
    )
    configs.publish(
        "group-behavior", persona_id="aemeath", group_id="group-1",
        expected_version=0,
    )
    proposed = {
        "attention_window_ms": 4_000,
        "reply_length_tendency": "short",
        "media_preference": "contextual",
        "participation_weights": {"direct": 1.1},
    }
    service = ShadowCalibrationService(repository, config_repository=configs)
    stale = service.run(
        persona_id="aemeath", group_id="group-1",
        proposed_config=proposed, release_config=_release(),
        baseline_runtime=_FixedRuntime(), candidate_runtime=_FixedRuntime(),
    )
    assert stale.baseline_config_version == 1
    assert len(stale.baseline_config_digest) == 64

    changed = {**base, "retention_days": 45}
    configs.create_draft(
        "manual-change", changed,
        persona_id="aemeath", group_id="group-1", now=2,
    )
    configs.validate(
        "manual-change", persona_id="aemeath", group_id="group-1"
    )
    configs.publish(
        "manual-change", persona_id="aemeath", group_id="group-1",
        expected_version=1,
    )
    commands = CommandService(
        repository.path,
        persona_id="aemeath",
        group_ids=("group-1",),
        admin_ids=("admin:root",),
        config_repository=configs,
        shadow_repository=repository,
    )
    context = CommandContext(
        admin_id="admin:root", persona_id="aemeath", group_id="group-1",
        expected_version=0, reason="reviewed", confirmed=True,
    )
    with pytest.raises(ShadowCalibrationRejected, match="stale"):
        commands.execute(ApproveCalibration(stale.entity_ref), context)

    fresh = service.run(
        persona_id="aemeath", group_id="group-1",
        proposed_config=proposed, release_config=_release(),
        baseline_runtime=_FixedRuntime(), candidate_runtime=_FixedRuntime(),
    )
    approved = commands.execute(ApproveCalibration(fresh.entity_ref), context)
    published = configs.load(
        approved.data["config_id"], approved.data["config_version"]
    )
    assert published.config["retention_days"] == 45
    assert {
        name: published.config[name] for name in proposed
    } == proposed


def test_calibration_config_identity_is_opaque_and_scoped_by_persona_and_group(tmp_path):
    repository = ShadowReviewRepository(
        tmp_path / "groupmate-social-runtime-v2.db"
    )
    proposed = {
        "attention_window_ms": 4_000,
        "reply_length_tendency": "short",
        "media_preference": "contextual",
        "participation_weights": {"direct": 1.1},
    }
    digest = hashlib.sha256(b"{}").hexdigest()
    runs = {
        persona: repository.save_calibration(
            persona_id=persona,
            group_id="shared-raw-group",
            manifest_version=1,
            proposed_config=proposed,
            comparison={},
            baseline_config_version=0,
            baseline_config_digest=digest,
            status="PENDING_APPROVAL",
            reason_codes=(),
        )
        for persona in ("persona-alpha", "persona-beta")
    }
    config_ids = {}
    for persona, run in runs.items():
        commands = CommandService(
            repository.path,
            persona_id=persona,
            group_ids=("shared-raw-group",),
            admin_ids=("admin:root",),
            shadow_repository=repository,
        )
        approved = commands.execute(
            ApproveCalibration(run.entity_ref),
            CommandContext(
                admin_id="admin:root",
                persona_id=persona,
                group_id="shared-raw-group",
                expected_version=0,
                reason="independent scoped approval",
                confirmed=True,
            ),
        )
        config_id = approved.data["config_id"]
        config_ids[persona] = config_id
        assert persona not in config_id
        assert "shared-raw-group" not in config_id
        rollback = commands.execute(
            RestoreConfig(config_id, 1),
            CommandContext(
                admin_id="admin:root",
                persona_id=persona,
                group_id="shared-raw-group",
                expected_version=1,
                reason="independent rollback drill",
                confirmed=True,
            ),
        )
        assert rollback.data["version"] == 2

    assert config_ids["persona-alpha"] != config_ids["persona-beta"]


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
    assert set(
        projected["summary"]["comparison"]["holdout"]["candidate"]["lanes"]
    ) == {
        "SOCIAL_CONVERSATION",
        "GROUPMATE_CAPABILITY",
        "EXTERNAL_PLUGIN_COMPATIBILITY",
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
    published = commands.config_repository.load(approved.data["config_id"], 1)
    assert published.status is ConfigStatus.PUBLISHED
    assert set(published.config) == set(proposed)

    rollback = commands.execute(
        RestoreConfig(approved.data["config_id"], 1),
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
