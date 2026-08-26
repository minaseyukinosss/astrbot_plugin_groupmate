from __future__ import annotations

import asyncio
import json

from groupmate.adapters.participants import ParticipantDirectory
from groupmate.adapters.web_api import ControlPlaneWebAPI, WebRequest
from groupmate.social_runtime.control.commands import CommandService
from groupmate.social_runtime.control.queries import ProjectionQueries
from groupmate.social_runtime.control.stream import ProjectionStream
from groupmate.social_runtime.profile.contracts import (
    ProfileEpisode,
    ProfileFact,
    ProfileSnapshot,
    SocialEdge,
)
from groupmate.social_runtime.profile.group_portrait import GroupPortraitBuilder
from groupmate.social_runtime.profile.repository import ProfileRepository


def _seed(path, tmp_path):
    participants = ParticipantDirectory(path, tmp_path / "avatars")
    member = participants.remember_actor(
        persona_id="persona",
        group_id="group-1",
        actor_id="raw-qq-151",
        display_name="玲151",
        updated_at=200,
    )
    other = participants.remember_actor(
        persona_id="persona",
        group_id="group-1",
        actor_id="raw-qq-sai",
        display_name="小赛151",
        updated_at=200,
    )
    repo = ProfileRepository(path)
    fact = ProfileFact(
        fact_id="fact-1",
        persona_id="persona",
        group_id="group-1",
        subject_id="raw-qq-151",
        category="behavior_pattern",
        summary="会持续追问到问题真正落地",
        source_kind="observed_pattern",
        source_actor_id="raw-qq-151",
        source_event_ids=("event-1", "event-2", "event-3"),
        confidence=0.94,
        status="confirmed",
        evidence_count=3,
        valid_from=100,
        injectable=True,
    )
    repo.put_fact(fact)
    repo.put_snapshot(
        ProfileSnapshot(
            persona_id="persona",
            group_id="group-1",
            subject_id="raw-qq-151",
            one_line_portrait="会持续追问到问题真正落地",
            group_roles=("体验把关者",),
            individual_fingerprints=("会持续追问到问题真正落地",),
            preferences_and_boundaries=("不接受看不懂的结果",),
            representative_episode_ids=("episode-1",),
            relationship_summary="技术同伴",
            maturity="forming",
            source_revision=3,
            generated_at=200,
        )
    )
    repo.put_episode(
        ProfileEpisode(
            episode_id="episode-1",
            persona_id="persona",
            group_id="group-1",
            title="一起修复线上错行",
            summary="持续指出字体与错行问题，直到修复落地。",
            participants=("raw-qq-151", "raw-qq-sai"),
            source_event_ids=("event-1", "event-2"),
            episode_type="shared_achievement",
            valence=0.7,
            importance=0.9,
            confidence=0.94,
            status="confirmed",
            occurred_at=100,
            last_reinforced_at=180,
        )
    )
    edge = SocialEdge(
        edge_id="edge-1",
        persona_id="persona",
        group_id="group-1",
        source_member_id="raw-qq-151",
        target_member_id="raw-qq-sai",
        relation_type="technical_peer",
        direction="bidirectional",
        strength=0.72,
        confidence=0.91,
        source_event_ids=("event-1", "event-2", "event-3"),
        status="confirmed",
        valid_from=100,
        valid_until=None,
        last_observed_at=180,
    )
    repo.put_edge(edge)
    repo.put_group_portrait(
        GroupPortraitBuilder().build(
            persona_id="persona",
            group_id="group-1",
            member_snapshots=(repo.snapshot("persona", "group-1", "raw-qq-151"),),
            culture=("重视问题落地",),
            topic_counts={"插件开发": 4},
            activity_hours=(22, 23),
            edges=(edge,),
            source_revision=3,
            generated_at=200,
        )
    )
    return participants, member, other


def _api(path, participants):
    service = CommandService(
        path,
        persona_id="persona",
        group_ids=("group-1",),
        admin_ids=("admin:root",),
    )
    return ControlPlaneWebAPI(
        queries=ProjectionQueries(path),
        stream=ProjectionStream(path),
        command_service_for=lambda _username: service,
        event_publisher=lambda _event: None,
        persona_id="persona",
        group_ids=("group-1",),
        admin_ids=("admin:root",),
        participants=participants,
    )


def _request(endpoint, *, username="admin:root", **query):
    return WebRequest(
        method="GET",
        path=endpoint,
        query={"persona_id": "persona", "group_id": "group-1", **query},
        headers={},
        json_body=None,
        username=username,
    )


def test_profile_endpoints_are_admin_only_group_scoped_and_hide_raw_ids(tmp_path):
    path = tmp_path / "groupmate-social-runtime-v2.db"
    participants, member, _other = _seed(path, tmp_path)
    api = _api(path, participants)

    listing = asyncio.run(api.handle(_request("/profiles")))
    detail = asyncio.run(
        api.handle(_request("/profile", member_ref=member["member_ref"]))
    )
    portrait = asyncio.run(api.handle(_request("/group-portrait")))
    denied = asyncio.run(api.handle(_request("/profiles", username="member:1")))

    assert listing.status == detail.status == portrait.status == 200
    assert denied.status == 403
    assert listing.body["scope"] == {"persona_id": "persona", "group_id": "group-1"}
    assert listing.body["items"][0]["summary"]["display_name"] == "玲151"
    assert detail.body["items"][0]["summary"]["snapshot"]["one_line_portrait"] == "会持续追问到问题真正落地"
    assert detail.body["items"][0]["summary"]["episodes"][0]["title"] == "一起修复线上错行"
    assert detail.body["items"][0]["summary"]["relations"][0]["other_display_name"] == "小赛151"
    assert portrait.body["items"][0]["summary"]["common_topics"] == ["插件开发"]
    rendered = json.dumps([listing.body, detail.body, portrait.body], ensure_ascii=False)
    assert "raw-qq-151" not in rendered
    assert "raw-qq-sai" not in rendered


def test_profile_detail_rejects_member_ref_from_another_group(tmp_path):
    path = tmp_path / "groupmate-social-runtime-v2.db"
    participants, _member, _other = _seed(path, tmp_path)
    foreign = participants.remember_actor(
        persona_id="persona",
        group_id="group-2",
        actor_id="foreign",
        display_name="外群成员",
        updated_at=200,
    )
    api = _api(path, participants)

    response = asyncio.run(
        api.handle(_request("/profile", member_ref=foreign["member_ref"]))
    )

    assert response.status == 404
