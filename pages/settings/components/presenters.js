const KIND_LABELS = Object.freeze({
  "group_world.projected": "群聊现场已更新",
  "runtime.mode": "运行模式变更",
  "runtime.paused": "运行已暂停",
  "runtime.resumed": "运行已恢复",
  "persona.mode": "人格状态更新",
  "attention.frame_created": "发现值得关注的消息",
  "cognition.observed": "完成场景理解",
  "governor.decided": "完成参与判断",
  "plan.created": "创建行动计划",
  "task.proposed": "任务已提出",
  "task.queued": "任务进入队列",
  "task.started": "任务开始执行",
  "task.completed": "任务已完成",
  "task.failed": "任务执行失败",
  "capability.result": "能力执行完成",
  "delivery.sent": "消息已交付",
  "delivery.unknown": "交付状态未知",
  "memory.fact_recorded": "记住一条事实",
  "relationship.updated": "关系状态更新",
  "culture.updated": "群文化更新",
  "control.runtime_paused": "管理员暂停运行",
  "control.runtime_resumed": "管理员恢复运行",
  "config.published": "人格配置已发布",
  "evaluation.shadow_decision_captured": "观察决策待复核",
  "calibration.shadow_candidate_evaluated": "校准候选已评估",
});

const FIELD_LABELS = Object.freeze({
  runtime_mode: "运行模式",
  paused: "运行状态",
  status: "状态",
  outcome: "结果",
  disposition: "处理结果",
  task_status: "任务状态",
  result_status: "执行结果",
  progress: "进度",
  error_code: "错误",
  reason_codes: "判断依据",
  constraints: "安全约束",
  direct_request: "直接请求",
  delivery_relevant: "需要交付",
  reconsider_at: "重新考虑时间",
  expires_at: "有效期",
  config_version: "配置版本",
  culture_status: "文化状态",
  decision: "复核结论",
  split: "数据分组",
});

const HIDDEN_FIELDS = new Set([
  "kind",
  "scene_version",
  "control_version",
  "persona_state_version",
  "manifest_version",
  "labels_frozen",
  "command_id",
]);

const VALUE_LABELS = Object.freeze({
  OFF: "未启用",
  SHADOW: "观察模式（不发送）",
  SOCIAL_RUNTIME: "社交运行",
  SILENCE: "保持沉默",
  RESPOND: "准备回复",
  SPEAK: "准备回复",
  ACTION: "执行动作",
  ACT: "执行动作",
  ALLOW: "允许",
  DENY: "已阻止",
  DEFER: "稍后处理",
  PENDING: "等待处理",
  RUNNING: "进行中",
  SUCCEEDED: "已完成",
  FAILED: "失败",
  CANCELLED: "已取消",
  UNKNOWN: "状态未知",
  active: "有效",
  pending: "待处理",
  succeeded: "已完成",
  failed: "失败",
  cancelled: "已取消",
  sent: "已发送",
  reasonable: "合理",
  unreasonable: "不合理",
  insufficient: "证据不足",
  shadow_only: "观察模式限制",
});

const PARTICIPATION_LANE_LABELS = Object.freeze({
  DIRECT_FAST: "直接互动",
  CONTINUATION: "延续对话",
  AMBIENT: "普通群聊观察",
});

const COGNITION_STATE_LABELS = Object.freeze({
  READY: "理解完成",
  DEGRADED: "认知降级",
  PENDING: "等待理解",
  FAILED: "理解失败",
});

const COGNITION_WORKER_LABELS = Object.freeze({
  "level0.rules": "本地规则判断",
  direct_interaction: "直接互动识别",
  ambient_social_assessor: "群聊理解与插话评估",
  scene_interpreter: "群聊场景理解",
  participation_assessor: "插话时机判断",
  social_risk: "社交风险识别",
  context_reconstruction: "上下文重建",
  relationship: "成员关系理解",
});

const COGNITION_DIAGNOSTIC_STATUS_LABELS = Object.freeze({
  SUCCEEDED: "完成",
  TIMED_OUT: "等待超时",
  MODEL_FAILED: "模型调用失败",
  INVALID_OUTPUT: "模型输出无法使用",
  REJECTED: "结果未采用",
  FAILED: "执行失败",
  MISSING: "未执行",
  BUDGET_EXHAUSTED: "调用预算已用尽",
});

const DIRECT_OUTPUT_EXPLANATIONS = Object.freeze({
  direct_response_empty: "认知模型返回了空内容，本次未采用。",
  direct_response_json_invalid: "认知模型返回内容不是可解析的 JSON，本次未采用。",
  direct_response_shape_invalid: "认知模型返回结构不完整，本次未采用。",
  direct_missing_field: "认知模型返回结果缺少必填字段，本次未采用。",
  direct_invalid_decision: "认知模型给出了无效的参与决定，本次未采用。",
  direct_invalid_signal: "认知模型给出了无效的群聊信号，本次未采用。",
  direct_speak_without_signal: "认知模型建议参与，但没有给出有效信号，本次未采用。",
  direct_unknown_target: "认知模型引用了当前候选成员之外的对象，本次未采用。",
  direct_empty_speak_evidence: "认知模型建议参与，但没有提供消息证据，本次未采用。",
  direct_unknown_evidence: "认知模型引用了当前上下文之外的消息，本次未采用。",
  direct_invalid_score: "认知模型返回的评分不在有效范围内，本次未采用。",
});

const PARTICIPATION_DIAGNOSTIC_LABELS = Object.freeze({
  deterministic_direct_fast: "明确 @、回复或直接请求，策略直接进入回复准备",
  deterministic_continuation: "命中当前对话延续窗口",
  deterministic_scope_missing: "缺少明确对象或话题，未生成参与方案",
  model_gated_ambient: "普通群聊由模型判断是否适合参与",
});

export function kindLabel(kind) {
  const value = String(kind || "").trim();
  if (!value) return "运行事件";
  if (KIND_LABELS[value]) return KIND_LABELS[value];
  if (value.startsWith("task.")) return "任务状态更新";
  if (value.startsWith("delivery.")) return "消息交付更新";
  if (value.startsWith("governance.") || value.startsWith("control.")) return "治理操作";
  if (value.startsWith("memory.")) return "记忆更新";
  if (value.startsWith("relationship.")) return "关系更新";
  return "运行事件";
}

export function fieldLabel(field) {
  return FIELD_LABELS[field] || "详情";
}

export function valueLabel(field, value) {
  if (field === "paused" && typeof value === "boolean") return value ? "已暂停" : "运行中";
  if (value === true) return "是";
  if (value === false) return "否";
  if (value === null || value === undefined || value === "") return "—";
  if (Array.isArray(value)) return value.map((item) => valueLabel(field, item)).join("、");
  if (typeof value === "object") {
    return Object.entries(value)
      .map(([key, item]) => `${fieldLabel(key)}：${valueLabel(key, item)}`)
      .join("；");
  }
  const text = String(value);
  return VALUE_LABELS[text] || text;
}

export function participationLaneLabel(value) {
  const normalized = String(value || "").toUpperCase();
  return PARTICIPATION_LANE_LABELS[normalized] || "策略通道未记录";
}

export function cognitionStateLabel(value) {
  const normalized = String(value || "").toUpperCase();
  return COGNITION_STATE_LABELS[normalized] || String(value || "状态未记录");
}

export function cognitionWorkerLabel(value) {
  return COGNITION_WORKER_LABELS[String(value || "")] || "其他认知模块";
}

export function cognitionBackendLabel(diagnostic = {}) {
  if (String(diagnostic.backend || "") !== "direct_deepseek") return "";
  const model = String(diagnostic.model || "").trim();
  return model ? `直连 DeepSeek · ${model}` : "直连 DeepSeek";
}

export function cognitionDiagnosticStatusLabel(value) {
  const normalized = String(value || "").toUpperCase();
  return COGNITION_DIAGNOSTIC_STATUS_LABELS[normalized] || "状态未知";
}

export function cognitionDiagnosticExplanation(diagnostic = {}) {
  const code = String(diagnostic.diagnostic_code || "");
  if (code === "direct_timeout") {
    return "直连模型在 6 秒内未返回，本次已转为保守观察。";
  }
  if (code === "direct_auth_failed") {
    return "认知模型鉴权失败，请管理员检查 API Key。";
  }
  if (code === "direct_rate_limited") {
    return "认知模型触发限流，本次已转为保守观察。";
  }
  if (code === "direct_network_failed") {
    return "无法连接认知模型服务，本次已转为保守观察。";
  }
  if (code === "direct_upstream_failed") {
    return "认知模型服务暂时异常，本次已转为保守观察。";
  }
  if (code === "direct_invalid_output") {
    return "认知模型返回内容未通过本地校验，本次未采用。";
  }
  if (DIRECT_OUTPUT_EXPLANATIONS[code]) {
    return DIRECT_OUTPUT_EXPLANATIONS[code];
  }
  if (code === "worker_timeout") {
    const hasProviderMetric = diagnostic.provider_latency_ms !== undefined
      && diagnostic.provider_latency_ms !== null;
    const queueWait = Math.max(0, Number(diagnostic.queue_wait_ms) || 0);
    const providerWait = Math.max(0, Number(diagnostic.provider_latency_ms) || 0);
    const milliseconds = Math.max(
      0,
      Number(diagnostic.timeout_ms)
        || providerWait
        || Number(diagnostic.latency_ms)
        || 0,
    );
    const seconds = Math.max(1, Math.round(milliseconds / 1_000));
    if (hasProviderMetric && providerWait <= 0 && queueWait > 0) {
      return `等待认知执行名额约 ${seconds} 秒仍未开始，本次已转为保守观察。`;
    }
    if (hasProviderMetric && providerWait <= 0) {
      return "本次认知未能在截止前开始，已转为保守观察。";
    }
    return `模型等待约 ${seconds} 秒仍未返回，本次已转为保守观察。`;
  }
  if (code.startsWith("model_call_failed")) {
    const category = code.split(":", 2)[1];
    return category
      ? `模型 Provider 调用失败（${category}），本次已转为保守观察。`
      : "模型 Provider 调用失败，本次已转为保守观察。";
  }
  if (code === "invalid_worker_output") return "模型返回内容不符合结构要求，本次未采用。";
  if (code === "cognition_budget_exhausted") return "本轮认知调用预算已用尽，本次未继续调用模型。";
  if (String(diagnostic.status || "").toUpperCase() === "SUCCEEDED") return "已按预期完成。";
  return "该认知模块没有产生可用结果，本次按安全规则处理。";
}

export function cognitionDiagnosticMetricRows(diagnostic = {}) {
  const rows = [];
  if (Number(diagnostic.queue_wait_ms) > 0) {
    rows.push(["排队等待", formatTraceDuration(diagnostic.queue_wait_ms)]);
  }
  if (Number(diagnostic.provider_latency_ms) > 0) {
    rows.push([
      String(diagnostic.backend || "") === "direct_deepseek" ? "模型请求" : "Provider 等待",
      formatTraceDuration(diagnostic.provider_latency_ms),
    ]);
  }
  if (Number(diagnostic.input_bytes) > 0) {
    rows.push(["输入大小", formatBytes(diagnostic.input_bytes)]);
  }
  if (Number(diagnostic.timeout_ms) > 0) {
    rows.push(["本次截止", formatTraceDuration(diagnostic.timeout_ms)]);
  }
  return rows;
}

export function participationDiagnosticLabel(value) {
  return PARTICIPATION_DIAGNOSTIC_LABELS[String(value || "")] || "按当前参与策略完成判断";
}

export function traceWouldReply(summary = {}) {
  const decision = summary.decision || summary;
  if (typeof decision.would_reply === "boolean") return decision.would_reply;
  return String(decision.outcome || decision.pre_gate_outcome || "").toUpperCase() === "ACT";
}

export function traceIsObserved(summary = {}) {
  const decision = summary.decision || {};
  const outcome = String(decision.outcome || decision.pre_gate_outcome || "").toUpperCase();
  if (outcome) return ["OBSERVE", "SILENCE"].includes(outcome);
  if (typeof decision.would_reply === "boolean") return !decision.would_reply;
  return ["OBSERVED", "SILENT"].includes(String(summary.delivery?.status || "").toUpperCase());
}

export function replyExpectation(decision = {}, delivery = {}) {
  const outcome = String(decision.outcome || decision.pre_gate_outcome || "").toUpperCase();
  if (outcome === "PENDING") return "尚未完成判断";
  const labels = {
    ACT: "正式运行会回复",
    OBSERVE: "本轮暂不参与",
    SILENCE: "本轮不回复",
    DEFER: "稍后重新判断",
  };
  if (labels[outcome]) return labels[outcome];
  if (typeof decision.would_reply === "boolean") {
    return decision.would_reply ? "正式运行会回复" : "本轮不回复";
  }
  if (!outcome) return "尚未完成判断";
  return "已完成判断";
}

export function traceResultHeadline(summary = {}) {
  if (String(summary.route?.owner || "").toUpperCase() === "EXTERNAL_PLUGIN") {
    return "由外部能力处理";
  }
  const decision = summary.decision || {};
  const outcome = String(
    decision.outcome || decision.pre_gate_outcome || "",
  ).toUpperCase();
  if (!outcome || outcome === "PENDING") return "等待完成判断";
  if (String(summary.judgement?.status || "") === "unavailable") {
    return "判断未完成";
  }
  return ({
    ACT: "正式运行会回复",
    OBSERVE: "本轮暂不参与",
    SILENCE: "本轮不回复",
    DEFER: "稍后重新判断",
  })[outcome] || replyExpectation(decision, summary.delivery);
}

export function traceResultState(summary = {}) {
  if (String(summary.route?.owner || "").toUpperCase() === "EXTERNAL_PLUGIN") {
    return summary.route?.label || "交给外部能力";
  }
  const decision = summary.decision || {};
  const outcome = String(
    decision.outcome || decision.pre_gate_outcome || "",
  ).toUpperCase();
  if (!outcome || outcome === "PENDING") return "等待判断";
  return summary.judgement?.label || decision.label || ({
    ACT: "准备回复",
    OBSERVE: "继续观察",
    SILENCE: "保持沉默",
    DEFER: "稍后再判断",
  })[outcome] || "已完成判断";
}

export function traceResultReason(summary = {}) {
  if (String(summary.route?.owner || "").toUpperCase() === "EXTERNAL_PLUGIN") {
    return String(summary.route?.reason || "由已匹配的外部插件继续处理。");
  }
  const judgement = summary.judgement || {};
  if (String(judgement.status || "") === "accepted" && judgement.reason) {
    return String(judgement.reason);
  }
  if (String(judgement.status || "") === "unavailable") {
    const diagnostics = Array.isArray(summary.understanding?.diagnostics)
      ? summary.understanding.diagnostics
      : [];
    const failure = diagnostics.find(
      (item) => String(item?.status || "").toUpperCase() !== "SUCCEEDED",
    );
    return failure
      ? cognitionDiagnosticExplanation(failure)
      : "模型判断未产生可用结果，本次按安全规则处理。";
  }
  const reasons = Array.isArray(summary.decision?.reasons)
    ? summary.decision.reasons.filter(Boolean)
    : [];
  if (reasons.length) return reasons.join("；");
  const outcome = String(summary.decision?.outcome || "").toUpperCase();
  if (!outcome || outcome === "PENDING") return "消息仍在处理中。";
  return String(summary.route?.reason || "已按当前策略完成判断。");
}

export function strategySummary(summary = {}) {
  if (String(summary.route?.owner || "").toUpperCase() === "EXTERNAL_PLUGIN") {
    return "Groupmate 不参与判断";
  }
  const decision = summary.decision || {};
  const outcome = String(decision.outcome || decision.pre_gate_outcome || "").toUpperCase();
  if (!outcome || outcome === "PENDING") return "等待进入策略判断";
  return `${participationLaneLabel(decision.participation_lane)} · ${replyExpectation(decision, summary.delivery)}`;
}

export function candidateSummary(understanding = {}) {
  const count = Math.max(0, Number(understanding.candidate_count) || 0);
  const source = String(understanding.candidate_source || "").toLowerCase();
  if (!count || source === "none") return "未生成参与方案";
  const sourceLabel = source === "deterministic"
    ? "策略生成"
    : source === "model"
      ? "模型生成"
      : "来源未记录";
  return `${sourceLabel} · ${count} 个参与方案`;
}

export function traceHasCognitionFailure(summary = {}) {
  const understanding = summary.understanding || {};
  if (["DEGRADED", "FAILED"].includes(String(understanding.status || "").toUpperCase())) return true;
  return (Array.isArray(understanding.diagnostics) ? understanding.diagnostics : [])
    .some((diagnostic) => String(diagnostic?.status || "").toUpperCase() !== "SUCCEEDED");
}

export function visibleFacts(summary = {}) {
  return Object.entries(summary)
    .filter(([key, value]) => !HIDDEN_FIELDS.has(key) && FIELD_LABELS[key] && value !== undefined)
    .map(([key, value]) => ({
      key,
      label: fieldLabel(key),
      value: valueLabel(key, value),
    }));
}

export function itemTone(item = {}) {
  const summary = item.summary || {};
  const values = [summary.status, summary.outcome, summary.task_status, summary.result_status]
    .map((value) => String(value || "").toLowerCase());
  if (values.some((value) => ["failed", "unknown", "denied", "error"].includes(value))) return "danger";
  if (summary.paused === true || values.some((value) => ["pending", "deferred", "running"].includes(value))) return "warning";
  if (values.some((value) => ["succeeded", "sent", "active", "allowed"].includes(value))) return "ok";
  return "neutral";
}

export function formatTimestamp(value) {
  const timestamp = Number(value || 0);
  if (!timestamp) return "时间未记录";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(timestamp * 1000));
}

export function formatTraceDuration(value) {
  const milliseconds = Math.max(0, Number(value) || 0);
  if (milliseconds < 1_000) return "不足 1 秒";
  const totalSeconds = Math.round(milliseconds / 1_000);
  if (totalSeconds < 60) return `${totalSeconds} 秒`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return seconds ? `${minutes} 分 ${seconds} 秒` : `${minutes} 分钟`;
}

export function messageSummary(message = {}) {
  const explicit = String(message?.summary || "").trim();
  if (explicit && explicit !== "[非文本消息]") return explicit;
  const labels = (Array.isArray(message?.parts) ? message.parts : [])
    .map((part) => String(part?.kind === "text" ? part?.text : part?.label || "").trim())
    .filter(Boolean);
  return labels.join(" · ") || "[非文本消息]";
}

export function formatBytes(value) {
  if (value === null || value === undefined || value === "") return "";
  const bytes = Number(value);
  if (!Number.isFinite(bytes) || bytes < 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${Math.round(bytes / 1024)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(bytes < 10 * 1024 ** 2 ? 1 : 0)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
}
