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
