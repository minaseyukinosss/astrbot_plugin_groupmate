-- Groupmate installed-live SHADOW snapshot audit (aggregate-only).
-- Run without mutating the exported database:
-- sqlite3 'file:/path/to/groupmate-shadow-20260824.db?mode=ro&immutable=1' \
--   < analysis/shadow_20260824.sql

.headers on
.mode box

SELECT 'quick_check' AS check_name, quick_check AS result
FROM pragma_quick_check;

SELECT
  COUNT(*) AS decisions,
  COUNT(DISTINCT decision_id) AS distinct_decisions,
  COUNT(DISTINCT group_id) AS groups,
  COUNT(DISTINCT persona_id) AS personas,
  MIN(datetime(occurred_at, 'unixepoch', 'localtime')) AS first_local,
  MAX(datetime(occurred_at, 'unixepoch', 'localtime')) AS last_local,
  SUM(json_valid(capture_json)) AS valid_capture_json,
  SUM(status <> 'pending') AS reviewed
FROM shadow_review_items;

SELECT
  json_extract(capture_json, '$.governor.outcome') AS outcome,
  json_extract(capture_json, '$.governor.reason_codes') AS reason_codes,
  json_extract(capture_json, '$.governor.constraints') AS constraints,
  COUNT(*) AS decisions
FROM shadow_review_items
GROUP BY 1, 2, 3
ORDER BY decisions DESC;

SELECT
  COALESCE(json_extract(action.value, '$.kind'), '(none)') AS candidate_kind,
  COALESCE(json_extract(action.value, '$.proposed_act'), '(none)') AS proposed_act,
  COUNT(*) AS decisions
FROM shadow_review_items AS review
LEFT JOIN json_each(review.capture_json, '$.candidate_actions') AS action
GROUP BY 1, 2
ORDER BY decisions DESC;

SELECT
  json_extract(evidence_json, '$.evaluation.frame.trigger_kind') AS trigger_kind,
  json_array_length(evidence_json, '$.evaluation.frame.requested_workers') AS workers,
  COUNT(*) AS decisions,
  MIN(CAST(completed_at - json_extract(
    evidence_json, '$.evaluation.source_event.occurred_at'
  ) AS INTEGER)) AS minimum_seconds,
  ROUND(AVG(CAST(completed_at - json_extract(
    evidence_json, '$.evaluation.source_event.occurred_at'
  ) AS INTEGER)), 1) AS average_seconds,
  MAX(CAST(completed_at - json_extract(
    evidence_json, '$.evaluation.source_event.occurred_at'
  ) AS INTEGER)) AS maximum_seconds,
  SUM(json_extract(
    evidence_json, '$.evaluation.candidates[0].proposed_act'
  ) = 'observe_without_action') AS degraded_candidates,
  SUM(json_extract(
    evidence_json, '$.evaluation.candidates[0].kind'
  ) IS NULL) AS no_candidates
FROM shadow_capture_evidence
GROUP BY 1, 2
ORDER BY decisions DESC;

SELECT
  status,
  COALESCE(json_extract(resolution_json, '$.kind'), '(none)') AS resolution_kind,
  COALESCE(json_extract(resolution_json, '$.reason_code'), '(none)') AS reason_code,
  COUNT(*) AS requests
FROM scene_work_requests
GROUP BY 1, 2, 3
ORDER BY requests DESC;

SELECT
  COUNT(*) AS inbox_messages,
  SUM(status = 'committed') AS committed,
  SUM(status = 'failed') AS failed
FROM inbox;

SELECT
  COUNT(*) AS traces,
  SUM(json_extract(state_json, '$.route.owner') = 'GROUPMATE') AS routed_to_groupmate,
  SUM(json_extract(state_json, '$.decision.outcome') = 'OBSERVE') AS decided,
  SUM(json_extract(state_json, '$.decision.outcome') = 'PENDING') AS pending_projection
FROM message_traces;

SELECT
  COALESCE(json_extract(state_json, '$.message.media_types'), '[]') AS media_types,
  COUNT(*) AS messages
FROM message_traces
GROUP BY 1
ORDER BY messages DESC;

SELECT
  date(datetime(occurred_at, 'unixepoch', 'localtime')) AS local_day,
  COUNT(*) AS decisions,
  SUM(json_extract(capture_json, '$.prediction.action') = 1) AS predicted_actions,
  SUM(status <> 'pending') AS reviewed
FROM shadow_review_items
GROUP BY 1
ORDER BY local_day;
