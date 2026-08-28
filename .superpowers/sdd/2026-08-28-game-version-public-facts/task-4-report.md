# Task 4 report — frozen AstrBot official-source probe adapter

## Implementation

Implemented the SHADOW-only `AstrBotOfficialSourceProbe` in
`groupmate/adapters/astrbot_official_sources.py`. It has no HTTP client and
uses only a context-injected async host capability:
`fetch_official_source(url=..., timeout_seconds=...)`.

The adapter deduplicates registered canonical URLs, applies an independent
timeout to each fetch, rejects changed or unsafe redirect targets, and parses
only HTML metadata (`publisher`, title, publication time). It never forwards
page body text or exceptions. Evidence uses the registered source identity,
has a SHA-256 body fingerprint, and uses `published_at=None` when the metadata
has no valid publication time. Missing/mismatched publisher metadata produces
no domain evidence and an incomplete aggregate.

`AstrBotSocialRuntimeBridge` now exposes the adapter as
`official_source_probe` for a later scheduler task. It starts no scheduling
work and does not add any reply-path integration.

## Files changed

- `groupmate/adapters/astrbot_official_sources.py` (new)
- `groupmate/adapters/astrbot_bridge.py`
- `tests/contracts/test_official_source_probe.py` (new; four test functions)
- `.superpowers/sdd/2026-08-28-game-version-public-facts/task-4-report.md`

## TDD evidence

RED command:

```text
.venv/bin/python -m pytest -q tests/contracts/test_official_source_probe.py

==================================== ERRORS ====================================
________ ERROR collecting tests/contracts/test_official_source_probe.py ________
ImportError while importing test module '/Users/minase/Desktop/ams/astrbot_plugin_groupmate/tests/contracts/test_official_source_probe.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
tests/contracts/test_official_source_probe.py:9: in <module>
    from groupmate.adapters.astrbot_official_sources import AstrBotOfficialSourceProbe
E   ModuleNotFoundError: No module named 'groupmate.adapters.astrbot_official_sources'
=========================== short test summary info ============================
ERROR tests/contracts/test_official_source_probe.py
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.31s
```

GREEN command:

```text
.venv/bin/python -m pytest -q tests/contracts/test_official_source_probe.py
..........                                                               [100%]
10 passed in 0.33s
```

Minimum compatibility command:

```text
.venv/bin/python -m pytest -q tests/contracts/test_official_source_probe.py tests/shared/test_astrbot_package_loading.py
...........                                                              [100%]
11 passed in 0.50s
```

## Coverage and self-review

There are exactly four new test functions. Their table-driven cases cover
success, metadata-only page-instruction handling, missing publication time,
publisher missing/mismatch partials, duplicate URLs, timeout, unavailable
provider, unsafe private redirect, and secret-bearing exception text. They
assert that requested URLs stay within the registry, output evidence remains
bounded, and diagnostics contain only fixed domain codes.

Self-review confirmed: no direct HTTP imports or client, no key persistence,
no AstrBot imports (so package loading remains safe without AstrBot), no
member/Persona/relationship data reads, no raw page body or exception detail in
the result, and no scheduler or reply-path behavior.

## Concerns

The host must explicitly provide `context.fetch_official_source`; absent that
capability intentionally produces the fixed `official_probe_unavailable`
result. A future deployment integration must provide this constrained host
capability before Task 5 schedules probes.
