from __future__ import annotations

from groupmate.social_runtime.contracts import SocialEventEnvelope
from groupmate.social_runtime.profile.identity import IdentityService
from groupmate.social_runtime.profile.repository import ProfileRepository


def _event(*, event_id: str, actor_id: str, name: str, group_id: str = "group-1"):
    return SocialEventEnvelope.create(
        event_id=event_id,
        event_type="platform.message",
        occurred_at=100,
        received_at=100,
        persona_id="persona",
        group_id=group_id,
        actor_id=actor_id,
        source_message_id=event_id,
        correlation_id=event_id,
        causation_id=None,
        payload={
            "platform": "qq",
            "sender": {"id": actor_id, "name": name},
            "text": "你好",
        },
    )


def _service(tmp_path) -> IdentityService:
    return IdentityService(
        ProfileRepository(tmp_path / "groupmate-social-runtime-v2.db")
    )


def test_nickname_change_updates_one_identity_and_keeps_alias_history(tmp_path):
    service = _service(tmp_path)

    first = service.observe(
        _event(event_id="event-1", actor_id="u151", name="玲151")
    )
    second = service.observe(
        _event(
            event_id="event-2",
            actor_id="u151",
            name="玲151🍅（已离线）",
        )
    )

    assert first.actor_id == second.actor_id == "u151"
    assert second.display_name == "玲151🍅（已离线）"
    assert [
        item.alias for item in service.aliases("persona", "group-1", "u151")
    ] == ["玲151", "玲151🍅（已离线）"]


def test_similar_aliases_never_merge_different_actor_ids(tmp_path):
    service = _service(tmp_path)
    service.observe(
        _event(event_id="event-ling", actor_id="u-ling", name="玲151")
    )
    service.observe(
        _event(event_id="event-sai", actor_id="u-sai", name="小赛151")
    )

    ling = service.resolve("persona", "qq", "u-ling")
    sai = service.resolve("persona", "qq", "u-sai")

    assert ling is not None and ling.display_name == "玲151"
    assert sai is not None and sai.display_name == "小赛151"
    assert ling.actor_id != sai.actor_id


def test_same_actor_profile_facts_remain_group_local(tmp_path):
    service = _service(tmp_path)
    service.observe(
        _event(event_id="event-g1", actor_id="same-user", name="一群昵称")
    )
    service.observe(
        _event(
            event_id="event-g2",
            actor_id="same-user",
            name="二群昵称",
            group_id="group-2",
        )
    )

    identity = service.resolve("persona", "qq", "same-user")
    assert identity is not None and identity.display_name == "二群昵称"
    assert [
        item.alias
        for item in service.aliases("persona", "group-1", "same-user")
    ] == ["一群昵称"]
    assert [
        item.alias
        for item in service.aliases("persona", "group-2", "same-user")
    ] == ["二群昵称"]
