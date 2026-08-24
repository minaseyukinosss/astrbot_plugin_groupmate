# Direct Cognition Output Repair Design

## Problem

rc.17 proves that the plugin-owned DeepSeek route is fast and reachable, but it collapses two different failures into `direct_invalid_output`: extracting/parsing the API content and validating the business verdict. The request prompt lists field names but omits a complete JSON example and several invariants enforced by the local worker. A model can therefore follow the prompt closely and still be rejected.

## Considered approaches

1. **Prompt-only repair.** Add an example and stronger wording. This is small, but occasional empty output and real model deviations remain opaque.
2. **Prompt + bounded normalization + safe subcodes (selected).** Keep standard `/chat/completions`, make the contract explicit, normalize only safe silence defaults, and expose stable failure categories without storing raw output.
3. **DeepSeek strict tool calls.** A server-validated JSON Schema is stronger, but currently requires the Beta endpoint and changes the request contract. It is unnecessary for this repair and adds rollout risk.

## Request contract

The system message includes one complete example containing every field:

```json
{
  "decision": "silence",
  "signal": "none",
  "target_id": null,
  "evidence_event_ids": ["event-id-from-input"],
  "confidence": 0.82,
  "disruption": 0.60,
  "novelty": 0.20,
  "reason": "成员仍在自然交流，暂不打断"
}
```

It explicitly states:

- every field is required;
- IDs must be copied from the input;
- `speak` requires a non-`none` signal and evidence;
- `silence` should normally include evidence, but may use an empty evidence array when no single message is decisive;
- the three scores are JSON numbers in `0..1`;
- no wrapper object, Markdown, reply text, or reasoning is allowed.

`response_format: {"type": "json_object"}`, non-thinking mode, 192 output tokens, no retry, and the six-second HTTP timeout remain unchanged.

## Local normalization and safety

- A `silence` verdict with an empty evidence array binds to the newest event in the frozen frame. This cannot authorize a reply and only satisfies the evidence-bearing observation contract.
- A `speak` verdict still requires at least one model-selected in-frame evidence ID.
- Unknown evidence IDs, unknown target IDs, unsupported signals, missing required fields, invalid scores, and `speak + none` remain rejected.
- No remote target, event, score, scene version, expiry, or topic is trusted beyond the existing frozen-frame validation.

## Safe diagnostics

The client emits parse-stage categories:

- `direct_response_empty`
- `direct_response_json_invalid`
- `direct_response_shape_invalid`

The worker emits validation-stage categories:

- `direct_missing_field`
- `direct_invalid_decision`
- `direct_invalid_signal`
- `direct_speak_without_signal`
- `direct_unknown_target`
- `direct_empty_speak_evidence`
- `direct_unknown_evidence`
- `direct_invalid_score`

Only the stable category, backend, model, timing, and request size are persisted. Raw prompts, response content, field values, IDs, and exception messages are not logged or shown.

The runtime inspector maps each category to a concise Chinese explanation. Legacy `direct_invalid_output` records remain readable.

## Verification

Focused tests cover the exact prompt example, empty/invalid API content, every validation subcode, silence evidence normalization, strict speak evidence, trace persistence, presenter wording, and compatibility with rc.17 records. No live DeepSeek call is made during automated verification. The release is then packaged as rc.18 for another SHADOW run.
