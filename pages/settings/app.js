const bridge = window.AstrBotPluginPage;

const LABEL = {
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
  PLAN: "计划",
  FALLBACK: "降级",
  SPEAK: "开口",
  GUARD: "护栏",
  COMPOSE: "组装",
  SCHEDULE: "调度",
  SEND: "发送",
  MEMORY: "记忆",
  END: "结束",
};

const HIGHLIGHT = new Set([
  "OBSERVE",
  "SCENE",
  "PARTICIPATION",
  "INTENT",
  "ACT",
  "SPEAK",
  "SEND",
  "END",
]);

const els = {
  badge: document.getElementById("badge"),
  error: document.getElementById("error"),
  refresh: document.getElementById("refresh"),
  pauseToggle: document.getElementById("pause-toggle"),
  filterGroup: document.getElementById("filter-group"),
  filterOutcome: document.getElementById("filter-outcome"),
  decisionList: document.getElementById("decision-list"),
  decisionCount: document.getElementById("decision-count"),
  pathSummary: document.getElementById("path-summary"),
  traceList: document.getElementById("trace-list"),
  traceMeta: document.getElementById("trace-meta"),
  runtimePaused: document.getElementById("runtime-paused"),
  runtimeHealth: document.getElementById("runtime-health"),
  runtimeGroups: document.getElementById("runtime-groups"),
};

const state = {
  paused: false,
  selectedId: null,
  groups: [],
};

function showError(message) {
  els.error.hidden = !message;
  els.error.textContent = message || "";
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function formatTime(timestamp) {
  if (!timestamp) return "—";
  const date = new Date(Number(timestamp) * 1000);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString([], {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function shortId(id) {
  const value = String(id || "");
  return value.length > 10 ? `${value.slice(0, 8)}…` : value;
}

function setModule(name) {
  document.querySelectorAll(".module-tab").forEach((tab) => {
    tab.classList.toggle("is-active", tab.dataset.module === name);
  });
  document.querySelectorAll("[data-module-panel]").forEach((panel) => {
    panel.hidden = panel.dataset.modulePanel !== name;
  });
}

function renderStatus(payload) {
  state.paused = Boolean(payload.paused);
  els.badge.textContent = state.paused ? "已暂停" : "观察中";
  els.badge.dataset.tone = state.paused ? "warn" : "ok";
  els.pauseToggle.textContent = state.paused ? "恢复" : "暂停";
  els.runtimePaused.textContent = state.paused ? "暂停观察" : "正在观察";
  els.runtimeHealth.textContent = payload.config_health || "—";
  const groups = payload.bootstrapped || [];
  state.groups = groups;
  els.runtimeGroups.textContent = String(groups.length);

  const current = els.filterGroup.value;
  els.filterGroup.innerHTML =
    '<option value="">全部</option>' +
    groups
      .map(
        (groupId) =>
          `<option value="${escapeHtml(groupId)}">${escapeHtml(groupId)}</option>`,
      )
      .join("");
  if (current && groups.includes(current)) {
    els.filterGroup.value = current;
  }
}

function pathLine(item) {
  const parts = [
    item.trigger || "—",
    item.scene || "—",
    item.act || item.intent || "—",
    item.sent ? "发送" : item.end_reason || "沉默",
  ];
  return parts.join(" → ");
}

function renderDecisionList(items) {
  els.decisionCount.textContent = `${items.length} 条`;
  if (!items.length) {
    els.decisionList.innerHTML =
      '<div class="empty">暂无决策记录。群里产生观察后刷新即可。</div>';
    return;
  }
  els.decisionList.innerHTML = items
    .map((item) => {
      const selected = item.decision_id === state.selectedId ? " is-selected" : "";
      const tone = item.sent ? "ok" : "mute";
      return `<button type="button" class="decision-row${selected}" data-id="${escapeHtml(item.decision_id)}">
        <span class="decision-top">
          <span class="tag" data-tone="${tone}">${item.sent ? "发送" : "沉默"}</span>
          <span class="muted small">${escapeHtml(formatTime(item.timestamp))}</span>
          <span class="muted small">群 ${escapeHtml(item.group_id)}</span>
        </span>
        <span class="decision-path">${escapeHtml(pathLine(item))}</span>
        <span class="muted small">${escapeHtml(shortId(item.decision_id))}</span>
      </button>`;
    })
    .join("");
}

function renderTrace(payload) {
  if (!payload) {
    els.pathSummary.className = "path-summary muted";
    els.pathSummary.textContent = "选择左侧一条决策查看路径。";
    els.traceMeta.textContent = "";
    els.traceList.innerHTML = "";
    return;
  }
  els.pathSummary.className = "path-summary";
  els.pathSummary.innerHTML = `
    <div class="path-main">${escapeHtml(pathLine(payload))}</div>
    <div class="muted small">终态 ${escapeHtml(payload.end_reason || "—")} · ${payload.sent ? "已发送" : "未发送"}</div>
  `;
  els.traceMeta.textContent = `群 ${payload.group_id}`;
  const stages = payload.stages || [];
  els.traceList.innerHTML = stages
    .map((stage) => {
      const key = String(stage.state || "");
      const label = LABEL[key] || key;
      const mark = HIGHLIGHT.has(key) ? " is-key" : "";
      return `<li class="trace-item${mark}">
        <span class="trace-state">${escapeHtml(label)}</span>
        <span class="trace-reason">${escapeHtml(stage.reason || "—")}</span>
        <span class="muted small">${escapeHtml(formatTime(stage.timestamp))}</span>
      </li>`;
    })
    .join("");
}

async function loadStatus() {
  const payload = await bridge.apiGet("status");
  renderStatus(payload);
}

async function loadDecisions() {
  const groupId = els.filterGroup.value;
  const outcome = els.filterOutcome.value || "all";
  const params = { limit: 50, outcome };
  if (groupId) params.group_id = groupId;
  const payload = await bridge.apiGet("decisions", params);
  const items = payload.items || [];
  if (
    state.selectedId &&
    !items.some((item) => item.decision_id === state.selectedId)
  ) {
    state.selectedId = null;
    renderTrace(null);
  }
  renderDecisionList(items);
}

async function loadTrace(decisionId) {
  state.selectedId = decisionId;
  const payload = await bridge.apiGet(
    `decisions/${encodeURIComponent(decisionId)}`,
  );
  renderTrace(payload);
  Array.from(els.decisionList.querySelectorAll(".decision-row")).forEach((row) => {
    row.classList.toggle("is-selected", row.dataset.id === decisionId);
  });
}

async function reloadAll() {
  showError("");
  await loadStatus();
  await loadDecisions();
  if (state.selectedId) {
    await loadTrace(state.selectedId);
  }
}

async function togglePause() {
  showError("");
  await bridge.apiPost("runtime", { paused: !state.paused });
  await reloadAll();
}

await bridge.ready();

document.querySelectorAll(".module-tab").forEach((tab) => {
  tab.addEventListener("click", () => setModule(tab.dataset.module));
});

els.refresh.addEventListener("click", () => {
  reloadAll().catch((error) => showError(error.message || String(error)));
});
els.pauseToggle.addEventListener("click", () => {
  togglePause().catch((error) => showError(error.message || String(error)));
});
els.filterGroup.addEventListener("change", () => {
  loadDecisions().catch((error) => showError(error.message || String(error)));
});
els.filterOutcome.addEventListener("change", () => {
  loadDecisions().catch((error) => showError(error.message || String(error)));
});
els.decisionList.addEventListener("click", (event) => {
  const row = event.target.closest("[data-id]");
  if (!row) return;
  loadTrace(row.dataset.id).catch((error) =>
    showError(error.message || String(error)),
  );
});

try {
  await reloadAll();
} catch (error) {
  showError(error.message || String(error));
}
