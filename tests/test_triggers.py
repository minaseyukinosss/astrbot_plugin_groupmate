from groupmate.models import GroupPolicy, TriggerKind
from groupmate.triggers import TriggerRouter


def build_router():
    return TriggerRouter(GroupPolicy(aliases=("爱弥斯", "小爱", "飞行雪绒")))


def test_existing_command_is_bypassed(message_factory):
    result = build_router().classify(message_factory(text="help", is_command=True))

    assert result.kind is TriggerKind.COMMAND


def test_native_at_is_not_generated_by_plugin(message_factory):
    result = build_router().classify(message_factory(text="在吗", mentions_bot=True))

    assert result.kind is TriggerKind.NATIVE_DIRECT


def test_alias_direct_and_alias_discussion_are_distinct(message_factory):
    router = build_router()

    assert router.classify(message_factory(text="小爱，在吗")).kind is TriggerKind.ALIAS_DIRECT
    assert (
        router.classify(message_factory(text="小爱是不是挺难调的")).kind
        is TriggerKind.ALIAS_MENTION
    )


def test_bot_and_empty_messages_are_ignored(message_factory):
    router = build_router()

    assert router.classify(message_factory(is_bot=True)).kind is TriggerKind.IGNORE
    assert router.classify(message_factory(text="", segment_types=())).kind is TriggerKind.IGNORE

