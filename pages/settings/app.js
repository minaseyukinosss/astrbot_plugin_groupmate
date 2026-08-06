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
  PLAN: "表达",
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

/** 决策码 → 中文；未知码原样保留，便于对照日志。 */
const TERM = {
  // 触发
  ignore: "忽略",
  command: "宿主命令",
  native_direct: "平台直唤",
  alias_direct: "称呼直唤",
  copied_at: "复制的@",
  continuation: "续聊",
  alias_mention: "称呼提及",
  alias_mentioned: "称呼提及",
  candidate: "候选观察",
  host_interaction: "宿主互动",
  // 场景
  direct_address: "直接点名",
  reply_to_bot: "回复机器人",
  active_continuation: "主动续聊",
  social_response: "社交回应",
  ambient_contribution: "氛围跟一句",
  task_request: "任务请求",
  direct_interaction: "直接互动",
  // 行动 / 意图
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
  // 归属
  user: "用户",
  bot: "机器人",
  group: "群",
  ambiguous: "归属不明",
  interaction_partner: "互动对象",
  platform_mention: "平台提及",
  multi_mention: "多人提及",
  multi_name_call: "多名点名",
  // 参与义务 / 结果
  speak: "开口",
  silence: "沉默",
  direct_required: "必须回应",
  open_optional: "可选开口",
  none: "无义务",
  sent: "已发送",
  participation_speak: "参与开口",
  participation_silence: "参与决定沉默",
  decision_ignore: "决策忽略",
  model_silence: "模型选择沉默",
  soft_speak_contract: "软开口契约",
  empty_topic: "空话题",
  empty_delivery: "空投递",
  bypassed_trigger: "触发已旁路",
  copied_at_bypassed: "复制@旁路",
  copied_plain_at: "复制@提示",
  open_send_budget_exhausted: "开放发送额度用尽",
  generation_budget_exhausted: "生成额度用尽",
  generation_error: "生成失败",
  repair_error: "修复失败",
  repair_silence: "修复后仍沉默",
  // 戳一戳 / 压力
  poke_direct: "被戳本人",
  poke_bystander: "旁观跟戳",
  poke_bystander_hostile: "旁观敌意跟戳",
  poke_boundary_silence: "戳一戳边界沉默",
  poke_spam: "暴戳",
  pressure_normal: "压力正常",
  pressure_nudge: "轻度催促",
  pressure_pester: "反复纠缠",
  pressure_after_boundary: "边界后压力",
  pressure_excluded: "压力排除",
  pressure_excluded_reply: "回复排除压力",
  pressure_reset_contentful: "有内容重置压力",
  // 抑制 / 动机
  "inhibit:ambiguous_target": "抑制：目标不明",
  "inhibit:passing_alias_mention": "抑制：路过提及",
  "inhibit:owned_by_other_user": "抑制：话题属他人",
  "inhibit:empty_echo": "抑制：空复读",
  "inhibit:avoid_monopoly": "抑制：避免霸麦",
  "motive:help_when_concrete": "动机：具体求助",
  no_open_motive: "无开口动机",
  no_personal_memory: "无个人记忆",
  // 任务 / 能力 / 护栏
  supported: "已支持",
  unsupported: "不支持",
  unknown: "未知",
  needs_information: "缺信息",
  resolver_missing: "解析器缺失",
  resolver_none: "解析为空",
  resolver_invalid: "解析无效",
  accepted: "通过",
  schedule_failed: "记忆调度失败",
  vision: "看图",
  vision_disabled: "看图关闭",
  // 组装字段
  act: "行动",
  media: "媒体",
  delay: "延迟",
  segments: "分段",
  poke_only: "仅回戳",
};

const MODULE_TITLE = {
  decision: "决策",
  runtime: "运行",
  config: "配置",
  future: "扩展",
};

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
  contextList: document.getElementById("context-list"),
  traceList: document.getElementById("trace-list"),
  traceMeta: document.getElementById("trace-meta"),
  crumbCurrent: document.getElementById("crumb-current"),
  runtimePaused: document.getElementById("runtime-paused"),
  runtimeHealth: document.getElementById("runtime-health"),
  runtimeGroups: document.getElementById("runtime-groups"),
};

const state = {
  paused: false,
  selectedId: null,
  groups: [],
  allItems: [],
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

function hasCjk(value) {
  return /[\u3400-\u9fff]/.test(value);
}

function translateToken(token) {
  const raw = String(token || "").trim();
  if (!raw) return "";
  if (TERM[raw]) return TERM[raw];
  const lower = raw.toLowerCase();
  if (TERM[lower]) return TERM[lower];
  if (raw.startsWith("scene:")) {
    return `场景：${translateToken(raw.slice(6))}`;
  }
  if (raw.startsWith("guard_rejected:")) {
    const codes = raw
      .slice("guard_rejected:".length)
      .split(",")
      .map((part) => translateToken(part))
      .filter(Boolean);
    return codes.length ? `护栏拒绝：${codes.join("、")}` : "护栏拒绝";
  }
  if (raw.startsWith("resolver_error:")) {
    return `解析出错：${raw.slice("resolver_error:".length)}`;
  }
  if ((raw.includes("=") || raw.includes(";")) && !hasCjk(raw) && !raw.includes(" ")) {
    return raw
      .split(";")
      .map((chunk) => {
        if (!chunk.includes("=")) {
          return translateToken(chunk);
        }
        const [key, ...rest] = chunk.split("=");
        const value = rest.join("=");
        if (key === "delay") {
          const seconds = Number(value);
          if (!Number.isNaN(seconds)) {
            return `延迟 ${seconds.toFixed(2)} 秒`;
          }
        }
        if (key === "media") {
          return value === "0" || !value
            ? "无媒体"
            : `媒体 ${translateToken(value)}`;
        }
        if (key === "segments") {
          return `${value} 段`;
        }
        if (key === "act") {
          return `行动：${translateToken(value)}`;
        }
        if (!value) return translateToken(key);
        return `${translateToken(key)}：${translateToken(value)}`;
      })
      .filter(Boolean)
      .join(" · ");
  }
  if (raw.includes(":") && !hasCjk(raw)) {
    return raw
      .split(":")
      .map((part) => translateToken(part))
      .join(" · ");
  }
  return raw;
}

function describeValue(value, stateKey = "") {
  const raw = String(value ?? "").trim();
  if (!raw) return "—";
  if (stateKey === "RECALL" && /^\d+$/.test(raw)) {
    return `${raw} 条`;
  }
  if (stateKey === "PLAN" || hasCjk(raw)) {
    return raw;
  }
  if (raw.includes(",") && !hasCjk(raw)) {
    return raw
      .split(",")
      .map((part) => translateToken(part))
      .filter(Boolean)
      .join(" · ");
  }
  return translateToken(raw);
}

function setModule(name) {
  document.querySelectorAll(".nav-item").forEach((tab) => {
    tab.classList.toggle("is-active", tab.dataset.module === name);
  });
  document.querySelectorAll("[data-module-panel]").forEach((panel) => {
    panel.hidden = panel.dataset.modulePanel !== name;
  });
  if (els.crumbCurrent) {
    els.crumbCurrent.textContent = MODULE_TITLE[name] || name;
  }
}

function mergeGroups(...lists) {
  const seen = new Set();
  const merged = [];
  for (const list of lists) {
    for (const raw of list || []) {
      const groupId = String(raw || "").trim();
      if (!groupId || seen.has(groupId)) continue;
      seen.add(groupId);
      merged.push(groupId);
    }
  }
  return merged;
}

function fillGroupFilter(groups) {
  state.groups = mergeGroups(groups);
  const current = els.filterGroup.value;
  els.filterGroup.innerHTML =
    '<option value="">全部</option>' +
    state.groups
      .map(
        (groupId) =>
          `<option value="${escapeHtml(groupId)}">${escapeHtml(groupId)}</option>`,
      )
      .join("");
  if (current && state.groups.includes(current)) {
    els.filterGroup.value = current;
  } else {
    els.filterGroup.value = "";
  }
}

function applyFilters() {
  const groupId = els.filterGroup.value;
  const outcome = els.filterOutcome.value || "all";
  let items = state.allItems.slice();
  if (groupId) {
    items = items.filter((item) => String(item.group_id) === groupId);
  }
  if (outcome === "sent") {
    items = items.filter((item) => Boolean(item.sent));
  } else if (outcome === "silent") {
    items = items.filter((item) => !item.sent);
  }
  if (
    state.selectedId &&
    !items.some((item) => item.decision_id === state.selectedId)
  ) {
    state.selectedId = null;
    renderTrace(null);
  }
  renderDecisionList(items);
}

function renderStatus(payload) {
  state.paused = Boolean(payload.paused);
  els.badge.textContent = state.paused ? "已暂停" : "观察中";
  els.badge.dataset.tone = state.paused ? "warn" : "ok";
  els.pauseToggle.textContent = state.paused ? "恢复" : "暂停";
  els.runtimePaused.textContent = state.paused ? "暂停观察" : "正在观察";
  els.runtimeHealth.textContent = payload.config_health || "—";
  const bootstrapped = payload.bootstrapped || [];
  els.runtimeGroups.textContent = String(bootstrapped.length);
  fillGroupFilter(mergeGroups(state.groups, bootstrapped));
}

function pathLine(item) {
  const outcome = item.sent
    ? "发送"
    : describeValue(item.end_reason || "silence");
  const parts = [
    describeValue(item.trigger || "—"),
    describeValue(item.scene || "—"),
    describeValue(item.act || item.intent || "—"),
    outcome,
  ];
  return parts.join(" → ");
}

function renderDecisionList(items) {
  els.decisionCount.textContent = String(items.length);
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
        <span class="decision-main">
          <span class="decision-top">
            <span class="tag" data-tone="${tone}">${item.sent ? "发送" : "沉默"}</span>
            <span class="muted">${escapeHtml(formatTime(item.timestamp))}</span>
            <span class="muted">群 ${escapeHtml(item.group_id)}</span>
          </span>
          <span class="decision-path">${escapeHtml(pathLine(item))}</span>
          <span class="decision-id">${escapeHtml(shortId(item.decision_id))}</span>
        </span>
      </button>`;
    })
    .join("");
}

function renderContext(items) {
  if (!items || !items.length) {
    els.contextList.className = "context-list muted";
    els.contextList.textContent = "附近没有已记录的聊天。";
    return;
  }
  els.contextList.className = "context-list";
  els.contextList.innerHTML = items
    .map((item) => {
      const role = item.is_bot ? "bot" : "user";
      const marks = [
        item.is_focus ? "is-focus" : "",
        item.is_reply ? "is-reply" : "",
      ]
        .filter(Boolean)
        .join(" ");
      const badge = item.is_focus
        ? '<span class="tag" data-tone="ok">触发</span>'
        : item.is_reply
          ? '<span class="tag" data-tone="ok">回复</span>'
          : "";
      return `<div class="context-item ${role} ${marks}">
        <div class="context-top">
          <strong>${escapeHtml(item.sender_name || "—")}</strong>
          ${badge}
          <span class="muted small">${escapeHtml(formatTime(item.timestamp))}</span>
        </div>
        <div class="context-text">${escapeHtml(item.text || "—")}</div>
      </div>`;
    })
    .join("");
}

function renderTrace(payload) {
  if (!payload) {
    els.pathSummary.className = "path-summary muted";
    els.pathSummary.textContent = "选择一条决策查看路径。";
    els.traceMeta.textContent = "";
    els.contextList.className = "context-list muted";
    els.contextList.textContent = "选择决策后显示附近聊天。";
    els.traceList.innerHTML = "";
    return;
  }
  els.pathSummary.className = "path-summary";
  const endLabel = describeValue(payload.end_reason || "—");
  els.pathSummary.innerHTML = `
    <div class="path-main">${escapeHtml(pathLine(payload))}</div>
    <div class="muted small">终态 ${escapeHtml(endLabel)} · ${payload.sent ? "已发送" : "未发送"}</div>
  `;
  els.traceMeta.textContent = `群 ${payload.group_id}`;
  renderContext(payload.context || []);
  const stages = payload.stages || [];
  els.traceList.innerHTML = stages
    .map((stage) => {
      const key = String(stage.state || "");
      const label = LABEL[key] || key;
      const mark = HIGHLIGHT.has(key) ? " is-key" : "";
      const rawReason = String(stage.reason || "");
      const nice = describeValue(rawReason, key);
      const title =
        nice !== rawReason && rawReason
          ? ` title="${escapeHtml(rawReason)}"`
          : "";
      return `<li class="trace-item${mark}">
        <span class="trace-state">${escapeHtml(label)}</span>
        <span class="trace-reason"${title}>${escapeHtml(nice)}</span>
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
  // Always fetch the unfiltered page, then filter in the UI.
  // Group options come from the decision ledger, not only in-memory bootstraps.
  const payload = await bridge.apiGet("decisions", {
    limit: 50,
    outcome: "all",
  });
  state.allItems = payload.items || [];
  const itemGroups = state.allItems.map((item) => item.group_id);
  fillGroupFilter(mergeGroups(payload.groups, itemGroups, state.groups));
  applyFilters();
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

document.querySelectorAll(".nav-item").forEach((tab) => {
  tab.addEventListener("click", () => setModule(tab.dataset.module));
});

els.refresh.addEventListener("click", () => {
  reloadAll().catch((error) => showError(error.message || String(error)));
});
els.pauseToggle.addEventListener("click", () => {
  togglePause().catch((error) => showError(error.message || String(error)));
});
els.filterGroup.addEventListener("change", () => {
  try {
    applyFilters();
  } catch (error) {
    showError(error.message || String(error));
  }
});
els.filterOutcome.addEventListener("change", () => {
  try {
    applyFilters();
  } catch (error) {
    showError(error.message || String(error));
  }
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
