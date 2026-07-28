from eval.behavior_metrics import BehaviorObservation, aggregate_behavior_metrics


def _observation(
    scene,
    act,
    *,
    replied=True,
    media=False,
    allowed=False,
    false_completion=False,
    duplicate_media=False,
    reply_chars=12,
    latency_ms=80,
):
    return BehaviorObservation(
        scene=scene,
        act=act,
        replied=replied,
        media=media,
        media_allowed=allowed,
        false_completion=false_completion,
        duplicate_media=duplicate_media,
        reply_chars=reply_chars,
        latency_ms=latency_ms,
    )


def test_metrics_report_scene_and_act_without_runtime_targets():
    report = aggregate_behavior_metrics(
        [
            _observation(
                "social_response", "reciprocate", media=True, allowed=True
            ),
            _observation(
                "social_response", "boundary", media=False, allowed=False
            ),
            _observation(
                "task_request",
                "task_unsupported",
                false_completion=True,
            ),
        ]
    )

    assert report["by_scene"]["social_response"]["replies"] == 2
    assert report["by_act"]["reciprocate"]["media_given_reply"] == 1.0
    assert report["violations"]["forbidden_media"] == 0
    assert report["violations"]["false_completion"] == 1
    assert "runtime_probability" not in report


def test_rates_are_conditioned_on_matching_opportunities_and_replies():
    report = aggregate_behavior_metrics(
        [
            _observation(
                "social_response", "reciprocate", media=True, allowed=True
            ),
            _observation(
                "social_response",
                "reciprocate",
                replied=False,
                media=False,
                allowed=True,
                reply_chars=0,
            ),
            _observation(
                "task_request",
                "task_handoff",
                media=True,
                allowed=False,
                duplicate_media=True,
            ),
        ]
    )

    social = report["by_scene"]["social_response"]
    assert social["opportunities"] == 2
    assert social["reply_rate"] == 0.5
    assert social["media_given_reply"] == 1.0
    assert report["violations"] == {
        "forbidden_media": 1,
        "false_completion": 0,
        "duplicate_media": 1,
    }


def test_empty_observations_return_stable_empty_report():
    report = aggregate_behavior_metrics([])

    assert report["by_scene"] == {}
    assert report["by_act"] == {}
    assert report["violations"] == {
        "forbidden_media": 0,
        "false_completion": 0,
        "duplicate_media": 0,
    }
