# Direct DeepSeek Ambient Cognition Design

## Goal

Make ordinary-group participation judgments complete inside Groupmate's short decision window without changing the model used for final chat replies. Groupmate will call DeepSeek directly for AMBIENT cognition, explicitly disable thinking, request compact JSON, and keep all authorization in local policy.

## Evidence and root cause

The rc.16 trace shows zero queue delay, a 5 KB request, and eight seconds entirely inside Provider wait. The current cognition path calls AstrBot `Context.llm_generate()` with the same Provider used for final replies. It does not set DeepSeek's `thinking`, `response_format`, `max_tokens`, or retry policy. DeepSeek V4 defaults thinking to enabled with high effort, so the non-streaming call can spend the full decision window producing reasoning before the final structured response.

The model is not treated as faulty. The failure is a task-to-call mismatch: a short participation gate is sent through a general reply Provider with parameters Groupmate does not reliably control.

## Considered approaches

1. Increase the AMBIENT timeout. Rejected because it makes the bot react to stale conversations and does not remove unnecessary reasoning.
2. Configure a second AstrBot Provider with DeepSeek thinking disabled. Viable, but still leaves cognition coupled to AstrBot Provider internals and duplicates a full reply Provider for a small classifier.
3. Give Groupmate a direct DeepSeek cognition client. Selected because it provides per-request control, keeps reply generation unchanged, and creates a narrow boundary that can be measured and tested independently.

## Runtime boundary

Only `AMBIENT` cognition uses the direct client. Command ownership, FAST direct interactions, CONTINUATION, local rules, Participation Policy, Governor, reply planning, and final reply generation remain unchanged.

```text
ordinary group messages
  -> bounded AMBIENT attention window
  -> local level-zero rules
  -> Groupmate DeepSeek cognition client
  -> compact participation verdict
  -> local observation normalization
  -> Participation Policy
  -> Social Governor
  -> ACT / OBSERVE / SILENCE

ACT reply generation
  -> existing AstrBot Provider
```

## Configuration

Groupmate adds three direct-cognition settings:

- `cognition_api_key`: required secret for enabled SHADOW or SOCIAL_RUNTIME operation.
- `cognition_api_base`: defaults to `https://api.deepseek.com`.
- `cognition_model`: defaults to `deepseek-v4-flash`; operators may choose `deepseek-v4-pro`.

The existing `generation_provider` remains required for final reply generation. API keys must never appear in traces, exceptions returned to the page, logs, or exported evidence.

An upgraded installation without `cognition_api_key` must report a clear runtime blocker instead of silently falling back to the slow AstrBot cognition path.

## Direct request

The client sends one non-streaming `POST {base}/chat/completions` request with no automatic retry:

```json
{
  "model": "deepseek-v4-flash",
  "stream": false,
  "thinking": {"type": "disabled"},
  "response_format": {"type": "json_object"},
  "max_tokens": 192,
  "temperature": 0.1,
  "messages": [
    {"role": "system", "content": "Return one compact JSON participation verdict."},
    {"role": "user", "content": "Evaluate the supplied bounded recent-group facts as JSON."}
  ]
}
```

The HTTP client uses a six-second total timeout inside the existing eight-second AMBIENT budget, leaving time for validation and local governance. It performs no retry because a retried verdict would be stale.

## Compact model contract

The model returns one dominant signal and one participation verdict:

```json
{
  "decision": "speak",
  "signal": "help_request",
  "target_id": "123456",
  "evidence_event_ids": ["qq:789"],
  "confidence": 0.86,
  "disruption": 0.10,
  "novelty": 0.82,
  "reason": "A member asked an unanswered question."
}
```

Allowed signals are `help_request`, `care_signal`, `humor_signal`, `greeting`, `boundary_signal`, and `none`. `decision` is `speak` or `silence`. The response remains advisory and never contains reply text.

Groupmate validates target and evidence IDs against the frozen AttentionFrame, then locally supplies scene version, expiry, topic identity, participation-assessment fields, and safe defaults. Invalid or out-of-scope data degrades to OBSERVE.

## Input minimization

The direct prompt includes only facts needed for the verdict:

- at most 12 recent focus events with event ID, actor ID, bounded text/segment labels, and reply/mention facts;
- at most 4 focused topics;
- at most 8 candidate audience IDs and display names;
- bounded activity, conversation lease, and relevant persona participation guidance.

Internal frame hashes, config versions, the full generic JSON Schema, database details, media URLs, and unrelated persona fields are not sent. The target is a materially smaller request than the current 5 KB generic worker envelope.

## Failure behavior and observability

- HTTP timeout, DNS/connect failure, 401/403, 429, 5xx, malformed JSON, or invalid scope produces a typed safe diagnostic and OBSERVE.
- No response body, API key, URL query, raw prompt, or reasoning content is persisted.
- Existing numeric diagnostics remain: queue wait, network/model wait, request bytes, and actual deadline.
- The runtime page labels the cognition backend and safe failure category so an operator can distinguish authentication, rate limiting, upstream failure, invalid output, and timeout.
- Old traces remain readable and are not backfilled.

## Lifecycle

The direct client is created when the bridge starts and closed when the plugin stops or reloads. One shared async session is reused rather than creating a connection per message. The existing cognition concurrency gate continues to cap simultaneous calls.

## Verification

Focused tests will cover:

- exact DeepSeek request parameters, including thinking disabled, JSON mode, 192-token cap, and no retry;
- compact response normalization and rejection of unknown target/evidence IDs;
- safe classification of timeout, authentication, rate-limit, upstream, and malformed-output failures;
- separation of direct cognition from AstrBot reply generation;
- configuration blockers and secret redaction;
- persistence and page rendering compatibility for new and old diagnostics;
- the existing SHADOW participation and hard-governance scenarios.

No live DeepSeek request is made by the automated test suite. Online acceptance requires several new post-install SHADOW events whose direct cognition completes within six seconds and produces a valid participation verdict.
