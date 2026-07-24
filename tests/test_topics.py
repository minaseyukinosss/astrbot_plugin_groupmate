from groupmate.engine.topics import (
    ACTIVE_CONTEXT_MAX_MESSAGES,
    TOPIC_IDLE_GAP_SECONDS,
    TopicWindow,
    select_active_messages,
)


def test_topic_window_is_bounded(message_factory):
    window = TopicWindow(group_id="g1", max_messages=3)

    for index in range(5):
        window.append(
            message_factory(message_id=str(index), timestamp=index, text=str(index))
        )

    assert [item.message_id for item in window.snapshot().messages] == ["2", "3", "4"]


def test_topic_window_deduplicates_message_identity(message_factory):
    window = TopicWindow(group_id="g1", max_messages=3)
    message = message_factory(message_id="same")

    assert window.append(message) is True
    assert window.append(message) is False
    assert len(window.snapshot().messages) == 1


def test_select_active_messages_filters_by_topic_created_at(message_factory):
    older = message_factory(message_id="old", timestamp=100, text="鸣潮要倒闭了")
    newer = message_factory(message_id="new", timestamp=300, text="战双有4w倍率")

    selected = select_active_messages((older, newer), topic_created_at=300)

    assert [item.message_id for item in selected] == ["new"]


def test_select_active_messages_cuts_on_idle_gap(message_factory):
    first = message_factory(message_id="a", timestamp=100, text="鸣潮要倒闭了")
    second = message_factory(
        message_id="b",
        timestamp=100 + TOPIC_IDLE_GAP_SECONDS + 1,
        text="战双有4w倍率",
    )
    third = message_factory(
        message_id="c",
        timestamp=100 + TOPIC_IDLE_GAP_SECONDS + 5,
        text="真不是填错了吗",
    )

    selected = select_active_messages((first, second, third), topic_created_at=0)

    assert [item.message_id for item in selected] == ["b", "c"]


def test_select_active_messages_caps_count(message_factory):
    messages = [
        message_factory(
            message_id=f"m{index}",
            timestamp=1000 + index,
            text=f"msg-{index}",
        )
        for index in range(ACTIVE_CONTEXT_MAX_MESSAGES + 5)
    ]

    selected = select_active_messages(messages, topic_created_at=0)

    assert len(selected) == ACTIVE_CONTEXT_MAX_MESSAGES
    assert selected[0].message_id == "m5"
    assert selected[-1].message_id == "m12"


def test_select_active_messages_empty_input():
    assert select_active_messages((), topic_created_at=10) == ()
