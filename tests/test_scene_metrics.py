from eval.scene_metrics import SceneObservation, aggregate_scene_metrics
from groupmate.models import InteractionScene


def test_metrics_are_conditioned_on_interaction_scene():
    observations = (
        SceneObservation(
            scene=InteractionScene.DIRECT_ADDRESS,
            replied=True,
            quoted=True,
            media=False,
            reply_chars=8,
            latency_seconds=5,
        ),
        SceneObservation(
            scene=InteractionScene.DIRECT_ADDRESS,
            replied=False,
        ),
        SceneObservation(
            scene=InteractionScene.AMBIENT_CONTRIBUTION,
            replied=True,
            quoted=False,
            media=True,
            reply_chars=20,
            latency_seconds=12,
        ),
        SceneObservation(
            scene=InteractionScene.AMBIENT_CONTRIBUTION,
            replied=False,
        ),
        SceneObservation(
            scene=InteractionScene.AMBIENT_CONTRIBUTION,
            replied=False,
        ),
        SceneObservation(
            scene=InteractionScene.AMBIENT_CONTRIBUTION,
            replied=False,
        ),
    )

    metrics = aggregate_scene_metrics(observations)

    direct = metrics[InteractionScene.DIRECT_ADDRESS.value]
    ambient = metrics[InteractionScene.AMBIENT_CONTRIBUTION.value]
    assert direct["opportunities"] == 2
    assert direct["reply_rate"] == 0.5
    assert direct["quote_rate_given_reply"] == 1.0
    assert direct["media_rate_given_reply"] == 0.0
    assert direct["median_reply_chars"] == 8
    assert ambient["opportunities"] == 4
    assert ambient["reply_rate"] == 0.25
    assert ambient["quote_rate_given_reply"] == 0.0
    assert ambient["media_rate_given_reply"] == 1.0


def test_overall_metrics_do_not_replace_scene_metrics():
    observations = (
        SceneObservation(
            scene=InteractionScene.REPLY_TO_BOT,
            replied=True,
            quoted=True,
            reply_chars=10,
            latency_seconds=4,
        ),
        SceneObservation(
            scene=InteractionScene.AMBIENT_CONTRIBUTION,
            replied=False,
        ),
    )

    metrics = aggregate_scene_metrics(observations)

    assert metrics["overall"]["reply_rate"] == 0.5
    assert InteractionScene.REPLY_TO_BOT.value in metrics
    assert InteractionScene.AMBIENT_CONTRIBUTION.value in metrics
