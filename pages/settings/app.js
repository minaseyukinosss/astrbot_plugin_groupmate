const MODULE = {
  overview: ["总览", "她此刻如何在群里生活。"],
  attention: ["参与", "判断何时接话，何时把空间留给群友。"],
  members: ["成员", "确认每个人是谁，并管理昵称轨迹与实际称呼。"],
  relationships: ["关系", "查看和修正相处形成的关系。"],
  memory: ["记忆", "治理她实际保留的事实与共同经历。"],
  capabilities: ["能力", "查看她能采取的行动及其边界。"],
  self: ["自我", "维护稳定的相处方式和身份表达边界。"],
  decision: ["决策", "追溯一次开口或沉默是怎样发生的。"],
  quality: ["质量", "检查近期行为是否符合群聊伙伴目标。"],
  governance: ["审计", "追踪、解释并安全回滚每次治理操作。"],
};

const STAGE_LABEL = {
  OBSERVE: "触发",
  SCENE: "场景",
  ADDRESSEE: "归属",
  TASK_RESOLUTION: "任务",
  PARTICIPATION: "参与",
  INTENT: "意图",
  ACT: "行动",
  GATE: "门控",
  RECALL: "回忆",
  CAPABILITY: "能力",
  PLAN: "表达",
  FALLBACK: "降级",
  SPEAK: "开口",
  GUARD: "护栏",
  COMPOSE: "组装",
  SCHEDULE: "调度",
  SEND: "发送",
  MEMORY: "记忆",
  RELATIONSHIP: "关系",
  CONTINUITY: "后续",
  COMMITMENT: "承诺",
  END: "结束",
};

const TERM = {
  ignore: "忽略",
  command: "宿主命令",
  native_direct: "平台直唤",
  alias_direct: "称呼直唤",
  copied_at: "复制的 @",
  continuation: "续聊",
  alias_mention: "称呼提及",
  candidate: "候选观察",
  host_interaction: "宿主互动",
  direct_address: "直接点名",
  reply_to_bot: "回复爱弥斯",
  active_continuation: "主动续聊",
  social_response: "社交回应",
  ambient_contribution: "氛围参与",
  task_request: "任务请求",
  direct_interaction: "直接互动",
  acknowledge: "应一声",
  answer: "作答",
  clarify: "澄清",
  reciprocate: "回礼",
  playful_reply: "玩笑回应",
  boundary: "边界回应",
  task_handoff: "任务移交",
  task_unsupported: "任务不支持",
  visual_reaction: "看图反应",
  short_social: "短社交",
  help_detail: "帮忙细说",
  task_result: "任务结果",
  direct_required: "必须回应",
  open_optional: "可选开口",
  none: "无参与义务",
  sent: "已开口",
  participation_speak: "决定开口",
  participation_silence: "选择沉默",
  decision_ignore: "判断后忽略",
  model_silence: "生成后沉默",
  empty_topic: "没有有效话题",
  empty_delivery: "没有可发内容",
  bypassed_trigger: "触发已旁路",
  copied_at_bypassed: "复制 @ 旁路",
  open_send_budget_exhausted: "开放参与额度用尽",
  generation_budget_exhausted: "生成额度用尽",
  generation_error: "生成失败",
  repair_error: "修复失败",
  repair_silence: "修复后仍沉默",
  poke_direct: "被直接戳一戳",
  poke_bystander: "旁观跟戳",
  poke_bystander_hostile: "旁观敌意跟戳",
  poke_boundary_silence: "戳一戳边界沉默",
  poke_spam: "高频戳一戳",
  "inhibit:ambiguous_target": "目标不明，保持沉默",
  "inhibit:passing_alias_mention": "只是路过提及",
  "inhibit:owned_by_other_user": "话题属于他人",
  "inhibit:empty_echo": "避免空复读",
  "inhibit:avoid_monopoly": "避免连续抢话",
  "motive:help_when_concrete": "出现具体求助",
  no_open_motive: "没有自然开口动机",
  no_personal_memory: "没有相关个人记忆",
  supported: "已支持",
  unsupported: "不支持",
  unknown: "未知",
  accepted: "通过",
  schedule_failed: "调度失败",
  vision: "看图",
  vision_disabled: "看图关闭",
  profile: "个人事实",
  episodic: "共同经历",
  GROUP: "群共同记忆",
  USER_IN_GROUP: "群内个人记忆",
  SELF: "自我记忆",
  read_only: "只读",
  normal: "常规",
  dangerous: "高风险",
  capability: "内建能力",
  llm_tool: "平台工具",
  builtin: "内置能力",
  member: "群成员",
  admin: "管理员",
  relationship_corrected: "修正关系",
  memory_deleted: "删除记忆",
  governance_reverted: "回滚操作",
  relationship_evidence_rejected: "否定关系证据",
  relationship_evidence_reviewed: "复核关系证据",
  member_address_corrected: "修正成员称呼",
  member_identity_linked: "关联成员身份",
  continuity_status_corrected: "修正未完事项",
  self_commitment_status_corrected: "修正自我承诺",
  plan: "计划",
  promise: "承诺",
  follow_up: "后续事项",
  open: "进行中",
  completed: "已完成",
  cancelled: "已取消",
  deleted: "已遗忘",
  THANKS: "感谢",
  PRAISE: "认可",
  HELP_REQUEST: "求助",
  HELPED: "提供帮助",
  FRIENDLY_TEASE: "友好玩笑",
  CORRECTION: "纠正",
  BOUNDARY_PUSH: "推动边界",
  HARASSMENT: "持续越界",
  APOLOGY: "道歉",
  context_verified: "上下文已验证",
  correct: "判断正确",
  wrong_person: "认错人",
  wrong_kind: "类型判断错",
  insufficient_context: "上下文不足",
  other_error: "其他误判",
  pending: "待复核",
  in_progress: "进行中",
  blocked: "受阻",
  withdrawn: "已撤回",
  rejected: "已否定",
  reply_chain: "回复链",
  platform_mention: "平台 @",
  leading_address: "句首称呼",
  participant_alias: "正文称呼",
  adjacent_qa: "紧邻问答",
  latest_speaker: "最新发言人",
  interaction_partner: "当前互动者",
  hard_trigger_sender: "直接呼叫者",
  reply_to_bot_audience_sender: "回复爱弥斯的人",
  ambiguous_group_reply: "对象不明确",
  recount_unconfirmed: "未经确认的转述",
  memory_unresolved: "记忆对象不明确",
  capability_not_executed: "所需能力没有实际执行",
  capability_failed: "所需能力执行失败",
  source_message_unavailable: "原始消息不可用",
  source_information_missing: "原始内容不足",
  waiting_for_new_information: "还在等待新的事实",
  group_not_enabled: "该群未启用",
  group_busy_deferred: "群聊正忙，已延后",
  quiet_hours_deferred: "安静时段，已延后",
  platform_failed: "平台发送失败",
  send_failed: "消息发送失败",
};

const els = Object.fromEntries(
  [
    "badge", "error", "toast", "refresh", "pause-toggle", "global-group-filter",
    "page-name", "page-description", "brand-name", "overview-updated", "presence-dot",
    "presence-title", "presence-detail", "metric-groups", "metric-memories",
    "metric-relationships", "metric-capabilities", "overview-groups", "health-list",
    "overview-decisions", "participation-total", "participation-sent", "participation-direct",
    "participation-silent", "attention-groups", "attention-reasons", "relationship-count",
    "relationship-evidence-summary", "relationship-learning-title",
    "relationship-learning-detail", "relationship-learning-badge",
    "relationship-body", "relationship-empty", "relationship-editor", "relationship-name",
    "relationship-context", "relationship-form", "relationship-confirm", "relationship-confirm-copy",
    "relationship-confirm-submit", "relationship-evidence-count", "relationship-evidence",
    "evidence-confirm", "evidence-confirm-copy", "evidence-reject-reason", "evidence-confirm-submit",
    "evidence-review", "evidence-review-copy", "evidence-review-outcome",
    "evidence-review-reason", "evidence-review-submit",
    "member-summary", "member-count", "member-renamed-count", "member-address-count",
    "member-linked-count", "member-body", "member-empty", "member-editor", "member-name",
    "member-context", "member-history-count", "member-history", "member-form",
    "member-continuity-count", "member-continuity",
    "member-link-target", "member-link-reason", "member-link-button", "member-link-confirm",
    "member-link-confirm-copy", "member-link-confirm-submit",
    "continuity-confirm", "continuity-confirm-copy", "continuity-reason", "continuity-confirm-submit",
    "memory-count", "memory-body", "memory-empty",
    "memory-confirm", "memory-confirm-copy", "memory-delete-reason", "memory-confirm-submit",
    "capability-count", "capability-body", "capability-empty", "identity-name",
    "identity-aliases", "identity-principles", "retention-status", "filter-outcome",
    "self-commitment-count", "self-commitment-list", "self-commitment-confirm",
    "self-commitment-scheduler",
    "self-commitment-confirm-copy", "self-commitment-reason", "self-commitment-confirm-submit",
    "decision-count", "decision-list", "trace-meta", "path-summary", "context-list",
    "target-summary", "target-reply", "target-reply-source", "target-social",
    "target-social-source", "target-memory", "target-memory-source",
    "trace-list", "quality-rate", "quality-sent", "quality-silent", "quality-health",
    "quality-reasons", "quality-learning-threshold", "quality-learning-groups",
    "governance-count", "governance-body", "governance-empty", "governance-confirm",
    "governance-confirm-copy", "governance-revert-reason", "governance-confirm-submit",
  ].map((id) => [id.replaceAll("-", "_"), document.getElementById(id)])
);

const state = {
  module: "overview",
  cognition: null,
  decisions: [],
  selectedDecision: "",
  selectedRelationship: null,
  selectedMember: null,
  pendingRelationship: null,
  pendingEvidence: null,
  pendingMemory: null,
  pendingGovernance: null,
  pendingMemberLink: null,
  pendingContinuity: null,
  pendingSelfCommitment: null,
  group: "",
  paused: false,
  toastTimer: null,
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function formatTime(timestamp, withSeconds = false) {
  if (!timestamp) return "—";
  const date = new Date(Number(timestamp) * 1000);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    ...(withSeconds ? { second: "2-digit" } : {}),
  });
}

function shortId(value, length = 8) {
  const text = String(value || "");
  return text.length > length ? `${text.slice(0, length)}…` : text;
}

function translateToken(value) {
  const raw = String(value ?? "").trim();
  if (!raw) return "—";
  if (TERM[raw]) return TERM[raw];
  const lower = raw.toLowerCase();
  if (TERM[lower]) return TERM[lower];
  if (raw.startsWith("scene:")) return `场景：${translateToken(raw.slice(6))}`;
  if (raw.startsWith("reply:")) return `回应：${translateToken(raw.slice(6))}`;
  return raw.replaceAll("_", " ");
}

function describeValue(value) {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (typeof value === "number") return String(value);
  if (typeof value === "object") {
    return Object.entries(value)
      .map(([key, item]) => `${translateToken(key)}：${describeValue(item)}`)
      .join("；");
  }
  const text = String(value);
  if (text.includes("|") || text.includes(",")) {
    return text.split(/[|,]/).map((item) => translateToken(item)).join(" · ");
  }
  return translateToken(text);
}

function showError(message) {
  els.error.hidden = !message;
  els.error.textContent = message || "";
}

function showToast(message) {
  clearTimeout(state.toastTimer);
  els.toast.textContent = message;
  els.toast.hidden = false;
  state.toastTimer = setTimeout(() => { els.toast.hidden = true; }, 2600);
}

function setBusy(button, busy, busyText = "处理中") {
  if (!button) return;
  if (busy) button.dataset.label = button.textContent;
  button.disabled = busy;
  button.textContent = busy ? busyText : (button.dataset.label || button.textContent);
}

function apiError(error) {
  return error?.message || String(error || "未知错误");
}

async function apiGet(path, params = {}) {
  const bridge = window.AstrBotPluginPage;
  if (!bridge?.apiGet) throw new Error("请从 AstrBot 的 Groupmate 插件页面打开此治理台。");
  return bridge.apiGet(path, params);
}

async function apiPost(path, payload) {
  const bridge = window.AstrBotPluginPage;
  if (!bridge?.apiPost) throw new Error("请从 AstrBot 的 Groupmate 插件页面打开此治理台。");
  return bridge.apiPost(path, payload);
}

function switchModule(name) {
  if (!MODULE[name]) return;
  state.module = name;
  document.querySelectorAll("[data-module]").forEach((item) => {
    item.classList.toggle("is-active", item.dataset.module === name);
  });
  document.querySelectorAll("[data-module-panel]").forEach((panel) => {
    const active = panel.dataset.modulePanel === name;
    panel.hidden = !active;
    panel.classList.toggle("is-active", active);
  });
  els.page_name.textContent = MODULE[name][0];
  els.page_description.textContent = MODULE[name][1];
  if (name === "decision" && !state.selectedDecision && state.decisions.length) {
    selectDecision(state.decisions[0].decision_id);
  }
}

function currentGroups() {
  const runtimeGroups = state.cognition?.runtime?.groups || {};
  return Object.entries(runtimeGroups).filter(([groupId]) => !state.group || groupId === state.group);
}

function currentRelationships() {
  return (state.cognition?.relationships || []).filter((item) => !state.group || item.group_id === state.group);
}

function currentMembers() {
  const items = state.cognition?.members || [];
  return state.group ? items.filter((item) => item.group_id === state.group) : items;
}

function currentContinuity() {
  return (state.cognition?.continuity || []).filter((item) => (
    item.status !== "deleted" && (!state.group || item.group_id === state.group)
  ));
}

function memberContinuity(item) {
  return currentContinuity().filter((entry) => (
    entry.group_id === item.group_id && entry.subject_id === (item.canonical_subject_id || item.subject_id)
  ));
}

function currentRelationshipEvidence() {
  return (state.cognition?.relationship_evidence || []).filter((item) => !state.group || item.group_id === state.group);
}

function currentMemories() {
  return (state.cognition?.memories || []).filter((item) => !state.group || item.group_id === state.group);
}

function currentSelfCommitments() {
  return (state.cognition?.self_commitments || []).filter((item) => (
    item.status !== "deleted" && (!state.group || item.group_id === state.group)
  ));
}

function commitmentStatusLabel(status) {
  return ({
    pending: "待处理",
    in_progress: "进行中",
    completed: "已完成",
    blocked: "受阻",
    withdrawn: "已撤回",
    deleted: "已遗忘",
  })[status] || translateToken(status);
}

function currentDecisions() {
  return state.decisions.filter((item) => !state.group || item.group_id === state.group);
}

function currentGovernance() {
  return (state.cognition?.governance || []).filter((item) => !state.group || item.group_id === state.group);
}

function fillGroupFilter() {
  const select = els.global_group_filter;
  const groups = state.cognition?.groups || [];
  const current = state.group;
  select.innerHTML = '<option value="">全部群</option>' + groups.map((group) => (
    `<option value="${escapeHtml(group)}">群 ${escapeHtml(shortId(group, 12))}</option>`
  )).join("");
  select.value = groups.includes(current) ? current : "";
  state.group = select.value;
}

function runtimeGroupRow(groupId, item) {
  const busy = Boolean(item.pending || item.in_flight || item.pending_hard);
  const status = busy ? "正在判断" : item.dispatch_enabled ? "在场" : "观察中";
  return `<div class="data-row">
    <div><span class="row-label">群</span><strong class="row-value">${escapeHtml(shortId(groupId, 14))}</strong></div>
    <div><span class="row-label">现场消息</span><div class="row-value">${Number(item.messages || 0)}</div></div>
    <div><span class="row-label">当前状态</span><div class="row-value">${status}</div></div>
    <div><span class="row-label">最近触发</span><div class="row-value">${escapeHtml(translateToken(item.last_trigger))}</div></div>
  </div>`;
}

function healthItems() {
  const runtime = state.cognition?.runtime || {};
  const items = [];
  if (runtime.paused) items.push(["danger", "爱弥斯已暂停，只观察群聊，不会主动处理新判断。"]);
  else items.push(["ok", "运行中，群消息会进入参与判断。"]);
  if (runtime.config_health === "ok") items.push(["ok", "部署配置未发现兼容性问题。"]);
  else items.push(["warning", `部署配置存在提醒：${(runtime.warnings || []).join("；") || "请检查插件齿轮配置。"}`]);
  const incompatible = (state.cognition?.capabilities || []).filter((item) => !item.compatible).length;
  if (incompatible) items.push(["warning", `${incompatible} 项能力当前不可用。`]);
  if (state.cognition?.privacy?.raw_message_retention === "not_configured") {
    items.push(["warning", "原始消息尚未设置自动清理周期；已接受记忆可单独删除。"]);
  }
  return items;
}

function renderHealth(target) {
  target.innerHTML = healthItems().map(([tone, text]) => (
    `<div class="health-row"><span class="health-indicator" data-tone="${tone}"></span><span>${escapeHtml(text)}</span></div>`
  )).join("");
}

function renderReasonList(target, reasons) {
  const entries = Object.entries(reasons || {});
  const max = Math.max(1, ...entries.map(([, count]) => Number(count || 0)));
  target.innerHTML = entries.length ? entries.map(([reason, count]) => (
    `<div class="reason-row"><span>${escapeHtml(translateToken(reason))}</span><div class="reason-track"><div class="reason-fill" style="width:${Math.max(3, Number(count) / max * 100)}%"></div></div><span class="reason-count">${Number(count)}</span></div>`
  )).join("") : '<div class="empty-state">还没有足够的判断记录。</div>';
}

function renderOverview() {
  const data = state.cognition || {};
  const runtime = data.runtime || {};
  const groups = currentGroups();
  const paused = Boolean(runtime.paused);
  els.overview_updated.textContent = `更新于 ${new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}`;
  els.presence_dot.dataset.tone = paused ? "warning" : "ok";
  els.presence_title.textContent = paused ? "暂时不参与群聊" : "正在群里生活";
  els.presence_detail.textContent = paused ? "仍会保留现场，但不会发起新的回应" : "观察消息、判断关系，并只在合适时开口";
  els.metric_groups.textContent = String(groups.length || (state.group ? 0 : (data.groups || []).length));
  els.metric_memories.textContent = String(currentMemories().length);
  els.metric_relationships.textContent = String(currentRelationships().length);
  els.metric_capabilities.textContent = String((data.capabilities || []).filter((item) => item.compatible).length);
  els.overview_groups.innerHTML = groups.length ? groups.map(([id, item]) => runtimeGroupRow(id, item)).join("") : '<div class="empty-state">所选范围还没有活跃现场。</div>';
  renderHealth(els.health_list);
  const recent = currentDecisions().slice(0, 5);
  els.overview_decisions.innerHTML = recent.length ? recent.map((item) => (
    `<div class="compact-row"><span>${formatTime(item.timestamp)}</span><div><strong>${escapeHtml(item.sent ? "选择开口" : "选择沉默")}</strong><span>${escapeHtml(translateToken(item.end_reason || item.participation || item.trigger))}</span></div><span class="tag" data-tone="${item.sent ? "ok" : "neutral"}">${item.sent ? "已开口" : "沉默"}</span></div>`
  )).join("") : '<div class="empty-state">还没有判断记录。</div>';
}

function renderAttention() {
  const quality = filteredQuality();
  els.participation_total.textContent = String(quality.decision_count || 0);
  els.participation_sent.textContent = String(quality.sent_count || 0);
  els.participation_direct.textContent = String(quality.direct_count || 0);
  els.participation_silent.textContent = String(quality.silent_count || 0);
  const groups = currentGroups();
  els.attention_groups.innerHTML = groups.length ? groups.map(([id, item]) => runtimeGroupRow(id, item)).join("") : '<div class="empty-state">所选范围还没有活跃现场。</div>';
  renderReasonList(els.attention_reasons, quality.reason_counts);
}

function scoreTone(value, signed = false) {
  const number = Number(value || 0);
  if (signed && number < -20) return "danger";
  if (!signed && number > 60) return "warning";
  return number > 20 ? "ok" : "neutral";
}

function renderRelationships() {
  const items = currentRelationships();
  const evidence = currentRelationshipEvidence();
  const accepted = evidence.filter((item) => item.status === "accepted").length;
  const pending = evidence.filter((item) => item.status === "pending").length;
  const rejected = evidence.filter((item) => item.status === "rejected").length;
  els.relationship_count.textContent = `${items.length} 位`;
  els.relationship_evidence_summary.textContent = `${accepted} 条生效 · ${pending} 条待复核${rejected ? ` · ${rejected} 条误判` : ""}`;
  renderRelationshipLearningState();
  els.relationship_empty.hidden = items.length > 0;
  els.relationship_body.innerHTML = items.map((item) => {
    const key = `${encodeURIComponent(item.group_id)}::${encodeURIComponent(item.user_id)}`;
    const selectedKey = state.selectedRelationship
      ? `${encodeURIComponent(state.selectedRelationship.group_id)}::${encodeURIComponent(state.selectedRelationship.user_id)}`
      : "";
    const selected = selectedKey === key;
    return `<tr data-selectable data-relationship-key="${escapeHtml(key)}" class="${selected ? "is-selected" : ""}">
      <td><strong class="cell-primary">${escapeHtml(item.display_name)}</strong><span class="cell-secondary">${escapeHtml(item.configured_relationship || "自然形成")}</span></td>
      <td>${escapeHtml(shortId(item.group_id, 10))}</td>
      <td><span class="tag" data-tone="${scoreTone(item.familiarity)}">${item.familiarity}</span></td>
      <td><span class="tag" data-tone="${scoreTone(item.affinity, true)}">${item.affinity}</span></td>
      <td><span class="tag" data-tone="${scoreTone(item.trust, true)}">${item.trust}</span></td>
      <td><span class="tag" data-tone="${scoreTone(item.boundary_pressure)}">${item.boundary_pressure}</span></td>
      <td class="numeric">${Number(item.accepted_evidence_count ?? item.interaction_count ?? 0)}</td>
    </tr>`;
  }).join("");
  if (state.selectedRelationship) renderRelationshipEditor(state.selectedRelationship);
}

function renderMembers() {
  const items = currentMembers();
  const renamed = items.filter((item) => (item.nickname_history || []).length > 1).length;
  const addressed = items.filter((item) => item.preferred_address).length;
  const linked = items.filter((item) => item.identity_status === "linked").length;
  els.member_summary.textContent = `${items.length} 位成员 · ${renamed} 位有改名轨迹`;
  els.member_count.textContent = String(items.length);
  els.member_renamed_count.textContent = String(renamed);
  els.member_address_count.textContent = String(addressed);
  els.member_linked_count.textContent = String(linked);
  els.member_empty.hidden = items.length > 0;
  els.member_body.innerHTML = items.map((item) => {
    const key = `${encodeURIComponent(item.group_id)}::${encodeURIComponent(item.subject_id)}`;
    const selectedKey = state.selectedMember
      ? `${encodeURIComponent(state.selectedMember.group_id)}::${encodeURIComponent(state.selectedMember.subject_id)}`
      : "";
    const history = item.nickname_history || [];
    const openCount = memberContinuity(item).filter((entry) => entry.status === "open").length;
    const linkedLabel = item.identity_status === "linked" ? `已关联到 ${item.canonical_name || "正确成员"}` : "独立身份";
    return `<tr data-selectable data-member-key="${escapeHtml(key)}" class="${selectedKey === key ? "is-selected" : ""}">
      <td><strong class="cell-primary">${escapeHtml(item.address || item.display_name)}</strong><span class="cell-secondary">${item.preferred_address ? "人工称呼" : "跟随群昵称"}</span></td>
      <td>${escapeHtml(shortId(item.group_id, 10))}</td>
      <td>${escapeHtml(item.display_name)}</td>
      <td>${openCount ? `<span class="tag" data-tone="warning">${openCount} 件进行中</span>` : "暂无"}</td>
      <td>${formatTime(item.last_seen_at)}</td>
      <td><span class="tag" data-tone="${item.identity_status === "linked" ? "warning" : "ok"}">${escapeHtml(linkedLabel)}</span></td>
    </tr>`;
  }).join("");
  if (state.selectedMember) renderMemberEditor(state.selectedMember);
}

function renderMemberEditor(item) {
  els.member_editor.hidden = false;
  els.member_name.textContent = item.address || item.display_name;
  els.member_context.textContent = `群 ${shortId(item.group_id, 14)} · 首次出现 ${formatTime(item.first_seen_at)} · 最近出现 ${formatTime(item.last_seen_at)}`;
  const history = [...(item.nickname_history || [])].sort((a, b) => Number(b.last_seen_at || 0) - Number(a.last_seen_at || 0));
  renderMemberContinuity(item);
  els.member_history_count.textContent = `${history.length} 个`;
  els.member_history.innerHTML = history.length ? history.map((entry, index) => (
    `<div class="member-history-row"><div><strong>${escapeHtml(entry.name)}</strong><span>${index === 0 ? "当前或最近使用" : "历史昵称"}</span></div><time>${formatTime(entry.first_seen_at)} 至 ${formatTime(entry.last_seen_at)}</time></div>`
  )).join("") : '<div class="empty-state compact-empty">还没有可展示的昵称记录。</div>';
  els.member_form.elements.preferred_address.value = item.preferred_address || "";
  els.member_form.elements.reason.value = "";
  const candidates = currentMembers().filter((candidate) => (
    candidate.group_id === item.group_id && candidate.subject_id !== item.subject_id && candidate.identity_status !== "linked"
  ));
  els.member_link_target.innerHTML = '<option value="">选择正确成员</option>' + candidates.map((candidate) => (
    `<option value="${escapeHtml(candidate.subject_id)}">${escapeHtml(candidate.address || candidate.display_name)}</option>`
  )).join("");
  els.member_link_reason.value = "";
  els.member_link_button.disabled = item.identity_status === "linked";
  els.member_link_button.textContent = item.identity_status === "linked" ? `已关联到 ${item.canonical_name || "正确成员"}` : "检查并关联";
}

function renderMemberContinuity(item) {
  const items = memberContinuity(item).sort((a, b) => {
    if (a.status === "open" && b.status !== "open") return -1;
    if (a.status !== "open" && b.status === "open") return 1;
    return Number(b.updated_at || 0) - Number(a.updated_at || 0);
  }).slice(0, 12);
  const openCount = items.filter((entry) => entry.status === "open").length;
  els.member_continuity_count.textContent = `${openCount} 件进行中`;
  els.member_continuity.innerHTML = items.length ? items.map((entry) => {
    const active = entry.status === "open";
    const actions = active
      ? `<button class="text-button" type="button" data-continuity-status="completed" data-continuity-id="${escapeHtml(entry.item_id)}">完成</button><button class="text-button" type="button" data-continuity-status="cancelled" data-continuity-id="${escapeHtml(entry.item_id)}">取消</button><button class="text-button danger-link" type="button" data-continuity-status="deleted" data-continuity-id="${escapeHtml(entry.item_id)}">遗忘</button>`
      : `<button class="text-button" type="button" data-continuity-status="open" data-continuity-id="${escapeHtml(entry.item_id)}">重新打开</button>`;
    return `<article class="continuity-item" data-status="${escapeHtml(entry.status)}">
      <div class="continuity-head"><span class="tag" data-tone="${active ? "warning" : "neutral"}">${escapeHtml(translateToken(entry.status))}</span><span>${escapeHtml(translateToken(entry.kind))}</span><time>${formatTime(entry.updated_at)}</time></div>
      <strong>${escapeHtml(entry.summary)}</strong>
      <q>${escapeHtml(entry.source_quote || "未保留可展示原话")}</q>
      <div class="continuity-actions">${actions}</div>
    </article>`;
  }).join("") : '<div class="empty-state compact-empty">还没有明确留下的后续事项。</div>';
}

function openContinuityConfirm(itemId, status) {
  const item = (state.cognition?.continuity || []).find((entry) => entry.item_id === itemId);
  if (!item) return;
  state.pendingContinuity = { item, status };
  els.continuity_confirm_copy.textContent = `将“${item.summary}”修正为“${translateToken(status)}”。`;
  els.continuity_reason.value = "";
  els.continuity_confirm.showModal();
}

async function correctContinuity() {
  if (!state.pendingContinuity) return;
  const reason = els.continuity_reason.value.trim();
  if (!reason) {
    showError("请填写状态修正原因。");
    return;
  }
  setBusy(els.continuity_confirm_submit, true, "保存中");
  try {
    await apiPost(`continuity/${encodeURIComponent(state.pendingContinuity.item.item_id)}/status`, {
      confirm: true,
      status: state.pendingContinuity.status,
      reason,
    });
    const selected = state.selectedMember ? { ...state.selectedMember } : null;
    state.pendingContinuity = null;
    els.continuity_confirm.close();
    await loadAll({ quiet: true });
    if (selected) {
      state.selectedMember = (state.cognition?.members || []).find((item) => item.group_id === selected.group_id && item.subject_id === selected.subject_id) || null;
      renderMembers();
    }
    showToast("事项状态已更新，后续相处立即使用");
  } catch (error) {
    showError(`修正事项状态失败：${apiError(error)}`);
  } finally {
    setBusy(els.continuity_confirm_submit, false);
  }
}

function selectMember(key) {
  const [encodedGroup, encodedSubject] = key.split("::");
  const groupId = decodeURIComponent(encodedGroup || "");
  const subjectId = decodeURIComponent(encodedSubject || "");
  state.selectedMember = (state.cognition?.members || []).find((item) => item.group_id === groupId && item.subject_id === subjectId) || null;
  renderMembers();
}

async function saveMemberAddress(event) {
  event.preventDefault();
  if (!state.selectedMember) return;
  const form = new FormData(els.member_form);
  const reason = String(form.get("reason") || "").trim();
  if (!reason) {
    showError("请填写称呼修正原因。");
    return;
  }
  const subject = { group_id: state.selectedMember.group_id, subject_id: state.selectedMember.subject_id };
  setBusy(event.submitter, true, "保存中");
  try {
    await apiPost("members/correct", {
      ...subject,
      preferred_address: String(form.get("preferred_address") || "").trim(),
      reason,
      confirm: true,
    });
    await loadAll({ quiet: true });
    state.selectedMember = (state.cognition?.members || []).find((item) => item.group_id === subject.group_id && item.subject_id === subject.subject_id) || null;
    renderMembers();
    showToast("成员称呼已更新，后续相处立即使用");
  } catch (error) {
    showError(`修正成员称呼失败：${apiError(error)}`);
  } finally {
    setBusy(event.submitter, false);
  }
}

function openMemberLinkConfirm() {
  if (!state.selectedMember) return;
  const targetId = els.member_link_target.value;
  const reason = els.member_link_reason.value.trim();
  const target = currentMembers().find((item) => item.group_id === state.selectedMember.group_id && item.subject_id === targetId);
  if (!target || !reason) {
    showError("请选择正确成员并填写身份关联原因。");
    return;
  }
  state.pendingMemberLink = {
    group_id: state.selectedMember.group_id,
    source_subject_id: state.selectedMember.subject_id,
    canonical_subject_id: target.subject_id,
    reason,
    source_name: state.selectedMember.address || state.selectedMember.display_name,
    target_name: target.address || target.display_name,
  };
  els.member_link_confirm_copy.textContent = `将“${state.pendingMemberLink.source_name}”关联为“${state.pendingMemberLink.target_name}”的同一成员。`;
  els.member_link_confirm.showModal();
}

async function linkMemberIdentity() {
  if (!state.pendingMemberLink) return;
  const source = { group_id: state.pendingMemberLink.group_id, subject_id: state.pendingMemberLink.source_subject_id };
  setBusy(els.member_link_confirm_submit, true, "关联中");
  try {
    await apiPost("members/link", { ...state.pendingMemberLink, confirm: true });
    state.pendingMemberLink = null;
    els.member_link_confirm.close();
    await loadAll({ quiet: true });
    state.selectedMember = (state.cognition?.members || []).find((item) => item.group_id === source.group_id && item.subject_id === source.subject_id) || null;
    renderMembers();
    showToast("成员身份已关联，原记录仍保留在审计中");
  } catch (error) {
    showError(`关联成员身份失败：${apiError(error)}`);
  } finally {
    setBusy(els.member_link_confirm_submit, false);
  }
}

function selectedLearningGroup() {
  const groups = state.cognition?.relationship_learning?.groups || [];
  if (state.group) return groups.find((item) => item.group_id === state.group) || null;
  return groups.length === 1 ? groups[0] : null;
}

function renderRelationshipLearningState() {
  const groups = state.cognition?.relationship_learning?.groups || [];
  const selected = selectedLearningGroup();
  if (selected) {
    const rate = Math.round(Number(selected.error_rate || 0) * 100);
    els.relationship_learning_title.textContent = selected.auto_apply ? "这个群的新证据会自动生效" : "这个群仍在影子学习";
    els.relationship_learning_detail.textContent = `已复核 ${selected.reviewed_count} 条，误判率 ${rate}%。${selected.eligible ? "质量门槛已达到。" : `至少需要 ${selected.min_reviewed_samples} 条且误判率不高于 ${Math.round(selected.max_error_rate * 100)}%。`}`;
    els.relationship_learning_badge.textContent = selected.auto_apply ? "已放行" : selected.eligible ? "待齿轮放行" : "影子模式";
    els.relationship_learning_badge.dataset.tone = selected.auto_apply ? "ok" : "warning";
    return;
  }
  const auto = groups.filter((item) => item.auto_apply).length;
  const pending = groups.reduce((sum, item) => sum + Number(item.pending || 0), 0);
  els.relationship_learning_title.textContent = auto ? `${auto} 个群已自动应用关系证据` : "所有群都在影子学习";
  els.relationship_learning_detail.textContent = `当前共有 ${pending} 条待复核证据。选择具体群可查看该群的质量门槛。`;
  els.relationship_learning_badge.textContent = auto ? `${auto} 群已放行` : "未放行";
  els.relationship_learning_badge.dataset.tone = auto ? "ok" : "warning";
}

function renderRelationshipEditor(item) {
  els.relationship_editor.hidden = false;
  els.relationship_name.textContent = item.display_name;
  els.relationship_context.textContent = `群 ${shortId(item.group_id, 14)} · 最近更新 ${formatTime(item.updated_at)}`;
  renderRelationshipEvidence(item);
  ["familiarity", "affinity", "trust", "boundary_pressure"].forEach((name) => {
    els.relationship_form.elements[name].value = item[name];
  });
  els.relationship_form.elements.reason.value = "";
}

function renderRelationshipEvidence(item) {
  const events = (state.cognition?.relationship_evidence || []).filter((event) => (
    event.group_id === item.group_id && event.user_id === item.user_id
  ));
  const accepted = events.filter((event) => event.status === "accepted").length;
  const pending = events.filter((event) => event.status === "pending").length;
  els.relationship_evidence_count.textContent = `${accepted} 生效 · ${pending} 待复核`;
  els.relationship_evidence.innerHTML = events.length ? events.map((event) => {
    const rejected = event.status === "rejected";
    const pendingReview = event.status === "pending";
    const statusLabel = pendingReview
      ? "待复核"
      : rejected
        ? translateToken(event.review_code || "rejected")
        : translateToken(event.kind);
    return `<article class="evidence-item" data-status="${escapeHtml(event.status)}">
      <div class="evidence-head"><span class="tag" data-tone="${pendingReview || rejected ? "warning" : "ok"}">${escapeHtml(statusLabel)}</span><time>${formatTime(event.occurred_at, true)}</time></div>
      <q>${escapeHtml(event.evidence_text || "未保留可展示片段")}</q>
      <div class="evidence-meta"><span>${escapeHtml(translateToken(event.kind))}</span><span>可信度 ${Math.round(Number(event.confidence || 0) * 100)}%</span><span>${escapeHtml(translateToken(event.reason_code))}</span></div>
      ${pendingReview
        ? `<button class="button evidence-review-button" type="button" data-review-evidence="${escapeHtml(event.event_id)}">复核证据</button>`
        : rejected
          ? `<p class="evidence-review">复核结果：${escapeHtml(translateToken(event.review_code || "rejected"))} · ${escapeHtml(event.review_reason || "管理员否定")}</p>`
          : `<button class="text-button evidence-reject" type="button" data-reject-evidence="${escapeHtml(event.event_id)}">否定已生效证据</button>`}
    </article>`;
  }).join("") : '<div class="empty-state compact-empty">还没有关系证据。</div>';
}

function selectRelationship(key) {
  const [encodedGroup, encodedUser] = key.split("::");
  const groupId = decodeURIComponent(encodedGroup || "");
  const userId = decodeURIComponent(encodedUser || "");
  state.selectedRelationship = (state.cognition?.relationships || []).find((item) => item.group_id === groupId && item.user_id === userId) || null;
  renderRelationships();
}

function renderMemories() {
  const items = currentMemories();
  els.memory_count.textContent = `${items.length} 条`;
  els.memory_empty.hidden = items.length > 0;
  els.memory_body.innerHTML = items.map((item) => (
    `<tr>
      <td><strong class="cell-primary">${escapeHtml(item.text)}</strong><span class="cell-secondary">${escapeHtml(translateToken(item.scope))} · 可信度 ${Math.round(Number(item.confidence || 0) * 100)}%</span></td>
      <td>${escapeHtml(item.subject_name)}</td>
      <td>${escapeHtml(shortId(item.group_id, 10))}</td>
      <td>${escapeHtml(translateToken(item.kind))}</td>
      <td>${formatTime(item.created_at)}</td>
      <td><button class="text-button" type="button" data-delete-memory="${escapeHtml(item.memory_id)}">删除</button></td>
    </tr>`
  )).join("");
}

function renderCapabilities() {
  const items = state.cognition?.capabilities || [];
  els.capability_count.textContent = `${items.length} 项`;
  els.capability_empty.hidden = items.length > 0;
  els.capability_body.innerHTML = items.map((item) => (
    `<tr>
      <td><strong class="cell-primary">${escapeHtml(item.name)}</strong><span class="cell-secondary">${escapeHtml(shortId(item.tool_id, 30))}</span></td>
      <td>${escapeHtml(item.description || "—")}</td>
      <td>${escapeHtml(item.source === "command" ? "插件命令" : translateToken(item.source))}</td>
      <td><span class="tag" data-tone="${item.risk === "dangerous" ? "danger" : item.risk === "unknown" ? "warning" : "neutral"}">${escapeHtml(translateToken(item.risk))}</span></td>
      <td>${escapeHtml(translateToken(item.permission))}</td>
      <td><span class="tag" data-tone="${item.compatible ? "ok" : "danger"}">${item.compatible ? "可用" : escapeHtml(item.compatibility_reason || "不可用")}</span></td>
    </tr>`
  )).join("");
}

function renderSelf() {
  const identity = state.cognition?.identity || {};
  const name = identity.display_name || "爱弥斯";
  els.brand_name.textContent = name;
  els.identity_name.textContent = name;
  els.identity_aliases.textContent = (identity.aliases || []).join("、") || "爱弥斯";
  els.identity_principles.innerHTML = (identity.principles || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  els.retention_status.textContent = state.cognition?.privacy?.raw_message_retention === "not_configured" ? "尚未配置自动清理周期" : "已配置";
  renderSelfCommitments();
}

function selfCommitmentActions(item) {
  if (item.status === "pending") {
    return `<button class="text-button" type="button" data-run-self-commitment="${escapeHtml(item.commitment_id)}">现在处理</button><button class="text-button" type="button" data-self-commitment-status="in_progress" data-self-commitment-id="${escapeHtml(item.commitment_id)}">开始处理</button><button class="text-button" type="button" data-self-commitment-status="completed" data-self-commitment-id="${escapeHtml(item.commitment_id)}">确认完成</button><button class="text-button" type="button" data-self-commitment-status="blocked" data-self-commitment-id="${escapeHtml(item.commitment_id)}">标记受阻</button><button class="text-button danger-link" type="button" data-self-commitment-status="withdrawn" data-self-commitment-id="${escapeHtml(item.commitment_id)}">撤回</button>`;
  }
  if (item.status === "in_progress" || item.status === "blocked") {
    return `<button class="text-button" type="button" data-self-commitment-status="completed" data-self-commitment-id="${escapeHtml(item.commitment_id)}">确认完成</button><button class="text-button" type="button" data-self-commitment-status="pending" data-self-commitment-id="${escapeHtml(item.commitment_id)}">回到待处理</button><button class="text-button danger-link" type="button" data-self-commitment-status="withdrawn" data-self-commitment-id="${escapeHtml(item.commitment_id)}">撤回</button>`;
  }
  return `<button class="text-button" type="button" data-self-commitment-status="pending" data-self-commitment-id="${escapeHtml(item.commitment_id)}">重新打开</button><button class="text-button danger-link" type="button" data-self-commitment-status="deleted" data-self-commitment-id="${escapeHtml(item.commitment_id)}">遗忘</button>`;
}

function renderSelfCommitments() {
  const items = currentSelfCommitments().sort((a, b) => {
    const activeA = ["pending", "in_progress", "blocked"].includes(a.status);
    const activeB = ["pending", "in_progress", "blocked"].includes(b.status);
    if (activeA !== activeB) return activeA ? -1 : 1;
    return Number(b.updated_at || 0) - Number(a.updated_at || 0);
  });
  const openCount = items.filter((item) => ["pending", "in_progress", "blocked"].includes(item.status)).length;
  const schedulerMode = state.cognition?.runtime?.commitment_scheduler || "stopped";
  els.self_commitment_scheduler.textContent = schedulerMode === "astrbot_cron" ? "AstrBot 定时唤醒" : schedulerMode === "compatibility_loop" ? "兼容调度" : "调度未运行";
  els.self_commitment_scheduler.dataset.tone = schedulerMode === "astrbot_cron" ? "ok" : schedulerMode === "compatibility_loop" ? "warning" : "danger";
  els.self_commitment_count.textContent = `${openCount} 件未结束`;
  els.self_commitment_list.innerHTML = items.length ? items.map((item) => {
    const tone = item.status === "completed" ? "ok" : item.status === "blocked" ? "danger" : ["withdrawn"].includes(item.status) ? "neutral" : "warning";
    const result = (item.result_facts || []).join("；") || item.result_quote || (item.failure_code ? `原因：${translateToken(item.failure_code)}` : "尚无结果证据");
    return `<article class="commitment-row" data-status="${escapeHtml(item.status)}">
      <div class="commitment-main"><strong>${escapeHtml(item.summary)}</strong><q>${escapeHtml(item.source_quote)}</q><span>形成于 ${formatTime(item.created_at)} · 群 ${escapeHtml(shortId(item.group_id, 10))}</span></div>
      <div class="commitment-meta"><span class="tag" data-tone="${tone}">${escapeHtml(commitmentStatusLabel(item.status))}</span><span>对谁负责：<strong>${escapeHtml(item.beneficiary_name || "群成员")}</strong></span><span>履约方式：<strong>${escapeHtml(item.fulfillment_mode === "reminder" ? "到时提醒" : item.fulfillment_mode === "capability" ? "执行能力" : "等待后续事实")}</strong></span><span>依赖能力：<strong>${escapeHtml(item.required_capability ? translateToken(item.required_capability) : "无需外部能力")}</strong></span>${item.due_at ? `<span>约定时间：<strong>${formatTime(item.due_at)}</strong></span>` : ""}${item.next_attempt_at ? `<span>下次尝试：<strong>${formatTime(item.next_attempt_at)}</strong></span>` : ""}<span>已尝试：<strong>${Number(item.attempt_count || 0)} 次</strong></span>${item.last_delivery_at ? `<span>最近交付：<strong>${formatTime(item.last_delivery_at)}</strong></span>` : ""}</div>
      <div class="commitment-result"><strong>履约结果</strong><p>${escapeHtml(result)}</p><div class="commitment-actions">${selfCommitmentActions(item)}</div></div>
    </article>`;
  }).join("") : '<div class="empty-state">目前没有爱弥斯亲口承担、需要继续跟进的事情。</div>';
}

function openSelfCommitmentConfirm(commitmentId, status) {
  const item = (state.cognition?.self_commitments || []).find((entry) => entry.commitment_id === commitmentId);
  if (!item) return;
  state.pendingSelfCommitment = { item, status };
  els.self_commitment_confirm_copy.textContent = `将“${item.summary}”修正为“${commitmentStatusLabel(status)}”。`;
  els.self_commitment_reason.value = "";
  els.self_commitment_confirm.showModal();
}

async function correctSelfCommitment() {
  if (!state.pendingSelfCommitment) return;
  const reason = els.self_commitment_reason.value.trim();
  if (!reason) {
    showError("请填写承诺状态修正原因。");
    return;
  }
  setBusy(els.self_commitment_confirm_submit, true, "保存中");
  try {
    await apiPost(`commitments/${encodeURIComponent(state.pendingSelfCommitment.item.commitment_id)}/status`, {
      confirm: true,
      status: state.pendingSelfCommitment.status,
      reason,
    });
    state.pendingSelfCommitment = null;
    els.self_commitment_confirm.close();
    await loadAll({ quiet: true });
    showToast("承诺状态已更新，后续对话立即使用");
  } catch (error) {
    showError(`修正承诺状态失败：${apiError(error)}`);
  } finally {
    setBusy(els.self_commitment_confirm_submit, false);
  }
}

async function runSelfCommitmentNow(commitmentId, button) {
  setBusy(button, true, "处理中");
  try {
    const result = await apiPost(`commitments/${encodeURIComponent(commitmentId)}/run`, { confirm: true });
    await loadAll({ quiet: true });
    const status = result?.commitment?.status;
    showToast(status === "completed" ? "已经交付并记录结果" : status === "blocked" ? "本次处理受阻，原因已记录" : "已完成本次履约检查");
  } catch (error) {
    showError(`处理承诺失败：${apiError(error)}`);
  } finally {
    setBusy(button, false);
  }
}

function renderQuality() {
  const quality = filteredQuality();
  els.quality_rate.textContent = `${Math.round(Number(quality.sent_rate || 0) * 100)}%`;
  els.quality_sent.textContent = String(quality.sent_count || 0);
  els.quality_silent.textContent = String(quality.silent_count || 0);
  renderHealth(els.quality_health);
  renderLearningQuality();
  renderReasonList(els.quality_reasons, quality.reason_counts);
}

function renderLearningQuality() {
  const learning = state.cognition?.relationship_learning || {};
  const groups = (learning.groups || []).filter((item) => !state.group || item.group_id === state.group);
  els.quality_learning_threshold.textContent = `门槛：${learning.min_reviewed_samples || 20} 条 · 误判率 ≤ ${Math.round(Number(learning.max_error_rate ?? 0.1) * 100)}%`;
  els.quality_learning_groups.innerHTML = groups.length ? groups.map((item) => {
    const rate = Math.round(Number(item.error_rate || 0) * 100);
    const status = item.auto_apply ? "自动生效" : item.eligible ? "达到门槛，待放行" : "影子学习";
    const tone = item.auto_apply ? "ok" : "warning";
    return `<div class="learning-group-row">
      <div><strong>群 ${escapeHtml(shortId(item.group_id, 14))}</strong><span>${escapeHtml(status)}</span></div>
      <dl><div><dt>待复核</dt><dd>${Number(item.pending || 0)}</dd></div><div><dt>已复核</dt><dd>${Number(item.reviewed_count || 0)}</dd></div><div><dt>误判率</dt><dd>${rate}%</dd></div></dl>
      <div class="learning-errors"><span>认错人 ${Number(item.wrong_person || 0)}</span><span>类型错 ${Number(item.wrong_kind || 0)}</span><span>上下文不足 ${Number(item.insufficient_context || 0)}</span><span>其他误判 ${Number(item.other_error || 0)}</span></div>
      <span class="status-badge" data-tone="${tone}">${escapeHtml(status)}</span>
    </div>`;
  }).join("") : '<div class="empty-state">当前范围还没有关系学习样本。</div>';
}

function relationshipChange(value) {
  if (!value) return "尚无关系状态";
  return `熟悉 ${value.familiarity} · 亲近 ${value.affinity} · 信任 ${value.trust} · 边界 ${value.boundary_pressure}`;
}

function governanceChange(action) {
  if (action.target_kind === "relationship") {
    return [relationshipChange(action.before), relationshipChange(action.after)];
  }
  if (action.target_kind === "relationship_evidence") {
    const before = action.before || {};
    const after = action.after || {};
    const evidence = before.evidence || after.evidence || {};
    const label = translateToken(evidence.kind || "关系证据");
    const beforeStatus = translateToken((before.evidence || {}).status || "pending");
    const afterEvidence = after.evidence || {};
    const afterStatus = translateToken(afterEvidence.review_code || afterEvidence.status || "pending");
    return [
      `${label} · ${beforeStatus} · ${evidence.evidence_text || "无摘要"}`,
      `${label} · ${afterStatus}`,
    ];
  }
  if (action.target_kind === "member_profile") {
    const before = action.before || {};
    const after = action.after || {};
    return [
      before.preferred_address ? `称呼为 ${before.preferred_address}` : `跟随群昵称 ${before.display_name || "成员"}`,
      after.preferred_address ? `称呼为 ${after.preferred_address}` : `跟随群昵称 ${after.display_name || "成员"}`,
    ];
  }
  if (action.target_kind === "member_identity_link") {
    return [
      action.before?.active ? "已有身份关联" : "保持独立身份",
      action.after?.active ? "已关联为同一成员" : "已解除身份关联",
    ];
  }
  if (action.target_kind === "self_commitment") {
    const before = action.before || {};
    const after = action.after || {};
    const summary = before.summary || after.summary || "该项承诺";
    return [
      `${summary} · ${commitmentStatusLabel(before.status || "pending")}`,
      `${summary} · ${commitmentStatusLabel(after.status || "pending")}`,
    ];
  }
  const before = action.before || {};
  const after = action.after || {};
  const text = before.text || after.text || "该条记忆";
  const beforeStatus = before.status === "deleted" ? "已删除" : "保留中";
  const afterStatus = after.status === "deleted" ? "已删除" : "已恢复";
  return [`${text} · ${beforeStatus}`, `${text} · ${afterStatus}`];
}

function renderGovernance() {
  const items = currentGovernance();
  els.governance_count.textContent = `${items.length} 条`;
  els.governance_empty.hidden = items.length > 0;
  els.governance_body.innerHTML = items.map((item) => {
    const [before, after] = governanceChange(item);
    const reverted = Boolean(item.reverted_at);
    return `<tr>
      <td><strong class="cell-primary">${escapeHtml(translateToken(item.action_type))}</strong><span class="cell-secondary">${escapeHtml(shortId(item.action_id, 12))}</span></td>
      <td><strong class="cell-primary">${escapeHtml(item.subject_name || "成员")}</strong><span class="cell-secondary">群 ${escapeHtml(shortId(item.group_id, 10))}</span></td>
      <td><div class="change-pair"><span>原：${escapeHtml(before)}</span><strong>后：${escapeHtml(after)}</strong></div></td>
      <td><strong class="cell-primary">${escapeHtml(item.reason)}</strong><span class="cell-secondary">${escapeHtml(item.actor)}</span></td>
      <td>${formatTime(item.created_at, true)}</td>
      <td><span class="tag" data-tone="${reverted ? "warning" : item.reverts_action_id ? "neutral" : "ok"}">${reverted ? "已回滚" : item.reverts_action_id ? "回滚记录" : "已生效"}</span></td>
      <td>${item.can_revert ? `<button class="text-button" type="button" data-revert-action="${escapeHtml(item.action_id)}">回滚</button>` : ""}</td>
    </tr>`;
  }).join("");
}

function filteredQuality() {
  if (!state.group) return state.cognition?.quality || {};
  const decisions = currentDecisions();
  const sent = decisions.filter((item) => item.sent).length;
  const reasons = {};
  decisions.forEach((item) => {
    const reason = item.end_reason || "unknown";
    reasons[reason] = (reasons[reason] || 0) + 1;
  });
  return {
    decision_count: decisions.length,
    sent_count: sent,
    silent_count: decisions.length - sent,
    direct_count: decisions.filter((item) => item.participation === "direct_required").length,
    sent_rate: decisions.length ? sent / decisions.length : 0,
    reason_counts: reasons,
  };
}

function decisionTitle(item) {
  if (item.sent) {
    return "选择开口";
  }
  return "选择沉默";
}

function decisionSubtitle(item) {
  const bits = [
    translateToken(item.scene || item.trigger || ""),
    translateToken(item.end_reason || item.participation || ""),
  ].filter(Boolean);
  return bits.join(" · ") || "没有摘要";
}

function renderDecisionList() {
  const items = currentDecisions();
  els.decision_count.textContent = String(items.length);
  els.decision_list.innerHTML = items.length ? items.map((item) => (
    `<button type="button" class="decision-item ${item.decision_id === state.selectedDecision ? "is-selected" : ""}" data-decision-id="${escapeHtml(item.decision_id)}">
      <span class="decision-time">${formatTime(item.timestamp)}</span>
      <span class="decision-main"><strong>${decisionTitle(item)}</strong><span>${escapeHtml(decisionSubtitle(item))}</span></span>
    </button>`
  )).join("") : '<div class="empty-state">当前筛选下没有判断记录。可把结果切回「全部」，或点右上角刷新。</div>';
}

function renderContext(items) {
  els.context_list.innerHTML = items?.length ? items.map((item) => (
    `<div class="context-message ${item.is_reply ? "is-reply" : ""}"><small>${escapeHtml(item.sender_name || "群成员")} · ${formatTime(item.timestamp, true)}</small>${escapeHtml(item.text || "[非文本消息]")}</div>`
  )).join("") : '<div class="empty-state">这次判断没有可展示的附近聊天。</div>';
}

function renderTrace(trace) {
  els.trace_meta.textContent = `${trace.sent ? "已开口" : "沉默"} · 群 ${shortId(trace.group_id, 10)}`;
  const summary = [trace.trigger, trace.scene, trace.participation, trace.end_reason].filter(Boolean).map(translateToken);
  els.path_summary.textContent = summary.join(" → ") || "没有可展示的路径摘要。";
  renderTargetSummary(trace.addressee || {});
  renderContext(trace.context || []);
  els.trace_list.innerHTML = (trace.stages || []).map((stage) => (
    `<li class="trace-item"><strong>${escapeHtml(STAGE_LABEL[stage.state] || stage.state)}</strong><span>${escapeHtml(stage.state === "ADDRESSEE" ? "对象识别结果见上方" : describeValue(stage.reason))}</span><time>${formatTime(stage.timestamp, true)}</time></li>`
  )).join("");
}

function renderTargetSummary(addressee) {
  const rows = [
    ["reply", els.target_reply, els.target_reply_source],
    ["social", els.target_social, els.target_social_source],
    ["memory", els.target_memory, els.target_memory_source],
  ];
  const hasData = rows.some(([key]) => addressee[key]);
  els.target_summary.hidden = !hasData;
  rows.forEach(([key, nameNode, sourceNode]) => {
    const item = addressee[key] || {};
    const kind = item.kind || "ambiguous";
    nameNode.textContent = item.name || (kind === "group" ? "群聊" : "未确定");
    sourceNode.textContent = `${translateToken(item.source || kind)} · 可信度 ${Math.round(Number(item.confidence || 0) * 100)}%`;
  });
}

async function selectDecision(id) {
  state.selectedDecision = id;
  renderDecisionList();
  els.path_summary.textContent = "正在读取判断路径…";
  els.context_list.textContent = "";
  els.target_summary.hidden = true;
  els.trace_list.innerHTML = "";
  try {
    renderTrace(await apiGet(`decisions/${encodeURIComponent(id)}`));
  } catch (error) {
    showError(`读取判断路径失败：${apiError(error)}`);
  }
}

function applyFilters() {
  renderOverview();
  renderAttention();
  renderRelationships();
  renderMembers();
  renderMemories();
  renderSelf();
  renderDecisionList();
}

function renderAll() {
  state.paused = Boolean(state.cognition?.runtime?.paused);
  els.badge.textContent = state.paused ? "已暂停" : "运行中";
  els.badge.dataset.tone = state.paused ? "warning" : "ok";
  els.pause_toggle.textContent = state.paused ? "恢复" : "暂停";
  fillGroupFilter();
  renderOverview();
  renderAttention();
  renderRelationships();
  renderMembers();
  renderMemories();
  renderCapabilities();
  renderSelf();
  renderDecisionList();
  renderQuality();
  renderGovernance();
}

async function loadDecisions() {
  const payload = await apiGet("decisions", {
    group_id: state.group || "",
    outcome: els.filter_outcome.value,
    limit: 200,
  });
  state.decisions = payload.items || [];
}

async function loadAll({ quiet = false } = {}) {
  if (!quiet) setBusy(els.refresh, true, "刷新中");
  showError("");
  try {
    const [cognition, decisions] = await Promise.all([
      apiGet("cognition"),
      apiGet("decisions", { group_id: state.group || "", outcome: els.filter_outcome.value, limit: 200 }),
    ]);
    state.cognition = cognition;
    state.decisions = decisions.items || [];
    renderAll();
  } catch (error) {
    showError(`读取治理数据失败：${apiError(error)}`);
    els.badge.textContent = "连接失败";
    els.badge.dataset.tone = "danger";
  } finally {
    if (!quiet) setBusy(els.refresh, false);
  }
}

async function togglePause() {
  setBusy(els.pause_toggle, true);
  showError("");
  try {
    const result = await apiPost("runtime", { paused: !state.paused });
    state.paused = Boolean(result.paused);
    if (state.cognition?.runtime) state.cognition.runtime.paused = state.paused;
    renderAll();
    showToast(state.paused ? "爱弥斯已暂停参与" : "爱弥斯已恢复参与");
  } catch (error) {
    showError(`切换运行状态失败：${apiError(error)}`);
  } finally {
    setBusy(els.pause_toggle, false);
  }
}

function openRelationshipConfirm(event) {
  event.preventDefault();
  if (!state.selectedRelationship) return;
  const form = new FormData(els.relationship_form);
  state.pendingRelationship = {
    group_id: state.selectedRelationship.group_id,
    user_id: state.selectedRelationship.user_id,
    familiarity: Number(form.get("familiarity")),
    affinity: Number(form.get("affinity")),
    trust: Number(form.get("trust")),
    boundary_pressure: Number(form.get("boundary_pressure")),
    reason: String(form.get("reason") || "").trim(),
  };
  if (!state.pendingRelationship.reason) {
    showError("请填写关系修正原因。");
    return;
  }
  els.relationship_confirm_copy.textContent = `将 ${state.selectedRelationship.display_name} 的关系修正为：熟悉 ${state.pendingRelationship.familiarity}、亲近 ${state.pendingRelationship.affinity}、信任 ${state.pendingRelationship.trust}、边界压力 ${state.pendingRelationship.boundary_pressure}。原因：${state.pendingRelationship.reason}`;
  els.relationship_confirm.showModal();
}

async function saveRelationship() {
  if (!state.pendingRelationship) return;
  setBusy(els.relationship_confirm_submit, true, "保存中");
  try {
    const result = await apiPost("relationships/correct", { ...state.pendingRelationship, confirm: true });
    const updated = result.relationship;
    const index = state.cognition.relationships.findIndex((item) => item.group_id === updated.group_id && item.user_id === updated.user_id);
    if (index >= 0) state.cognition.relationships[index] = updated;
    state.selectedRelationship = updated;
    state.pendingRelationship = null;
    els.relationship_confirm.close();
    await loadAll({ quiet: true });
    state.selectedRelationship = (state.cognition?.relationships || []).find((item) => item.group_id === updated.group_id && item.user_id === updated.user_id) || updated;
    renderRelationships();
    showToast("关系已修正，后续判断立即生效");
  } catch (error) {
    showError(`修正关系失败：${apiError(error)}`);
  } finally {
    setBusy(els.relationship_confirm_submit, false);
  }
}

function openEvidenceConfirm(eventId) {
  const item = (state.cognition?.relationship_evidence || []).find((event) => event.event_id === eventId);
  if (!item || item.status !== "accepted") return;
  state.pendingEvidence = item;
  els.evidence_confirm_copy.textContent = `“${item.evidence_text || translateToken(item.kind)}”`;
  els.evidence_reject_reason.value = "";
  els.evidence_confirm.showModal();
}

function openEvidenceReview(eventId) {
  const item = (state.cognition?.relationship_evidence || []).find((event) => event.event_id === eventId);
  if (!item || item.status !== "pending") return;
  state.pendingEvidence = item;
  els.evidence_review_copy.textContent = `${item.display_name}：“${item.evidence_text || translateToken(item.kind)}”`;
  els.evidence_review_outcome.value = "correct";
  els.evidence_review_reason.value = "";
  els.evidence_review.showModal();
}

async function reviewEvidence() {
  if (!state.pendingEvidence) return;
  const outcome = els.evidence_review_outcome.value;
  const reason = els.evidence_review_reason.value.trim();
  if (!reason) {
    showError("请填写复核说明。");
    return;
  }
  const selected = state.selectedRelationship
    ? { group_id: state.selectedRelationship.group_id, user_id: state.selectedRelationship.user_id }
    : null;
  setBusy(els.evidence_review_submit, true, "保存中");
  try {
    await apiPost(`relationships/evidence/${encodeURIComponent(state.pendingEvidence.event_id)}/review`, { confirm: true, outcome, reason });
    state.pendingEvidence = null;
    els.evidence_review.close();
    await loadAll({ quiet: true });
    state.selectedRelationship = selected
      ? (state.cognition?.relationships || []).find((item) => item.group_id === selected.group_id && item.user_id === selected.user_id) || null
      : null;
    renderRelationships();
    showToast(outcome === "correct" ? "证据已确认并应用到关系" : "误判已记录，关系未受影响");
  } catch (error) {
    showError(`复核关系证据失败：${apiError(error)}`);
  } finally {
    setBusy(els.evidence_review_submit, false);
  }
}

async function rejectEvidence() {
  if (!state.pendingEvidence) return;
  const reason = els.evidence_reject_reason.value.trim();
  if (!reason) {
    showError("请填写否定原因。");
    return;
  }
  const selected = state.selectedRelationship
    ? { group_id: state.selectedRelationship.group_id, user_id: state.selectedRelationship.user_id }
    : null;
  setBusy(els.evidence_confirm_submit, true, "重建中");
  try {
    await apiPost(`relationships/evidence/${encodeURIComponent(state.pendingEvidence.event_id)}/reject`, { confirm: true, reason });
    state.pendingEvidence = null;
    els.evidence_confirm.close();
    await loadAll({ quiet: true });
    state.selectedRelationship = selected
      ? (state.cognition?.relationships || []).find((item) => item.group_id === selected.group_id && item.user_id === selected.user_id) || null
      : null;
    renderRelationships();
    showToast("证据已否定，关系已按剩余证据重建");
  } catch (error) {
    showError(`否定关系证据失败：${apiError(error)}`);
  } finally {
    setBusy(els.evidence_confirm_submit, false);
  }
}

function openMemoryConfirm(memoryId) {
  const item = (state.cognition?.memories || []).find((memory) => memory.memory_id === memoryId);
  if (!item) return;
  state.pendingMemory = item;
  els.memory_confirm_copy.textContent = `“${item.text}”`;
  els.memory_confirm.showModal();
}

async function deleteMemory() {
  if (!state.pendingMemory) return;
  const reason = els.memory_delete_reason.value.trim();
  if (!reason) {
    showError("请填写删除原因。");
    return;
  }
  setBusy(els.memory_confirm_submit, true, "删除中");
  try {
    await apiPost(`memories/${encodeURIComponent(state.pendingMemory.memory_id)}/delete`, { confirm: true, reason });
    state.pendingMemory = null;
    els.memory_confirm.close();
    await loadAll({ quiet: true });
    showToast("这件事已删除，并已阻止再次自动写入");
  } catch (error) {
    showError(`删除记忆失败：${apiError(error)}`);
  } finally {
    setBusy(els.memory_confirm_submit, false);
  }
}

function openGovernanceConfirm(actionId) {
  const action = (state.cognition?.governance || []).find((item) => item.action_id === actionId);
  if (!action || !action.can_revert) return;
  state.pendingGovernance = action;
  const label = translateToken(action.action_type);
  els.governance_confirm_copy.textContent = `将回滚“${label}”，恢复 ${action.subject_name || "该对象"} 在此次操作前的状态。`;
  els.governance_revert_reason.value = "";
  els.governance_confirm.showModal();
}

async function revertGovernance() {
  if (!state.pendingGovernance) return;
  const reason = els.governance_revert_reason.value.trim();
  if (!reason) {
    showError("请填写回滚原因。");
    return;
  }
  setBusy(els.governance_confirm_submit, true, "回滚中");
  try {
    await apiPost(`governance/${encodeURIComponent(state.pendingGovernance.action_id)}/revert`, { confirm: true, reason });
    state.pendingGovernance = null;
    state.selectedRelationship = null;
    els.relationship_editor.hidden = true;
    els.governance_confirm.close();
    await loadAll({ quiet: true });
    showToast("状态已恢复，回滚记录已写入审计");
  } catch (error) {
    showError(`回滚治理操作失败：${apiError(error)}`);
  } finally {
    setBusy(els.governance_confirm_submit, false);
  }
}

document.querySelectorAll("[data-module]").forEach((item) => item.addEventListener("click", () => switchModule(item.dataset.module)));
document.querySelectorAll("[data-go]").forEach((item) => item.addEventListener("click", () => switchModule(item.dataset.go)));
document.querySelector("[data-close-relationship]").addEventListener("click", () => {
  state.selectedRelationship = null;
  els.relationship_editor.hidden = true;
  renderRelationships();
});
document.querySelector("[data-close-member]").addEventListener("click", () => {
  state.selectedMember = null;
  els.member_editor.hidden = true;
  renderMembers();
});
document.querySelector("[data-cancel-relation]").addEventListener("click", () => els.relationship_confirm.close());
document.querySelector("[data-cancel-memory]").addEventListener("click", () => els.memory_confirm.close());
document.querySelector("[data-cancel-evidence]").addEventListener("click", () => els.evidence_confirm.close());
document.querySelector("[data-cancel-evidence-review]").addEventListener("click", () => els.evidence_review.close());
document.querySelector("[data-cancel-governance]").addEventListener("click", () => els.governance_confirm.close());
document.querySelector("[data-cancel-member-link]").addEventListener("click", () => els.member_link_confirm.close());
document.querySelector("[data-cancel-continuity]").addEventListener("click", () => els.continuity_confirm.close());
document.querySelector("[data-cancel-self-commitment]").addEventListener("click", () => els.self_commitment_confirm.close());

els.refresh.addEventListener("click", () => loadAll());
els.pause_toggle.addEventListener("click", togglePause);
els.global_group_filter.addEventListener("change", async () => {
  state.group = els.global_group_filter.value;
  state.selectedRelationship = null;
  state.selectedMember = null;
  els.relationship_editor.hidden = true;
  els.member_editor.hidden = true;
  try {
    await loadDecisions();
    applyFilters();
  } catch (error) {
    showError(`筛选群失败：${apiError(error)}`);
  }
});
els.filter_outcome.addEventListener("change", async () => {
  try {
    await loadDecisions();
    renderDecisionList();
  } catch (error) {
    showError(`筛选判断失败：${apiError(error)}`);
  }
});
els.relationship_body.addEventListener("click", (event) => {
  const row = event.target.closest("[data-relationship-key]");
  if (row) selectRelationship(row.dataset.relationshipKey);
});
els.relationship_form.addEventListener("submit", openRelationshipConfirm);
els.member_body.addEventListener("click", (event) => {
  const row = event.target.closest("[data-member-key]");
  if (row) selectMember(row.dataset.memberKey);
});
els.member_form.addEventListener("submit", saveMemberAddress);
els.member_link_button.addEventListener("click", openMemberLinkConfirm);
els.member_link_confirm_submit.addEventListener("click", linkMemberIdentity);
els.member_continuity.addEventListener("click", (event) => {
  const button = event.target.closest("[data-continuity-id]");
  if (button) openContinuityConfirm(button.dataset.continuityId, button.dataset.continuityStatus);
});
els.continuity_confirm_submit.addEventListener("click", correctContinuity);
els.self_commitment_list.addEventListener("click", (event) => {
  const runButton = event.target.closest("[data-run-self-commitment]");
  if (runButton) {
    runSelfCommitmentNow(runButton.dataset.runSelfCommitment, runButton);
    return;
  }
  const button = event.target.closest("[data-self-commitment-id]");
  if (button) openSelfCommitmentConfirm(button.dataset.selfCommitmentId, button.dataset.selfCommitmentStatus);
});
els.self_commitment_confirm_submit.addEventListener("click", correctSelfCommitment);
els.relationship_confirm_submit.addEventListener("click", saveRelationship);
els.relationship_evidence.addEventListener("click", (event) => {
  const review = event.target.closest("[data-review-evidence]");
  if (review) openEvidenceReview(review.dataset.reviewEvidence);
  const button = event.target.closest("[data-reject-evidence]");
  if (button) openEvidenceConfirm(button.dataset.rejectEvidence);
});
els.evidence_confirm_submit.addEventListener("click", rejectEvidence);
els.evidence_review_submit.addEventListener("click", reviewEvidence);
els.memory_body.addEventListener("click", (event) => {
  const button = event.target.closest("[data-delete-memory]");
  if (button) openMemoryConfirm(button.dataset.deleteMemory);
});
els.memory_confirm_submit.addEventListener("click", deleteMemory);
els.governance_confirm_submit.addEventListener("click", revertGovernance);
els.governance_body.addEventListener("click", (event) => {
  const button = event.target.closest("[data-revert-action]");
  if (button) openGovernanceConfirm(button.dataset.revertAction);
});
els.decision_list.addEventListener("click", (event) => {
  const item = event.target.closest("[data-decision-id]");
  if (item) selectDecision(item.dataset.decisionId);
});

loadAll();
