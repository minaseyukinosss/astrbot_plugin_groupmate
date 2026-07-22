const bridge = window.AstrBotPluginPage;

const state = {
  view: window.location.hash === "#review" ? "review" : "overview",
  overview: null,
  decisions: [],
  selected: null,
  nextCursor: null,
  hasMore: false,
  loading: false,
  pendingSelectId: null,
  filters: { label: "unlabeled", action: "all", limit: 20 },
};

const ACTION_LABELS = { respond: "回复", ignore: "沉默", bypass: "旁路" };
const RUNTIME_PAUSE_ENDPOINT = "runtime/pause";
const RUNTIME_RESUME_ENDPOINT = "runtime/resume";
const LABEL_LABELS = {
  unlabeled: "未标注",
  must_respond: "必须回复",
  may_respond: "可以回复",
  must_silence: "必须沉默",
  skipped: "跳过",
};

const $ = (id) => document.getElementById(id);

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatTime(timestamp) {
  if (!timestamp) return "时间未知";
  const date = new Date(Number(timestamp) * 1000);
  if (Number.isNaN(date.getTime())) return "时间未知";
  return date.toLocaleString([], { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function formatPercent(value) {
  return `${Math.round(Number(value || 0) * 100)}%`;
}

function setStatus(message, isError = false) {
  $("live-status").textContent = message;
  const banner = $("error-banner");
  banner.hidden = !isError;
  if (isError) banner.textContent = message;
}

function setView(view) {
  state.view = view === "review" ? "review" : "overview";
  window.location.hash = state.view === "review" ? "review" : "overview";
  document.querySelectorAll("[data-view-panel]").forEach((panel) => {
    panel.hidden = panel.dataset.viewPanel !== state.view;
  });
  document.querySelectorAll("[data-view]").forEach((tab) => {
    tab.classList.toggle("is-active", tab.dataset.view === state.view);
  });
  if (state.view === "review" && !state.decisions.length) loadDecisions();
}

function renderOverview() {
  const data = state.overview;
  if (!data) return;
  const runtime = data.runtime || {};
  const policy = data.data_policy || {};
  const paused = Boolean(runtime.paused);
  const shadow = Boolean(runtime.shadow_mode);
  const statusText = paused ? "已暂停" : "运行中";
  const badge = $("runtime-badge");
  badge.textContent = statusText;
  badge.dataset.tone = paused ? "warning" : "success";
  $("runtime-text").textContent = statusText;
  $("mode-text").textContent = shadow ? "影子模式" : "正常模式";
  $("group-count").textContent = String(runtime.initialized_group_count ?? 0);
  $("updated-at").textContent = runtime.last_updated_at ? `最近记录 ${formatTime(runtime.last_updated_at)}` : "暂无记录";
  $("pending-count").textContent = String(data.pending_count ?? 0);
  $("pending-description").textContent = data.pending_count
    ? "标注结果会进入本地评测集，原始模型判断不会被修改。"
    : "当前没有未标注记录；开启正文保存后，后续记录可供人工复核。";
  $("pause-button").hidden = false;
  $("pause-button").textContent = paused ? "恢复观察" : "暂停观察";
  $("runtime-action").textContent = paused ? "恢复观察" : "暂停观察";
  $("runtime-action").dataset.paused = String(paused);
  document.querySelector("[data-status-dot]").dataset.tone = paused ? "warning" : "success";
  $("sample-note").textContent = policy.sample_sufficient ? "样本充足" : "样本不足";
  $("policy-note").textContent = policy.stores_message_text
    ? `已保存脱敏正文，记录保留 ${policy.shadow_retention_days} 天。`
    : `当前只保存统计特征，记录保留 ${policy.shadow_retention_days} 天。`;
  renderRecent(data.recent || []);
  renderSummary(data.actions || {});
}

function decisionRow(item, selected = false) {
  const preview = escapeHtml(item.message_preview || "未保存文本");
  const action = ACTION_LABELS[item.action] || item.action || "未知";
  const label = LABEL_LABELS[item.label] || item.label || "未标注";
  return `<button class="decision-row${selected ? " is-selected" : ""}" data-open-id="${escapeHtml(item.decision_id)}" type="button">
    <span class="decision-main"><span class="decision-title">${preview}</span><span class="decision-subline">${escapeHtml(formatTime(item.created_at))} · ${escapeHtml(action)} · 置信度 ${formatPercent(item.confidence)} · ${escapeHtml(item.reason_code || "未知原因")}</span></span>
    <span class="decision-actions"><span class="tag" data-action="${escapeHtml(item.action)}">${escapeHtml(action)}</span><span class="tag" data-label="${escapeHtml(item.label)}">${escapeHtml(label)}</span></span>
  </button>`;
}

function renderRecent(items) {
  $("recent-list").innerHTML = items.length
    ? items.map((item) => decisionRow(item)).join("")
    : '<div class="empty-row">暂无待处理决策</div>';
}

function renderSummary(actions) {
  const names = ["respond", "ignore", "bypass"];
  const total = names.reduce((sum, name) => sum + Number(actions[name] || 0), 0);
  $("summary-list").innerHTML = names.map((name) => {
    const value = Number(actions[name] || 0);
    const width = total ? Math.max(3, Math.round((value / total) * 100)) : 0;
    return `<div class="summary-line"><span>${escapeHtml(ACTION_LABELS[name])}</span><div class="summary-bar"><span style="width:${width}%"></span></div><strong>${value}</strong></div>`;
  }).join("");
}

function renderReview() {
  const list = $("decision-list");
  $("review-count").textContent = state.decisions.length ? `已加载 ${state.decisions.length} 条` : "暂无记录";
  $("load-more").hidden = !state.hasMore;
  list.innerHTML = state.decisions.length
    ? state.decisions.map((item) => decisionRow(item, state.selected?.decision_id === item.decision_id)).join("")
    : '<div class="empty-row">当前筛选下没有记录</div>';
  renderDetail();
}

function renderDetail() {
  const item = state.selected;
  const panel = $("decision-detail");
  if (!item) {
    panel.innerHTML = '<div class="detail-empty"><span class="detail-icon">↗</span><h2>选择一条决策</h2><p class="muted">从左侧列表打开详情后，可以给模型判断添加人工标签。</p></div>';
    return;
  }
  const context = Array.isArray(item.context) && item.context.length
    ? `<div class="context-block">${item.context.map((row) => `<div class="context-row"><strong>${escapeHtml(row.sender || "成员")}</strong><span>${escapeHtml(row.text || "[无文本]")}</span></div>`).join("")}</div>`
    : `<div class="context-block muted">${escapeHtml(item.message_preview || "未保存文本")}</div>`;
  const labels = ["must_respond", "may_respond", "must_silence", "skipped"];
  panel.innerHTML = `<div class="section-heading compact"><div><p class="eyebrow">${escapeHtml(item.decision_id)}</p><h2 class="detail-title">${escapeHtml(item.message_preview || "未保存文本")}</h2></div><span class="tag" data-action="${escapeHtml(item.action)}">${escapeHtml(ACTION_LABELS[item.action] || item.action)}</span></div>
    ${context}
    <div class="detail-grid"><div class="detail-field"><span>模型判断</span><strong>${escapeHtml(ACTION_LABELS[item.action] || item.action)} · ${formatPercent(item.confidence)}</strong></div><div class="detail-field"><span>原因</span><strong>${escapeHtml(item.reason_code || "未知")}</strong></div><div class="detail-field"><span>人工标签</span><strong>${escapeHtml(LABEL_LABELS[item.label] || item.label)}</strong></div><div class="detail-field"><span>决策耗时</span><strong>${Number(item.latency_ms || 0).toFixed(1)} ms</strong></div></div>
    <div class="label-actions" aria-label="人工标签">${labels.map((label) => `<button class="button ${item.label === label ? "button-primary" : "button-secondary"}" data-label-action="${label}" type="button">${LABEL_LABELS[label]}</button>`).join("")}</div>`;
}

async function loadOverview() {
  try {
    const data = await bridge.apiGet("dashboard/overview");
    state.overview = data;
    renderOverview();
    setStatus("概览已更新");
  } catch (error) {
    setStatus(error?.message || "概览暂时无法读取，请重试。", true);
  }
}

async function loadDecisions(append = false) {
  if (state.loading) return;
  state.loading = true;
  try {
    const params = { ...state.filters };
    if (append && state.nextCursor) params.cursor = state.nextCursor;
    const data = await bridge.apiGet("shadow/decisions", params);
    const items = Array.isArray(data.items) ? data.items : [];
    state.decisions = append ? state.decisions.concat(items) : items;
    state.nextCursor = data.next_cursor || null;
    state.hasMore = Boolean(data.has_more);
    if (!append) {
      state.selected = state.decisions.find((item) => item.decision_id === state.pendingSelectId)
        || state.decisions[0]
        || null;
      state.pendingSelectId = null;
    }
    renderReview();
    setStatus("审阅列表已更新");
  } catch (error) {
    setStatus(error?.message || "影子决策暂时无法读取，请重试。", true);
  } finally {
    state.loading = false;
  }
}

async function setPaused(paused) {
  try {
    await bridge.apiPost(paused ? RUNTIME_PAUSE_ENDPOINT : RUNTIME_RESUME_ENDPOINT, {});
    await loadOverview();
    setStatus(paused ? "已暂停观察" : "已恢复观察");
  } catch (error) {
    setStatus(error?.message || "运行状态切换失败，请重试。", true);
  }
}

async function labelSelected(label) {
  if (!state.selected) return;
  try {
    const item = await bridge.apiPost(`shadow/decisions/${encodeURIComponent(state.selected.decision_id)}/label`, { label });
    const index = state.decisions.findIndex((row) => row.decision_id === item.decision_id);
    if (index >= 0) state.decisions[index] = item;
    state.selected = item;
    await loadOverview();
    if (state.filters.label === "unlabeled") {
      state.decisions = state.decisions.filter((row) => row.label === "unlabeled");
      state.selected = state.decisions[0] || null;
    }
    renderReview();
    setStatus("人工标签已保存");
  } catch (error) {
    setStatus(error?.message || "人工标签保存失败，请重试。", true);
  }
}

async function downloadExport() {
  try {
    await bridge.download("shadow/export", {}, "shadow_reviewed.jsonl");
    setStatus("评测集下载已开始");
  } catch (error) {
    setStatus(error?.message || "没有可导出的已标注记录。", true);
  }
}

document.querySelectorAll("[data-view]").forEach((button) => {
  button.addEventListener("click", () => setView(button.dataset.view));
});
window.addEventListener("hashchange", () => setView(window.location.hash.slice(1)));
$("refresh-button").addEventListener("click", loadOverview);
$("review-refresh").addEventListener("click", () => loadDecisions(false));
$("review-pending").addEventListener("click", () => setView("review"));
$("open-review").addEventListener("click", () => setView("review"));
$("export-button").addEventListener("click", downloadExport);
$("pause-button").addEventListener("click", () => setPaused($("pause-button").textContent === "暂停观察"));
$("runtime-action").addEventListener("click", () => setPaused($("runtime-action").dataset.paused !== "true"));
$("load-more").addEventListener("click", () => loadDecisions(true));
$("label-filter").addEventListener("change", (event) => {
  state.filters.label = event.target.value;
  state.nextCursor = null;
  loadDecisions(false);
});
$("action-filter").addEventListener("change", (event) => {
  state.filters.action = event.target.value;
  state.nextCursor = null;
  loadDecisions(false);
});
document.addEventListener("click", (event) => {
  const open = event.target.closest("[data-open-id]");
  if (open) {
    state.selected = state.decisions.find((item) => item.decision_id === open.dataset.openId)
      || state.overview?.recent?.find((item) => item.decision_id === open.dataset.openId)
      || null;
    if (state.view !== "review") {
      state.pendingSelectId = open.dataset.openId;
      setView("review");
    } else {
      renderReview();
    }
    return;
  }
  const label = event.target.closest("[data-label-action]");
  if (label) labelSelected(label.dataset.labelAction);
});

(async function boot() {
  await bridge.ready();
  setView(state.view);
  await loadOverview();
})();
