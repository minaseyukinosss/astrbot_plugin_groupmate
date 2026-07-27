from groupmate.core.scenes import classify_scene, is_hard_scene, policy_for_scene
from groupmate.models import ChatMessage, InteractionScene, QuoteMode, TriggerKind


def message(text="今天好热", **overrides):
    values = dict(
        message_id="m1",
        group_id="g1",
        sender_id="u1",
        sender_name="Alice",
        text=text,
        timestamp=100,
    )
    values.update(overrides)
    return ChatMessage(**values)


def test_reply_to_bot_is_its_own_scene():
    scene = classify_scene(
        TriggerKind.NATIVE_DIRECT,
        message(reply_to_bot=True, reply_to_message_id="bot-1"),
    )

    assert scene is InteractionScene.REPLY_TO_BOT
    assert policy_for_scene(scene).quote_mode is QuoteMode.ALWAYS


def test_leading_alias_is_direct_address_not_task_by_default():
    scene = classify_scene(
        TriggerKind.ALIAS_DIRECT,
        message("小爱，今天好热"),
    )

    assert scene is InteractionScene.DIRECT_ADDRESS
    assert policy_for_scene(scene).quote_mode is QuoteMode.ALWAYS


def test_explicit_capability_request_is_task_scene():
    scene = classify_scene(
        TriggerKind.ALIAS_DIRECT,
        message("小爱，帮我把这十三个群名片改掉"),
    )

    assert scene is InteractionScene.TASK_REQUEST
    assert policy_for_scene(scene).quote_mode is QuoteMode.ALWAYS


def test_continuation_is_sender_scoped_scene():
    scene = classify_scene(
        TriggerKind.CONTINUATION,
        message("那第二种呢"),
    )

    assert scene is InteractionScene.ACTIVE_CONTINUATION
    assert policy_for_scene(scene).quote_mode is QuoteMode.WHEN_INTERLEAVED


def test_ordinary_message_is_ambient_contribution():
    scene = classify_scene(TriggerKind.CANDIDATE, message())

    assert scene is InteractionScene.AMBIENT_CONTRIBUTION
    assert policy_for_scene(scene).hard_priority is False


def test_direct_praise_is_social_response_scene():
    scene = classify_scene(
        TriggerKind.ALIAS_DIRECT,
        message("小爱真厉害，给你一杯牛奶🥛"),
    )

    assert scene is InteractionScene.SOCIAL_RESPONSE


def test_look_up_request_is_task_scene():
    scene = classify_scene(
        TriggerKind.NATIVE_DIRECT,
        message("帮我看看这张图是什么"),
    )

    assert scene is InteractionScene.TASK_REQUEST


def test_indirect_alias_mention_is_not_social_response():
    scene = classify_scene(
        TriggerKind.ALIAS_MENTION,
        message("听说小爱昨天坏了"),
    )

    assert scene is InteractionScene.AMBIENT_CONTRIBUTION


def test_social_scene_priority_still_follows_user_addressing():
    assert is_hard_scene(
        InteractionScene.SOCIAL_RESPONSE, TriggerKind.ALIAS_DIRECT
    )
    assert not is_hard_scene(
        InteractionScene.SOCIAL_RESPONSE, TriggerKind.ALIAS_MENTION
    )
