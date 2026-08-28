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

## Fix round 1 — review findings

### Changes

- Added the explicit `OfficialSourceHostCapability` contract. It resolves the
  registry hostname before a fetch, accepts the exact public approved-address
  tuple for a pinned fetch, and returns final/redirect/peer/post-fetch
  attestations.
- `AstrBotOfficialSourceProbe` now requires both host methods, rejects missing
  or malformed attestations, rejects non-global pre-fetch, redirect, peer, and
  post-fetch addresses, requires the pinned peer to be one of the pre-approved
  addresses and still present in the post-fetch resolution, and remains free
  of a plugin HTTP client.
- `AstrBotSocialRuntimeBridge` now accepts an explicit
  `official_source_capability` and passes it to the probe. It never treats raw
  AstrBot context as a safe fetch capability; absence remains the fixed
  unavailable result.
- Aggregate completion now follows the frozen domain contract: every required
  source must be covered. Optional failures do not prevent complete status;
  all missing/mismatched-publisher outcomes produce an empty-evidence partial
  result.

### Covering tests

`tests/contracts/test_official_source_probe.py` remains at exactly four test
functions, using added table rows/assertions for an installed bridge capability,
pre-fetch private DNS, private redirect addresses, private post-fetch
resolution, peer pinning, optional-source completeness, and all-invalid
publisher partial behavior. `tests/shared/test_astrbot_package_loading.py`
continues to cover imports through the AstrBot shim package namespace.

### Verification

```text
.venv/bin/python -m pytest -q tests/contracts/test_official_source_probe.py tests/shared/test_astrbot_package_loading.py
................                                                         [100%]
16 passed in 0.56s
```

Test-function count command/output:

```text
rg -n '^def test_' tests/contracts/test_official_source_probe.py
117:def test_probe_emits_only_bounded_deterministic_metadata(page, expected_published):
248:def test_probe_aggregates_sources_without_bypassing_the_registry(
317:def test_probe_fails_closed_without_leaking_transport_or_redirect_details(
336:def test_bridge_exposes_an_installed_attested_probe_without_scheduling(tmp_path):
```
