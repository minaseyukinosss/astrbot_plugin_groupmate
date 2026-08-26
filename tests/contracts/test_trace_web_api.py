import asyncio

from groupmate.adapters.web_api import ControlPlaneWebAPI, WebRequest
from groupmate.social_runtime.contracts import SocialEventEnvelope
from groupmate.social_runtime.control.commands import CommandService
from groupmate.social_runtime.control.message_traces import MessageTraceRepository
from groupmate.social_runtime.control.queries import ProjectionQueries
from groupmate.social_runtime.control.stream import ProjectionStream


def _event(message_id="1", group_id="g-1"):
    return SocialEventEnvelope.create(
        event_id=f"qq:{message_id}",
        event_type="platform.message",
        occurred_at=10,
        received_at=10,
        persona_id="groupmate:default",
        group_id=group_id,
        actor_id="42",
        source_message_id=message_id,
        correlation_id=f"qq:{message_id}",
        causation_id=None,
        payload={
            "text": "晚上好",
            "sender": {"id": "42", "name": "夏夏"},
            "interaction_owner": "UNKNOWN",
        },
    )


def _api(
    tmp_path,
    *,
    runtime_status_provider=None,
    persona_status_provider=None,
):
    path = tmp_path / "runtime.db"
    repository = MessageTraceRepository(path)
    repository.record_received(_event(), runtime_mode="SHADOW", now=10)
    async def fail_avatar(_url):
        raise OSError("offline")

    repository.participants.fetcher = fail_avatar
    service = CommandService(
        path,
        persona_id="groupmate:default",
        group_ids=("g-1",),
        admin_ids=("admin:root",),
    )
    return ControlPlaneWebAPI(
        queries=ProjectionQueries(path),
        stream=ProjectionStream(path),
        command_service_for=lambda _username: service,
        event_publisher=lambda _event: None,
        persona_id="groupmate:default",
        group_ids=("g-1",),
        admin_ids=("admin:root",),
        participants=repository.participants,
        runtime_mode="SHADOW",
        runtime_status_provider=runtime_status_provider,
        persona_status_provider=persona_status_provider,
    )


def _get(path, **query):
    group_id = query.pop("group_id", "g-1")
    return WebRequest(
        method="GET",
        path=path,
        query={
            "persona_id": "groupmate:default",
            "group_id": group_id,
            **query,
        },
        headers={},
        json_body=None,
        username="admin:root",
    )


def test_trace_endpoint_is_group_scoped(tmp_path):
    api = _api(tmp_path)

    response = asyncio.run(api.handle(_get("/traces", group_id="g-1")))
    unavailable = asyncio.run(api.handle(_get("/traces", group_id="g-2")))

    assert response.status == 200
    assert response.body["projection"] == "traces"
    assert all(item["summary"]["actor"] for item in response.body["items"])
    assert unavailable.status == 404


def test_trace_endpoint_accepts_cursor_pagination_and_caps_page_size(tmp_path):
    api = _api(tmp_path)
    MessageTraceRepository(api.queries.path).record_received(
        _event("2"), runtime_mode="SHADOW", now=20
    )

    first = asyncio.run(api.handle(_get("/traces", limit="1")))
    second = asyncio.run(
        api.handle(_get("/traces", limit="500", before=first.body["next_cursor"]))
    )

    assert first.status == 200
    assert first.body["total_count"] == 2
    assert len(first.body["items"]) == 1
    assert first.body["has_more"] is True
    assert second.status == 200
    assert second.body["total_count"] == 2
    assert len(second.body["items"]) == 1
    assert second.body["has_more"] is False


def test_avatar_endpoint_only_resolves_registered_opaque_reference(tmp_path):
    api = _api(tmp_path)
    traces = asyncio.run(api.handle(_get("/traces")))
    avatar_ref = traces.body["items"][0]["summary"]["actor"]["avatar_ref"]

    avatar = asyncio.run(api.handle(_get("/avatar", avatar_ref=avatar_ref)))
    unknown = asyncio.run(
        api.handle(_get("/avatar", avatar_ref="participant:unknown"))
    )

    assert avatar.status == 200
    assert avatar.body["data_uri"].startswith("data:image/")
    assert avatar.body["source"] in {"qq", "fallback"}
    assert unknown.status == 404


def test_bootstrap_reports_configured_runtime_instead_of_historic_projection(tmp_path):
    api = _api(tmp_path)

    response = asyncio.run(api.handle(_get("/bootstrap")))

    assert response.status == 200
    assert response.body["configured_runtime_mode"] == "SHADOW"
    assert response.body["runtime_ready"] is True
    assert response.body["runtime_blockers"] == []


def test_bootstrap_reads_live_runtime_and_resolved_persona_on_each_request(tmp_path):
    state = {
        "effective_runtime_mode": "SHADOW",
        "runtime_state": "RUNNING",
        "runtime_ready": True,
        "runtime_blockers": [],
    }
    api = _api(
        tmp_path,
        runtime_status_provider=lambda _group_id: dict(state),
        persona_status_provider=lambda _group_id: {
            "name": "爱弥斯",
            "aliases": ["小爱"],
            "preset": "aemeath_current",
            "preset_label": "爱弥斯（当前剧情）",
        },
    )

    shadow = asyncio.run(api.handle(_get("/bootstrap")))
    state["effective_runtime_mode"] = "SOCIAL_RUNTIME"
    production = asyncio.run(api.handle(_get("/bootstrap")))

    assert shadow.body["configured_runtime_mode"] == "SHADOW"
    assert shadow.body["effective_runtime_mode"] == "SHADOW"
    assert production.body["effective_runtime_mode"] == "SOCIAL_RUNTIME"
    assert production.body["runtime_state"] == "RUNNING"
    assert production.body["resolved_persona"] == {
        "name": "爱弥斯",
        "aliases": ["小爱"],
        "preset": "aemeath_current",
        "preset_label": "爱弥斯（当前剧情）",
    }


def test_media_endpoint_resolves_only_registered_scoped_preview(tmp_path):
    path = tmp_path / "runtime.db"
    repository = MessageTraceRepository(path)
    event = SocialEventEnvelope.create(
        event_id="qq:image-1",
        event_type="platform.message",
        occurred_at=10,
        received_at=10,
        persona_id="groupmate:default",
        group_id="g-1",
        actor_id="42",
        source_message_id="image-1",
        correlation_id="qq:image-1",
        causation_id=None,
        payload={
            "text": "",
            "sender": {"id": "42", "name": "夏夏"},
            "interaction_owner": "UNKNOWN",
            "segments": [
                {
                    "type": "image",
                    "data": {"url": "https://multimedia.nt.qq.com.cn/demo.jpg"},
                }
            ],
        },
    )
    repository.record_received(event, runtime_mode="SHADOW", now=10)

    async def fetch_media(url, max_bytes):
        assert url == "https://multimedia.nt.qq.com.cn/demo.jpg"
        assert max_bytes == 4 * 1024 * 1024
        return b"\x89PNG\r\n\x1a\npreview", "image/png"

    repository.media.fetcher = fetch_media
    service = CommandService(
        path,
        persona_id="groupmate:default",
        group_ids=("g-1",),
        admin_ids=("admin:root",),
    )
    api = ControlPlaneWebAPI(
        queries=ProjectionQueries(path),
        stream=ProjectionStream(path),
        command_service_for=lambda _username: service,
        event_publisher=lambda _event: None,
        persona_id="groupmate:default",
        group_ids=("g-1",),
        admin_ids=("admin:root",),
        participants=repository.participants,
        message_media=repository.media,
        runtime_mode="SHADOW",
    )
    traces = asyncio.run(api.handle(_get("/traces")))
    media_ref = traces.body["items"][0]["summary"]["message"]["parts"][0]["media_ref"]

    preview = asyncio.run(api.handle(_get("/media", media_ref=media_ref)))
    unknown = asyncio.run(api.handle(_get("/media", media_ref="media:unknown")))

    assert preview.status == 200
    assert preview.body == {
        "data_uri": "data:image/png;base64,iVBORw0KGgpwcmV2aWV3",
        "kind": "image",
        "mime_type": "image/png",
        "name": "",
    }
    assert unknown.status == 404
