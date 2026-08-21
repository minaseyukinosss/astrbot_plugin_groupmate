import { governedAction } from "../components/command-dialog.js";
import { button, element } from "../components/dom.js";
import { formatTimestamp, kindLabel, valueLabel } from "../components/presenters.js";
import { controlVersion, statusNarrative } from "../components/projection.js";

const TABS = Object.freeze([
  ["all", "事件流"],
  ["evaluation", "参与判断"],
  ["scenes", "场景理解"],
  ["tasks", "任务"],
  ["alerts", "告警"],
]);

const PROJECTION_LABELS = Object.freeze({
  activity: "运行链路",
  scenes: "场景理解",
  evaluation: "SHADOW",
  tasks: "任务系统",
});

function icon(name, className = "ui-icon") {
  return element("img", { className, attrs: { src: `./assets/icons/${name}.svg`, alt: "" } });
}

function eventTone(event) {
  const summary = event.summary || {};
  const state = String(summary.outcome || summary.task_status || summary.result_status || "").toLowerCase();
  if (["failed", "unknown", "deny", "denied"].includes(state)) return "danger";
  if (["pending", "running", "queued", "defer", "deferred"].includes(state)) return "warning";
  if (event.projection === "evaluation") return "shadow";
  return "neutral";
}

function eventIntent(event) {
  const summary = event.summary || {};
  if (summary.attention?.trigger_kind) return summary.attention.trigger_kind;
  if (summary.direct_request === true) return "直接请求";
  if (summary.disposition) return valueLabel("disposition", summary.disposition);
  if (event.projection === "scenes") return "理解上下文";
  if (event.projection === "tasks") return "执行义务";
  return "链路更新";
}

function eventDecision(event) {
  const summary = event.summary || {};
  if (summary.outcome) return valueLabel("outcome", summary.outcome);
  if (summary.candidate_response) return "候选回复";
  if (summary.candidate_actions?.length) return `候选动作 ${summary.candidate_actions.length}`;
  if (summary.disposition) return valueLabel("disposition", summary.disposition);
  if (summary.task_status) return valueLabel("task_status", summary.task_status);
  return "已记录";
}

function eventResult(event) {
  const summary = event.summary || {};
  if (event.projection === "evaluation") return "仅观察";
  if (summary.result_status) return valueLabel("result_status", summary.result_status);
  if (summary.task_status) return valueLabel("task_status", summary.task_status);
  if (summary.disposition) return valueLabel("disposition", summary.disposition);
  return "已更新";
}

function eventDescription(event) {
  const summary = event.summary || {};
  return summary.focus?.summary
    || summary.focus?.content
    || summary.candidate_response
    || summary.observation?.summary
    || summary.reason_codes?.map((code) => valueLabel("reason_codes", code)).join(" · ")
    || summary.task_title
    || summary.status
    || PROJECTION_LABELS[event.projection];
}

function eventRow(event) {
  const evidenceCount = event.evidence_refs?.length || 0;
  const row = element("tr", {
    attrs: { tabindex: "0", role: "button", "aria-label": `查看 ${kindLabel(event.kind)} 详情` },
    dataset: { entityRef: event.entity_ref, projection: event.projection, tone: eventTone(event) },
  }, [
    element("td", { className: "event-time" }, [
      element("time", { text: formatTimestamp(event.as_of) }),
      element("small", { text: `#${String(event.projection_version || 0).padStart(4, "0")}` }),
    ]),
    element("td", { className: "event-main" }, [
      element("strong", { text: kindLabel(event.kind) }),
      element("small", { text: eventDescription(event) }),
    ]),
    element("td", {}, [element("span", { className: "source-chip", text: PROJECTION_LABELS[event.projection] })]),
    element("td", { text: eventIntent(event) }),
    element("td", { text: eventDecision(event) }),
    element("td", {}, [element("span", { className: "result-chip", text: eventResult(event), attrs: { "data-tone": eventTone(event) } })]),
    element("td", {}, [
      element("span", { className: "evidence-count" }, [icon("notes"), element("span", { text: String(evidenceCount) })]),
    ]),
  ]);
  row.addEventListener("keydown", (eventKey) => {
    if (eventKey.key === "Enter" || eventKey.key === " ") row.click();
  });
  return row;
}

function filterEvents(events, state) {
  const query = state.query.trim().toLowerCase();
  return events.filter((event) => {
    const matchesTab = state.tab === "all"
      || event.projection === state.tab
      || (state.tab === "alerts" && eventTone(event) === "danger");
    const matchesType = state.type === "all" || event.projection === state.type;
    const searchable = `${kindLabel(event.kind)} ${eventDescription(event)} ${eventIntent(event)} ${eventDecision(event)}`.toLowerCase();
    return matchesTab && matchesType && (!query || searchable.includes(query));
  });
}

function buildEventBrowser(events) {
  const state = { tab: "all", type: "all", query: "", limit: 8 };
  const tableBody = element("tbody");
  const empty = element("div", { className: "console-empty", text: "当前筛选下没有可见事件。", attrs: { hidden: "" } });
  const count = element("span", { className: "result-count" });
  const loadMore = button("加载更多", {
    className: "console-load-more",
    onClick: () => { state.limit += 8; refresh(); },
  });
  const tabs = element("div", { className: "console-tabs", attrs: { role: "tablist", "aria-label": "运行中心视图" } });

  function refresh() {
    const matched = filterEvents(events, state);
    const visible = matched.slice(0, state.limit);
    tableBody.replaceChildren(...visible.map(eventRow));
    count.textContent = `${matched.length} 条可见记录`;
    empty.hidden = visible.length > 0;
    loadMore.hidden = visible.length >= matched.length;
    tabs.querySelectorAll("button").forEach((tab) => {
      const active = tab.dataset.tab === state.tab;
      tab.classList.toggle("is-active", active);
      tab.setAttribute("aria-selected", String(active));
    });
  }

  for (const [value, label] of TABS) {
    tabs.append(button(label, {
      className: "console-tab",
      attrs: { role: "tab", "aria-selected": "false" },
      dataset: { tab: value },
      onClick: () => { state.tab = value; state.limit = 8; refresh(); },
    }));
  }

  const search = element("input", { attrs: { type: "search", placeholder: "搜索事件、意图或决策", "aria-label": "搜索事件" } });
  search.addEventListener("input", () => { state.query = search.value; state.limit = 8; refresh(); });
  const type = element("select", { attrs: { "aria-label": "筛选事件类型" } }, [
    element("option", { text: "全部事件", attrs: { value: "all" } }),
    ...Object.entries(PROJECTION_LABELS).map(([value, label]) => element("option", { text: label, attrs: { value } })),
  ]);
  type.addEventListener("change", () => { state.type = type.value; state.limit = 8; refresh(); });

  const browser = element("section", { className: "runtime-console", attrs: { "aria-label": "近期活动 · 事件流" } }, [
    tabs,
    element("div", { className: "console-toolbar" }, [
      element("label", { className: "console-search" }, [icon("search"), search]),
      type,
      element("span", { className: "range-chip" }, [icon("clock"), element("span", { text: "最近投影" })]),
      count,
    ]),
    element("div", { className: "console-table-wrap" }, [
      element("table", { className: "console-table" }, [
        element("thead", {}, [element("tr", {}, ["时间", "事件", "来源", "动机 / 意图", "决策 / 行动", "结果", "证据"].map((label) => element("th", { text: label }))) ]),
        tableBody,
      ]),
    ]),
    empty,
    loadMore,
  ]);
  refresh();
  return browser;
}

function modeBanner(mode, paused, decisions, expectedVersion, command) {
  const shadow = mode === "SHADOW";
  const controls = governedAction(paused ? "恢复运行" : "暂停运行", {
    type: "pause",
    expected_version: expectedVersion,
    payload: { paused: !paused },
  }, command, { danger: !paused });
  controls.classList.add("mode-action");
  return element("section", { className: "mode-banner", attrs: { "data-mode": mode } }, [
    element("div", { className: "mode-icon" }, [icon(shadow ? "eye" : "activity")]),
    element("div", { className: "mode-copy" }, [
      element("span", { text: "运行概览" }),
      element("strong", { text: shadow ? "SHADOW 观察已开启" : valueLabel("runtime_mode", mode) }),
      element("small", { text: shadow ? "正常完成注意、理解与参与判断，但不会向群内发送消息。" : "所有输出继续经过治理与发送门。" }),
    ]),
    element("dl", { className: "mode-facts" }, [
      element("div", {}, [element("dt", { text: "状态" }), element("dd", { text: paused ? "已暂停" : "运行中" })]),
      element("div", {}, [element("dt", { text: "参与判断" }), element("dd", { text: String(decisions) })]),
      element("div", {}, [element("dt", { text: "消息发送" }), element("dd", { text: shadow ? "关闭" : "受治理" })]),
    ]),
    controls,
  ]);
}

function statusPanel(mode, paused, health, taskItems) {
  const failed = taskItems.filter((item) => String(item.summary?.task_status || "").toLowerCase() === "failed").length;
  const rows = [
    ["总体验证", paused ? "已暂停" : "正常运行", paused ? "warning" : "ok"],
    ["情绪基调", mode === "SHADOW" ? "仅观察" : "已启用", "ok"],
    ["实时同步", health?.degraded ? "降级轮询" : "已连接", health?.degraded ? "warning" : "ok"],
    ["任务失败", failed ? `${failed} 条` : "无", failed ? "danger" : "ok"],
  ];
  return element("section", { className: "dashboard-panel" }, [
    element("h2", { text: "健康状态" }),
    element("ul", { className: "status-list" }, rows.map(([label, value, tone]) => element("li", {}, [
      element("span", { text: label }), element("strong", { text: value, attrs: { "data-tone": tone } }),
    ]))),
  ]);
}

function decisionPanel(evaluations) {
  const outcomes = evaluations.reduce((result, item) => {
    const key = String(item.summary?.outcome || item.summary?.disposition || "UNKNOWN").toUpperCase();
    result[key] = (result[key] || 0) + 1;
    return result;
  }, {});
  const total = Math.max(evaluations.length, 1);
  const rows = Object.entries(outcomes).length ? Object.entries(outcomes) : [["暂无判断", 0]];
  return element("section", { className: "dashboard-panel decision-panel" }, [
    element("h2", { text: "参与判断分布" }),
    element("div", { className: "decision-bars" }, rows.map(([label, value]) => element("div", {}, [
      element("span", { text: valueLabel("outcome", label) }),
      element("div", { className: "bar-track" }, [element("i", { attrs: { style: `--bar-size: ${Math.round((value / total) * 100)}%` } })]),
      element("strong", { text: String(value) }),
    ]))),
  ]);
}

function taskPanel(taskItems) {
  const rows = taskItems.slice(0, 5);
  return element("section", { className: "dashboard-panel task-panel" }, [
    element("h2", { text: "任务队列" }),
    rows.length ? element("ul", { className: "task-list" }, rows.map((item) => element("li", {
      dataset: { entityRef: item.entity_ref, projection: "tasks" },
    }, [
      element("span", {}, [element("strong", { text: kindLabel(item.kind) }), element("small", { text: eventDescription({ ...item, projection: "tasks" }) })]),
      element("b", { text: valueLabel("task_status", item.summary?.task_status || "UNKNOWN") }),
    ]))) : element("p", { className: "panel-empty", text: "当前没有任务义务。" }),
  ]);
}

function alertPanel(events, health) {
  const alerts = events.filter((event) => eventTone(event) === "danger").slice(0, 4);
  if (health?.degraded) alerts.unshift({ kind: "health.degraded", summary: { reason: (health.degraded_reasons || []).join("；") || "投影更新已降级" } });
  return element("section", { className: "dashboard-panel alert-panel" }, [
    element("h2", { text: "告警" }),
    alerts.length ? element("ul", { className: "alert-list" }, alerts.map((item) => element("li", {}, [
      icon("alert-triangle"),
      element("span", {}, [element("strong", { text: kindLabel(item.kind) }), element("small", { text: item.summary?.reason || eventDescription(item) })]),
    ]))) : element("div", { className: "all-clear" }, [icon("shield-check"), element("span", { text: "没有需要处理的告警" })]),
  ]);
}

export function renderRuntime(select, command) {
  const runtime = select("runtime");
  const activity = select("activity");
  const scenes = select("scenes");
  const tasks = select("tasks");
  const health = select("health");
  const evaluation = select("evaluation");
  const expectedVersion = controlVersion(select("governance"));
  const runtimeItems = runtime?.items || [];
  const taskItems = tasks?.items || [];
  const evaluationItems = evaluation?.items || [];
  const modeSummary = [...runtimeItems].reverse().find((item) => item.summary?.runtime_mode)?.summary || {};
  const mode = modeSummary.runtime_mode || "OFF";
  const paused = modeSummary.paused === true || runtimeItems.some((item) => item.summary?.paused === true);
  const events = [
    ...(activity?.items || []).map((item) => ({ ...item, projection: "activity" })),
    ...(scenes?.items || []).map((item) => ({ ...item, projection: "scenes" })),
    ...evaluationItems.map((item) => ({ ...item, projection: "evaluation" })),
    ...taskItems.map((item) => ({ ...item, projection: "tasks" })),
  ].sort((left, right) => Number(right.as_of || 0) - Number(left.as_of || 0));

  return element("div", { className: "workspace-stack runtime-workspace" }, [
    statusNarrative([runtime, activity, scenes, evaluation, tasks, health]),
    modeBanner(mode, paused, evaluationItems.length, expectedVersion, command),
    buildEventBrowser(events),
    element("div", { className: "dashboard-grid" }, [
      statusPanel(mode, paused, health, taskItems),
      decisionPanel(evaluationItems),
      taskPanel(taskItems),
      alertPanel(events, health),
    ]),
  ]);
}
