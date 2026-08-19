from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[2]
PAGE = ROOT / "pages" / "settings"


def _run_module(filename: str, body: str):
    source = (PAGE / filename).read_bytes()
    encoded = base64.b64encode(source).decode("ascii")
    script = (
        f"const module = await import('data:text/javascript;base64,{encoded}');\n"
        f"{body}"
    )
    result = subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_router_has_exact_five_hash_routes_and_safe_fallback():
    result = _run_module(
        "router.js",
        "console.log(JSON.stringify({"
        "routes: module.ROUTES.map((item) => item.path),"
        "known: module.normalizeHash('#/people?subject=x'),"
        "unknown: module.normalizeHash('#/not-a-route'),"
        "empty: module.normalizeHash('')"
        "}));",
    )

    assert result == {
        "routes": [
            "/runtime",
            "/persona",
            "/people",
            "/activity",
            "/governance",
        ],
        "known": "/people",
        "unknown": "/runtime",
        "empty": "/runtime",
    }


def test_store_ignores_stale_entities_and_never_optimistically_applies_commands():
    result = _run_module(
        "store.js",
        "const store = new module.ProjectionStore();"
        "store.setScope({persona_id:'aemeath',group_id:'group-1',available_groups:['group-1']});"
        "store.merge({projection:'people', projection_version:2, items:[{"
        "entity_ref:'people:one', projection_version:2, summary:{status:'active'}"
        "}]});"
        "store.trackCommand({command_id:'command:1', expected_version:2});"
        "const pendingAfterCommand = store.snapshot().pendingCommands.length;"
        "const afterCommand = store.selectEntity('people:one');"
        "store.merge({projection:'people', projection_version:1, items:[{"
        "entity_ref:'people:one', projection_version:1, summary:{status:'stale'}"
        "}]});"
        "const afterStale = store.selectEntity('people:one');"
        "store.applyProjectionEvent({cursor:3, kind:'memory.updated',"
        "scope:{persona_id:'aemeath',group_id:'group-1'},"
        "entity:'people:one', projection_version:3, summary:{status:'corrected'}});"
        "const pendingAfterUnrelated = store.snapshot().pendingCommands.length;"
        "store.applyProjectionEvent({cursor:4, kind:'control.runtime_paused',"
        "scope:{persona_id:'aemeath',group_id:'group-1'},"
        "entity:'governance:command', projection_version:4,"
        "summary:{command_id:'command:1', paused:true}});"
        "console.log(JSON.stringify({"
        "afterCommand, afterStale, current:store.selectEntity('people:one'),"
        "liveView:store.selectView('people'),"
        "pendingAfterCommand, pendingAfterUnrelated,"
        "pendingAfterProjection:store.snapshot().pendingCommands.length"
        "}));",
    )

    assert result["afterCommand"]["summary"]["status"] == "active"
    assert result["afterStale"]["summary"]["status"] == "active"
    assert result["current"]["summary"]["status"] == "corrected"
    assert result["current"]["projection_version"] == 3
    assert result["liveView"]["items"][0]["summary"]["status"] == "corrected"
    assert result["liveView"]["cursor"] == 3
    assert result["liveView"]["projection_version"] == 3
    assert result["pendingAfterCommand"] == 1
    assert result["pendingAfterUnrelated"] == 1
    assert result["pendingAfterProjection"] == 0


def test_polling_snapshot_reconciles_only_its_matching_command_id():
    result = _run_module(
        "store.js",
        "const store = new module.ProjectionStore();"
        "store.trackCommand({command_id:'command:poll', expected_version:0});"
        "store.merge({projection:'governance', projection_version:1, items:[{"
        "entity_ref:'governance:other', projection_version:1,"
        "summary:{command_id:'command:other'}"
        "}]});"
        "const afterOther = store.snapshot().pendingCommands.length;"
        "store.merge({projection:'governance', projection_version:2, items:[{"
        "entity_ref:'governance:poll', projection_version:2,"
        "summary:{command_id:'command:poll'}"
        "}]});"
        "console.log(JSON.stringify({"
        "afterOther, afterMatch:store.snapshot().pendingCommands.length"
        "}));",
    )

    assert result == {"afterOther": 1, "afterMatch": 0}


def test_scope_change_clears_old_group_state_and_rejects_late_sse_events():
    result = _run_module(
        "store.js",
        "const store = new module.ProjectionStore();"
        "store.setScope({persona_id:'aemeath',group_id:'group-1',available_groups:['group-1','group-2']});"
        "store.merge({projection:'people',projection_version:9,items:[{"
        "entity_ref:'people:group-one',projection_version:9,summary:{status:'private-one'}"
        "}]});"
        "store.setScope({persona_id:'aemeath',group_id:'group-2',available_groups:['group-1','group-2']});"
        "const lateMerge=store.merge({projection:'people',projection_version:10,"
        "scope:{persona_id:'aemeath',group_id:'group-1'},items:[{"
        "entity_ref:'people:late-http',projection_version:10,summary:{status:'must-not-cross-http'}"
        "}]});"
        "store.merge({projection:'people',projection_version:1,"
        "scope:{persona_id:'aemeath',group_id:'group-2'},items:[{"
        "entity_ref:'people:group-two',projection_version:1,summary:{status:'private-two'}"
        "}]});"
        "const lateApplied=store.applyProjectionEvent({cursor:10,kind:'memory.updated',"
        "scope:{persona_id:'aemeath',group_id:'group-1'},entity:'people:late-one',"
        "projection_version:10,summary:{status:'must-not-cross'}});"
        "console.log(JSON.stringify({lateMerge,lateApplied,snapshot:store.snapshot()}));",
    )

    assert result["lateMerge"] is False
    assert result["lateApplied"] is False
    assert set(result["snapshot"]["entities"]) == {"people:group-two"}
    assert [
        item["entity_ref"] for item in result["snapshot"]["views"]["people"]["items"]
    ] == ["people:group-two"]
    assert result["snapshot"]["scope"]["group_id"] == "group-2"


def test_store_tracks_connection_and_error_impact_without_faking_domain_success():
    result = _run_module(
        "store.js",
        "const store = new module.ProjectionStore();"
        "store.setConnection({state:'polling', impact:'Live updates delayed up to 15s'});"
        "store.setError({status:409, code:'conflict', impact:'Refresh before retry'});"
        "console.log(JSON.stringify(store.snapshot()));",
    )

    assert result["connection"] == {
        "state": "polling",
        "impact": "Live updates delayed up to 15s",
    }
    assert result["error"] == {
        "status": 409,
        "code": "conflict",
        "impact": "Refresh before retry",
    }
