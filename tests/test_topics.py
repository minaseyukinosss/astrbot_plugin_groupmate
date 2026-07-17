from groupmate.topics import TopicWindow


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

