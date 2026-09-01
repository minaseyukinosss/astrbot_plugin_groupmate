-- Groupmate 投递链路验收脚本
--
-- 用法（只读，不改动数据库）：
--   sqlite3 'file:/绝对路径/groupmate-social-runtime-v2.db?mode=ro&immutable=1' \
--     < analysis/verify-delivery.sql
--
-- 每节都会直接输出 PASS / FAIL / 需人工判断，不需要自己算。
-- 判据基线取自 2026-09-01 修复前的生产快照。

.headers on
.mode box

SELECT '=== 0. 数据窗口 ===' AS section;

SELECT
  datetime(MIN(received_at), 'unixepoch', 'localtime') AS first_local,
  datetime(MAX(received_at), 'unixepoch', 'localtime') AS last_local,
  ROUND((MAX(received_at) - MIN(received_at)) / 3600.0, 1) AS span_hours,
  COUNT(*) AS inbox_rows
FROM inbox;

-- 跨度不足 12 小时的话，下面的比例都不可靠，等够时间再看。


SELECT '=== 1. 核心判据：消息到底发出去了没有 ===' AS section;

SELECT
  COUNT(*) AS parts_total,
  SUM(status = 'sent') AS sent,
  SUM(receipt_json IS NOT NULL) AS with_receipt,
  SUM(status = 'expired') AS expired,
  SUM(status = 'failed') AS failed,
  CASE
    WHEN COUNT(*) = 0 THEN 'FAIL：outbox 为空，链路未走到投递'
    WHEN SUM(status = 'sent') = 0 THEN 'FAIL：仍然零投递'
    WHEN SUM(receipt_json IS NOT NULL) = 0 THEN 'FAIL：有 sent 但无回执，未真正调用平台'
    ELSE 'PASS：已产生带回执的真实投递'
  END AS verdict
FROM outbox;

-- 修复前基线：parts_total=2, sent=0, with_receipt=0, expired=2
-- 这一节 FAIL 的话，后面几节只用于定位，不必看比例。


SELECT '=== 2. 端到端延迟是否回落 ===' AS section;

WITH lat AS (
  SELECT CAST(completed_at - json_extract(
           evidence_json, '$.evaluation.source_event.occurred_at') AS INTEGER) AS sec
  FROM shadow_capture_evidence
  WHERE json_extract(evidence_json, '$.evaluation.frame.trigger_kind') = 'AMBIENT'
)
SELECT
  COUNT(*) AS n,
  MIN(sec) AS min_s,
  CAST(AVG(sec) AS INTEGER) AS avg_s,
  MAX(sec) AS max_s,
  CASE
    WHEN COUNT(*) = 0 THEN '需人工判断：窗口内没有 AMBIENT 决策'
    WHEN AVG(sec) <= 12 AND MAX(sec) <= 40 THEN 'PASS：窗口上限已生效'
    WHEN AVG(sec) <= 20 THEN '部分改善：均值回落但长尾仍在，检查 max_s 对应场景'
    ELSE 'FAIL：窗口饥饿未解决，均值仍高'
  END AS verdict
FROM lat;

-- 修复前基线：n=1167, min=4, avg=48, max=191
-- AMBIENT_WINDOW_MAX_SECONDS=8，加上认知与生成，均值落到 12 秒内属正常。


SELECT '=== 3. ACT 之后还有没有静默失败 ===' AS section;

SELECT
  COALESCE(json_extract(state_json, '$.decision.reply_diagnostic'), '(无诊断)') AS diagnostic,
  json_extract(state_json, '$.delivery.status') AS delivery_status,
  COUNT(*) AS n
FROM message_traces
WHERE json_extract(state_json, '$.decision.outcome') = 'ACT'
GROUP BY 1, 2
ORDER BY n DESC;

-- 修复前基线：8 条 PLANNING + diagnostic 为 NULL，完全看不出断点。
-- 现在如果仍有失败，diagnostic 会指明阶段：
--   reply_scene_parse_exception   → 场景解析
--   reply_stance_exception        → 立场决策
--   reply_social_move_exception   → 社交动作
--   reply_group_already_handled   → 同群本轮已处理
--   reply_scene_interpreter_unavailable → 解析器未装配
-- 出现 '(无诊断)' 且 delivery_status 不是 SENT，说明还有未覆盖的静默路径。


SELECT '=== 4. 4.2 效果：决策被丢弃的比例 ===' AS section;

SELECT
  status,
  COALESCE(json_extract(resolution_json, '$.reason_code'), '(none)') AS reason_code,
  COUNT(*) AS n,
  ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM scene_work_requests), 1) AS pct
FROM scene_work_requests
GROUP BY 1, 2
ORDER BY n DESC;

-- 修复前基线：newer_scene_committed 1325 (53.1%)、accepted 1168 (46.8%)
-- 期望：newer_scene_committed 显著下降，accepted 占比上升。
-- 若出现 persona_state_version_changed 占比可观，说明丢弃换了个名字复活，
-- 那是下一个要修的，优先于任何新功能。


SELECT '=== 5. 发言率与通道分布 ===' AS section;

SELECT
  json_extract(state_json, '$.decision.participation_lane') AS lane,
  json_extract(state_json, '$.decision.outcome') AS outcome,
  COUNT(*) AS n
FROM message_traces
WHERE json_extract(state_json, '$.decision.outcome') IS NOT NULL
GROUP BY 1, 2
ORDER BY n DESC;

-- 修复前基线：AMBIENT/OBSERVE 1267、AMBIENT/ACT 9、DIRECT_FAST/ACT 2
-- 注意：AMBIENT 的 OBSERVE 占绝大多数是正常的，目标 Bot 的环境介入也只占 11.5%。
-- 不要因为这里 OBSERVE 多就去调松阈值。


SELECT '=== 6. 快车道是否开始有流量 ===' AS section;

SELECT
  json_extract(envelope_json, '$.payload.mentions_bot') AS mentions_bot,
  json_extract(envelope_json, '$.payload.reply_to_bot') AS reply_to_bot,
  COUNT(*) AS n
FROM inbox
GROUP BY 1, 2
ORDER BY n DESC;

-- 修复前基线：mentions_bot=1 只有 2 条 / 2496。
-- bot 能稳定发言后，这个数应该自然上升（群友开始跟它说话）。
-- 这是滞后指标，第一天不用期待变化。


SELECT '=== 7. 对话续租是否建立 ===' AS section;

SELECT
  (SELECT COUNT(*) FROM sqlite_master
    WHERE type = 'table' AND name = 'conversation_leases') AS lease_table_exists,
  (SELECT COUNT(*) FROM reply_plans) AS reply_plans,
  (SELECT SUM(status = 'sent') FROM reply_plans) AS plans_sent,
  (SELECT COUNT(*) FROM delivery_bundles) AS bundles;

-- 修复前基线：lease_table_exists=0（表都没建过）、reply_plans=2 且全为 enqueued。
-- CONTINUATION 通道依赖续租，而续租依赖至少一次成功发言。
-- 这一节从 0 变成非 0，说明 bot 真正进入了对话循环。
