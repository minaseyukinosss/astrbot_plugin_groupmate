from __future__ import annotations

import asyncio
import json

from groupmate.adapters.web_api import (
    AstrBotControlPlaneRoutes,
    ControlPlaneWebAPI,
    WebRequest,
)
from groupmate.social_runtime.contracts import SocialEventEnvelope
from groupmate.social_runtime.control.commands import CommandService
from groupmate.social_runtime.control.projections import ProjectionConsumer
from groupmate.social_runtime.control.queries import ProjectionQueries
from groupmate.social_runtime.control.stream import ProjectionStream
from groupmate.social_runtime.persistence.event_store import SQLiteSocialEventStore
from groupmate.social_runtime.persistence.schema import connect_database


def _seed_runtime(path):
    store = SQLiteSocialEventStore(path)
    event = SocialEventEnvelope.create(
        event_id="event:web:1",
        event_type="platform.message",
        occurred_at=1,
        received_at=1,
        persona_id="aemeath",
        group_id="group-1",
        actor_id="member-1",
        source_message_id="message:1",
        correlation_id="corr:web:1",
        causation_id=None,
        payload={"text": "raw message"},
    )
    store.append(event)
    claimed = store.claim(
        "group:aemeath:group-1",
        0,
        1,
        persona_id="aemeath",
        group_id="group-1",
    )[0]
    store.commit(
        "group:aemeath:group-1",
        claimed,
        effects=(
            {
                "effect_id": "effect:web:1",
                "kind": "group_world.projected",
                "scene_version": 1,
            },
        ),
    )
    for name in ProjectionConsumer.PROJECTION_NAMES:
        ProjectionConsumer(path, name).consume(10)


def _api(path, published):
    service = CommandService(
        path,
        persona_id="aemeath",
        group_ids=("group-1",),
        admin_ids=("admin:root",),
    )

    async def publish(event):
        published.append(event)

    return ControlPlaneWebAPI(
        queries=ProjectionQueries(path),
        stream=ProjectionStream(path),
        command_service_for=lambda _username: service,
        event_publisher=publish,
        persona_id="aemeath",
        group_ids=("group-1",),
    )


def _request(path, *, method="GET", body=None, username="admin:root", headers=None):
    return WebRequest(
        method=method,
        path=path,
        query={"persona_id": "aemeath", "group_id": "group-1"},
        headers=headers or {},
        json_body=body,
        username=username,
    )


def test_query_endpoints_read_only_projection_and_never_domain_write_models(tmp_path):
    path = tmp_path / "groupmate-social-runtime-v2.db"
    _seed_runtime(path)
    api = _api(path, [])
    with connect_database(path) as db:
        before = tuple(
            db.execute(
                "SELECT (SELECT COUNT(*) FROM governance_actions), "
                "(SELECT COUNT(*) FROM tasks), (SELECT COUNT(*) FROM memories), "
                "(SELECT COUNT(*) FROM journal)"
            ).fetchone()
        )

    async def scenario():
        responses = []
        for endpoint in (
            "bootstrap",
            "runtime",
            "activity",
            "scenes",
            "people",
            "culture",
            "tasks",
            "persona",
            "governance",
            "evaluation",
            "health",
        ):
            responses.append(await api.handle(_request(f"/{endpoint}")))
        return responses

    responses = asyncio.run(scenario())
    with connect_database(path) as db:
        after = tuple(
            db.execute(
                "SELECT (SELECT COUNT(*) FROM governance_actions), "
                "(SELECT COUNT(*) FROM tasks), (SELECT COUNT(*) FROM memories), "
                "(SELECT COUNT(*) FROM journal)"
            ).fetchone()
        )

    assert all(response.status == 200 for response in responses)
    assert all(response.body["cursor"] == 1 for response in responses)
    assert after == before == (0, 0, 0, 1)


def test_bootstrap_selects_scope_from_server_when_page_has_no_group_context(tmp_path):
    path = tmp_path / "groupmate-social-runtime-v2.db"
    _seed_runtime(path)
    api = _api(path, [])

    response = asyncio.run(
        api.handle(
            WebRequest(
                method="GET",
                path="/bootstrap",
                query={},
                headers={},
                json_body=None,
                username="admin:root",
            )
        )
    )

    assert response.status == 200
    assert response.body["persona_id"] == "aemeath"
    assert response.body["available_groups"] == ["group-1"]
    assert response.body["selected_group_id"] == "group-1"


def test_command_uses_server_username_publishes_event_and_preserves_409(tmp_path):
    path = tmp_path / "groupmate-social-runtime-v2.db"
    _seed_runtime(path)
    published = []
    api = _api(path, published)
    first_body = {
        "type": "pause",
        "command_id": "command:web:1",
        "expected_version": 0,
        "reason": "maintenance",
        "confirmed": False,
        "admin_id": "attacker:body",
        "payload": {"paused": True},
    }
    stale_body = {
        **first_body,
        "command_id": "command:web:2",
        "reason": "second stale command",
        "payload": {"paused": False},
    }

    async def scenario():
        first = await api.handle(
            _request("/commands", method="POST", body=first_body)
        )
        stale = await api.handle(
            _request("/commands", method="POST", body=stale_body)
        )
        return first, stale

    first, stale = asyncio.run(scenario())

    assert first.status == 202
    assert first.body["accepted"] is True
    assert published[0].actor_id == "admin:root"
    assert published[0].actor_id != first_body["admin_id"]
    assert stale.status == 409
    assert stale.body["error"] == "expected_version_conflict"
    assert stale.body["current_version"] == 1


def test_command_accepts_bridge_body_scope_but_still_validates_server_allowlist(tmp_path):
    path = tmp_path / "groupmate-social-runtime-v2.db"
    _seed_runtime(path)
    published = []
    api = _api(path, published)
    body = {
        "type": "pause",
        "command_id": "command:body-scope",
        "expected_version": 0,
        "reason": "page bridge command",
        "confirmed": False,
        "persona_id": "aemeath",
        "group_id": "group-1",
        "payload": {"paused": True},
    }

    response = asyncio.run(
        api.handle(
            WebRequest(
                method="POST",
                path="/commands",
                query={},
                headers={},
                json_body=body,
                username="admin:root",
            )
        )
    )

    assert response.status == 202
    assert published[0].group_id == "group-1"


def test_sse_failure_degrades_health_without_canceling_projection(tmp_path, monkeypatch):
    path = tmp_path / "groupmate-social-runtime-v2.db"
    _seed_runtime(path)
    api = _api(path, [])

    def fail_stream(**_kwargs):
        raise RuntimeError("projection stream unavailable")

    monkeypatch.setattr(api.stream, "read", fail_stream)

    async def scenario():
        failed = await api.handle(
            _request("/events", headers={"Last-Event-ID": "0"})
        )
        health = await api.handle(_request("/health"))
        runtime = await api.handle(_request("/runtime"))
        return failed, health, runtime

    failed, health, runtime = asyncio.run(scenario())

    assert failed.status == 503
    assert failed.body["fallback_poll_seconds"] == 15
    assert health.status == 200
    assert health.body["degraded"] is True
    assert health.body["degraded_reasons"] == ["projection stream unavailable"]
    assert runtime.status == 200


def test_astrbot_routes_use_official_plugin_web_api_registration_contract():
    class Context:
        def __init__(self):
            self.calls = []

        def register_web_api(self, route, handler, methods, description):
            self.calls.append((route, handler, methods, description))

    context = Context()
    routes = AstrBotControlPlaneRoutes(context, api_factory=lambda: None)

    routes.register()

    assert {call[0] for call in context.calls} == {
        f"/astrbot_plugin_groupmate/{endpoint}"
        for endpoint in (
            "bootstrap",
            "runtime",
            "activity",
            "scenes",
            "people",
            "culture",
            "tasks",
            "persona",
            "governance",
            "evaluation",
            "health",
            "commands",
            "events",
        )
    }
    methods = {call[0].rsplit("/", 1)[-1]: call[2] for call in context.calls}
    assert methods["commands"] == ["POST"]
    assert methods["events"] == ["GET"]
