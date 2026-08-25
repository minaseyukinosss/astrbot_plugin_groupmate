import asyncio

from groupmate.adapters.participants import ParticipantDirectory
from groupmate.social_runtime.contracts import SocialEventEnvelope


def _event(*, card: str = "", nickname: str = "小夏"):
    return SocialEventEnvelope.create(
        event_id="qq:1",
        event_type="platform.message",
        occurred_at=10,
        received_at=10,
        persona_id="groupmate:default",
        group_id="g-1",
        actor_id="42",
        source_message_id="1",
        correlation_id="qq:1",
        causation_id=None,
        payload={
            "sender": {"id": "42", "name": card or nickname},
            "text": "你好",
        },
    )


def test_participant_prefers_card_and_hides_raw_qq_id(tmp_path):
    directory = ParticipantDirectory(tmp_path / "runtime.db", tmp_path / "avatars")

    participant = directory.remember(_event(card="夏夏", nickname="小夏"))

    assert participant["display_name"] == "夏夏"
    assert participant["avatar_ref"].startswith("participant:")
    assert "42" not in participant["avatar_ref"]
    assert "42" not in participant["member_ref"]


def test_participant_can_be_resolved_inside_the_same_group(tmp_path):
    directory = ParticipantDirectory(tmp_path / "runtime.db", tmp_path / "avatars")
    remembered = directory.remember(_event(card="夏夏"))

    resolved = directory.resolve_actor(
        persona_id="groupmate:default",
        group_id="g-1",
        actor_id="42",
    )

    assert resolved == remembered
    assert directory.resolve_actor(
        persona_id="groupmate:default",
        group_id="another-group",
        actor_id="42",
    ) is None


def test_active_members_are_scoped_recent_and_can_exclude_bot(tmp_path):
    directory = ParticipantDirectory(tmp_path / "runtime.db", tmp_path / "avatars")
    for actor_id, group_id, updated_at in (
        ("recent", "g-1", 100),
        ("old", "g-1", 20),
        ("bot", "g-1", 101),
        ("other-group", "g-2", 102),
    ):
        directory.remember_actor(
            persona_id="groupmate:default",
            group_id=group_id,
            actor_id=actor_id,
            display_name=actor_id,
            updated_at=updated_at,
        )

    active = directory.active_members(
        persona_id="groupmate:default",
        group_id="g-1",
        since=70,
        exclude_actor_ids=("bot",),
    )

    assert active == (
        {"actor_id": "recent", "display_name": "recent", "updated_at": 100},
    )


def test_recent_actor_ids_are_read_only_and_keep_scope(tmp_path):
    directory = ParticipantDirectory(tmp_path / "runtime.db", tmp_path / "avatars")
    for actor_id, group_id, updated_at in (
        ("recent", "g-1", 100),
        ("old", "g-1", 20),
        ("other-group", "g-2", 101),
    ):
        directory.remember_actor(
            persona_id="groupmate:default",
            group_id=group_id,
            actor_id=actor_id,
            display_name=actor_id,
            updated_at=updated_at,
        )

    assert directory.recent_actor_ids(
        persona_id="groupmate:default",
        group_id="g-1",
        since=70,
    ) == frozenset({"recent"})
    assert directory.active_members(
        persona_id="groupmate:default",
        group_id="g-1",
        since=0,
    ) == (
        {"actor_id": "recent", "display_name": "recent", "updated_at": 100},
        {"actor_id": "old", "display_name": "old", "updated_at": 20},
    )


def test_avatar_failure_returns_stable_generated_svg(tmp_path):
    async def failing_fetcher(_url):
        raise OSError("offline")

    directory = ParticipantDirectory(
        tmp_path / "runtime.db",
        tmp_path / "avatars",
        fetcher=failing_fetcher,
    )
    participant = directory.remember(_event(card="夏夏"))

    first = asyncio.run(directory.avatar_data(participant["avatar_ref"]))
    second = asyncio.run(directory.avatar_data(participant["avatar_ref"]))

    assert first == second
    assert first["source"] == "fallback"
    assert first["data_uri"].startswith("data:image/svg+xml;base64,")
    assert "42" not in first["data_uri"]


def test_avatar_success_is_cached_as_a_data_uri(tmp_path):
    calls = []

    async def fetcher(url):
        calls.append(url)
        return b"\x89PNG\r\n\x1a\nimage", "image/png"

    directory = ParticipantDirectory(
        tmp_path / "runtime.db",
        tmp_path / "avatars",
        fetcher=fetcher,
    )
    participant = directory.remember(_event())

    first = asyncio.run(directory.avatar_data(participant["avatar_ref"]))
    second = asyncio.run(directory.avatar_data(participant["avatar_ref"]))

    assert first["source"] == "qq"
    assert first["data_uri"].startswith("data:image/png;base64,")
    assert second == first
    assert len(calls) == 1
