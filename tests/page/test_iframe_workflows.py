from __future__ import annotations

import asyncio
import base64
import json
import subprocess

from groupmate.adapters.web_api import ControlPlaneWebAPI, WebRequest
from groupmate.social_runtime.contracts import SocialEventEnvelope
from groupmate.social_runtime.control.commands import CommandService
from groupmate.social_runtime.control.projections import ProjectionConsumer
from groupmate.social_runtime.control.queries import ProjectionQueries
from groupmate.social_runtime.control.stream import ProjectionStream
from groupmate.social_runtime.persistence.event_store import SQLiteSocialEventStore


def _seed_person(path):
    store = SQLiteSocialEventStore(path)
    event = SocialEventEnvelope.create(
        event_id="event:person:1",
        event_type="platform.message",
        occurred_at=1,
        received_at=1,
        persona_id="aemeath",
        group_id="group-1",
        actor_id="member-1",
        source_message_id="message:1",
        correlation_id="workflow:1",
        causation_id=None,
        payload={"text": "private source text"},
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
                "effect_id": "effect:person:1",
                "kind": "memory.fact_recorded",
                "subject_id": "member-1",
                "admin_visible": True,
                "fact_summary": "喜欢猫",
                "evidence_event_ids": ["event:person:1"],
            },
        ),
    )
    for name in ProjectionConsumer.PROJECTION_NAMES:
        ProjectionConsumer(path, name).consume(10)


def _request(body):
    return WebRequest(
        method="POST",
        path="/commands",
        query={},
        headers={},
        json_body={
            **body,
            "persona_id": "aemeath",
            "group_id": "group-1",
        },
        username="admin:root",
    )


def test_complete_admin_workflow_preserves_independent_expected_versions(tmp_path):
    path = tmp_path / "groupmate-social-runtime-v2.db"
    _seed_person(path)
    published = []
    service = CommandService(
        path,
        persona_id="aemeath",
        group_ids=("group-1",),
        admin_ids=("admin:root",),
    )
    api = ControlPlaneWebAPI(
        queries=ProjectionQueries(path),
        stream=ProjectionStream(path),
        command_service_for=lambda _username: service,
        event_publisher=published.append,
        persona_id="aemeath",
        group_ids=("group-1",),
        admin_ids=("admin:root",),
    )
    people = asyncio.run(
        api.handle(
            WebRequest(
                method="GET",
                path="/people",
                query={"persona_id": "aemeath", "group_id": "group-1"},
                headers={},
                json_body=None,
                username="admin:root",
            )
        )
    )
    entity_ref = people.body["items"][0]["entity_ref"]

    commands = (
        {"type": "pause", "command_id": "flow:pause", "expected_version": 0, "reason": "inspect safely", "confirmed": False, "payload": {"paused": True}},
        {"type": "config_draft", "command_id": "flow:draft", "expected_version": 0, "reason": "prepare behavior", "confirmed": False, "payload": {"config_id": "behavior", "config": {"style": {"reply_length": "short"}}}},
        {"type": "config_validate", "command_id": "flow:validate", "expected_version": 0, "reason": "validate behavior", "confirmed": False, "payload": {"config_id": "behavior"}},
        {"type": "config_dry_run", "command_id": "flow:dry", "expected_version": 0, "reason": "preview behavior", "confirmed": False, "payload": {"config_id": "behavior", "historical_events": [], "worker_outputs": []}},
        {"type": "config_publish", "command_id": "flow:publish", "expected_version": 0, "reason": "publish reviewed behavior", "confirmed": True, "payload": {"config_id": "behavior"}},
        {"type": "correct", "command_id": "flow:correct", "expected_version": 5, "reason": "correct reviewed fact", "confirmed": True, "payload": {"entity_ref": entity_ref, "correction": {"status": "reviewed"}}},
        {"type": "config_restore", "command_id": "flow:restore", "expected_version": 1, "reason": "rollback to reviewed version", "confirmed": True, "payload": {"config_id": "behavior", "source_version": 1}},
    )

    responses = [asyncio.run(api.handle(_request(body))) for body in commands]
    stale = asyncio.run(
        api.handle(
            _request({
                "type": "pause",
                "command_id": "flow:stale",
                "expected_version": 0,
                "reason": "exercise conflict",
                "confirmed": False,
                "payload": {"paused": False},
            })
        )
    )
    refreshed = asyncio.run(
        api.handle(
            _request({
                "type": "pause",
                "command_id": "flow:refresh",
                "expected_version": stale.body["current_version"],
                "reason": "retry after authoritative refresh",
                "confirmed": False,
                "payload": {"paused": False},
            })
        )
    )

    assert [response.status for response in responses] == [202] * len(commands)
    assert stale.status == 409
    assert stale.body["current_version"] == 7
    assert refreshed.status == 202
    assert [event.event_type for event in published] == [
        "control.runtime_paused",
        "control.config_draft_created",
        "control.config_validated",
        "control.config_dry_run",
        "control.config_published",
        "control.social_state_corrected",
        "control.config_restored",
        "control.runtime_resumed",
    ]


def test_page_bridge_reconnects_and_exposes_bounded_polling_state():
    source = (
        __import__("pathlib").Path(__file__).parents[2]
        / "pages"
        / "settings"
        / "bridge.js"
    ).read_bytes()
    encoded = base64.b64encode(source).decode("ascii")
    script = f"""
const module = await import('data:text/javascript;base64,{encoded}');
let handlers;
const calls = [];
const fake = {{
  ready: async () => ({{locale:'zh-CN'}}),
  apiGet: async (endpoint, params) => ({{endpoint, params}}),
  apiPost: async (endpoint, body) => ({{endpoint, body}}),
  subscribeSSE: async (endpoint, nextHandlers, params) => {{
    calls.push(['subscribe', endpoint, params]); handlers = nextHandlers; return 'subscription:1';
  }},
  unsubscribeSSE: async (id) => calls.push(['unsubscribe', id]),
}};
const bridge = new module.ApiBridge(fake);
const states = []; let polls = 0;
await bridge.connect({{
  params: {{group_id:'group-1'}},
  onEvent: (event) => calls.push(['event', event.entity]),
  onState: (state) => states.push(state),
  onPoll: () => {{ polls += 1; }},
}});
handlers.onOpen();
handlers.onMessage({{parsed:{{entity:'tasks:one'}}}});
handlers.onMessage({{parsed:{{kind:'snapshot_required', summary:{{reason:'cursor_expired'}}}}}});
handlers.onError(new Error('offline'));
await bridge.disconnect();
console.log(JSON.stringify({{calls, states, polls}}));
"""
    result = subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        check=True,
        capture_output=True,
        text=True,
    )
    value = json.loads(result.stdout)

    assert value["calls"][0][0:2] == ["subscribe", "events"]
    assert ["event", "tasks:one"] in value["calls"]
    assert ["unsubscribe", "subscription:1"] in value["calls"]
    assert [state["state"] for state in value["states"]] == [
        "connecting",
        "connected",
        "disconnected",
        "polling",
    ]
    assert ["event", None] not in value["calls"]
    assert value["polls"] == 2
