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
        "entity:'people:one', projection_version:3, summary:{status:'corrected'}});"
        "console.log(JSON.stringify({"
        "afterCommand, afterStale, current:store.selectEntity('people:one'),"
        "pendingAfterCommand, pendingAfterProjection:store.snapshot().pendingCommands.length"
        "}));",
    )

    assert result["afterCommand"]["summary"]["status"] == "active"
    assert result["afterStale"]["summary"]["status"] == "active"
    assert result["current"]["summary"]["status"] == "corrected"
    assert result["current"]["projection_version"] == 3
    assert result["pendingAfterCommand"] == 1
    assert result["pendingAfterProjection"] == 0


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
