import { governedAction } from "../components/command-dialog.js";
import { button, element } from "../components/dom.js";
import {
  cognitionStateLabel,
  formatTimestamp,
  formatTraceDuration,
  strategySummary,
  traceHasCognitionFailure,
  traceIsObserved,
  traceResultHeadline,
  traceResultReason,
  traceResultState,
  traceWouldReply,
} from "../components/presenters.js";
import { controlVersion } from "../components/projection.js";
import { renderMessageContent } from "../components/message.js";

const FILTERS = Object.freeze([
  ["all", "全部消息"],
  ["would_reply", "会回复"],
  ["observed", "继续观察"],
  ["external", "外部能力"],
  ["failed", "异常"],
]);

const FAILED_DELIVERIES = new Set(["FAILED", "UNKNOWN"]);

const DELIVERY_TONES = Object.freeze({
  SENT: "ok",
  SILENT: "neutral",
  OBSERVED: "shadow",
  HANDED_OFF: "info",
  FAILED: "danger",
  UNKNOWN: "warning",
  DEFERRED: "warning",
  PLANNING: "info",
  READY: "info",
  RECEIVED: "neutral",
  BLOCKED_BY_SHADOW: "shadow",
});

const runtimeViewState = {
  filter: "all",
  query: "",
  limit: 16,
};

function initials(name) {
  const normalized = String(name || "群成员").trim();
  return normalized ? [...normalized][0] : "群";
}

function participantAvatar(actor = {}, size = "normal") {
  return element("span", {
    className: `participant-avatar participant-avatar-${size}`,
    text: initials(actor.display_name),
    dataset: { avatarRef: actor.avatar_ref },
    attrs: { "aria-hidden": "true" },
  });
}

function deliveryTone(status) {
  return DELIVERY_TONES[String(status || "").toUpperCase()] || "neutral";
}

function traceSearchText(item) {
  const summary = item.summary || {};
  return [
    summary.actor?.display_name,
    summary.message?.summary,
    summary.route?.label,
    summary.route?.reason,
    summary.understanding?.summary,
    cognitionStateLabel(summary.understanding?.status),
    strategySummary(summary),
    traceResultHeadline(summary),
    traceResultReason(summary),
    summary.decision?.label,
    ...(summary.decision?.reasons || []),
    summary.relationship?.kind,
    summary.relationship?.reason,
    summary.relationship?.stage,
    summary.delivery?.label,
  ].join(" ").toLowerCase();
}

function traceRow(item) {
  const summary = item.summary || {};
  const actor = summary.actor || {};
  const delivery = summary.delivery || {};
  const row = element("tr", {
    className: "trace-row",
    dataset: {
      entityRef: item.entity_ref,
      projection: "traces",
      tone: deliveryTone(delivery.status),
    },
    attrs: {
      tabindex: "0",
      role: "button",
      "aria-label": `查看 ${actor.display_name || "群成员"} 的消息链路`,
    },
  }, [
    element("td", { className: "trace-time", attrs: { "data-label": "时间" } }, [
      element("time", { text: formatTimestamp(summary.timing?.received_at || item.as_of) }),
      element("small", {
        text: `链路历时 ${formatTraceDuration(summary.timing?.total_ms)}`,
        attrs: { title: "从消息进入 Groupmate 到最后一个处理阶段更新" },
      }),
    ]),
    element("td", { attrs: { "data-label": "收到的消息" } }, [
      element("div", { className: "trace-message" }, [
        participantAvatar(actor),
        element("span", { className: "trace-message-copy" }, [
          element("strong", { text: actor.display_name || "群成员" }),
          renderMessageContent(summary.message, { compact: true }),
        ]),
      ]),
    ]),
    element("td", { attrs: { "data-label": "处理路径" } }, [
      element("strong", { className: "trace-primary", text: summary.route?.label || "等待路由" }),
      element("small", { className: "two-line", text: summary.route?.reason || "—" }),
    ]),
    element("td", { attrs: { "data-label": "Groupmate 的理解" } }, [
      element("span", { className: "two-line", text: summary.understanding?.summary || "等待理解" }),
      ...(traceHasCognitionFailure(summary)
        ? [element("small", { className: "trace-warning", text: cognitionStateLabel(summary.understanding?.status) })]
        : []),
    ]),
    element("td", { attrs: { "data-label": "决定" } }, [
      element("strong", { className: "trace-primary", text: traceResultHeadline(summary) }),
      element("small", {
        className: "two-line",
        text: traceResultState(summary),
      }),
      element("small", { className: "two-line", text: traceResultReason(summary) }),
    ]),
    element("td", { attrs: { "data-label": "最终结果" } }, [
      element("span", {
        className: "trace-result",
        text: delivery.label || "等待处理",
        attrs: { "data-tone": deliveryTone(delivery.status) },
      }),
    ]),
  ]);
  row.addEventListener("keydown", (keyboardEvent) => {
    if (keyboardEvent.key === "Enter" || keyboardEvent.key === " ") row.click();
  });
  return row;
}

function filterTraces(items, state) {
  const query = state.query.trim().toLowerCase();
  return items.filter((item) => {
    const summary = item.summary || {};
    const status = String(summary.delivery?.status || "").toUpperCase();
    const external = status === "HANDED_OFF" || summary.route?.owner === "EXTERNAL_PLUGIN";
    const failed = FAILED_DELIVERIES.has(status) || traceHasCognitionFailure(summary);
    const matchesStatus = (
      state.filter === "all"
      || (state.filter === "failed" && failed)
      || (state.filter === "external" && external)
      || (state.filter === "would_reply" && !failed && !external && traceWouldReply(summary))
      || (state.filter === "observed" && !failed && !external && traceIsObserved(summary))
    );
    return matchesStatus && (!query || traceSearchText(item).includes(query));
  });
}

function messageBrowser(items, refreshData) {
  const state = runtimeViewState;
  const body = element("tbody");
  const count = element("span", { className: "result-count" });
  const empty = element("div", {
    className: "console-empty",
    text: "还没有可展示的群消息。收到下一条消息后，这里会按完整链路显示。",
    attrs: { hidden: "" },
  });
  const loadMore = button("加载更多", { className: "console-load-more" });
  const filters = element("div", {
    className: "trace-filters",
    attrs: { role: "tablist", "aria-label": "筛选消息结果" },
  });

  function refresh() {
    const matched = filterTraces(items, state);
    const visible = matched.slice(0, state.limit);
    body.replaceChildren(...visible.map(traceRow));
    count.textContent = `${matched.length} 条消息`;
    empty.hidden = visible.length > 0;
    loadMore.hidden = visible.length >= matched.length;
    filters.querySelectorAll("button").forEach((node) => {
      const active = node.dataset.filter === state.filter;
      node.classList.toggle("is-active", active);
      node.setAttribute("aria-selected", String(active));
    });
  }

  for (const [value, label] of FILTERS) {
    filters.append(button(label, {
      className: "trace-filter",
      dataset: { filter: value },
      attrs: { role: "tab", "aria-selected": "false" },
      onClick: () => {
        state.filter = value;
        state.limit = 16;
        refresh();
      },
    }));
  }
  const search = element("input", {
    attrs: {
      type: "search",
      placeholder: "搜索成员、消息或处理结果",
      "aria-label": "搜索消息链路",
    },
  });
  search.value = state.query;
  search.addEventListener("input", () => {
    state.query = search.value;
    state.limit = 16;
    refresh();
  });
  loadMore.addEventListener("click", () => {
    state.limit += 16;
    refresh();
  });

  const refreshButton = button("立即刷新", {
    className: "runtime-refresh",
    attrs: { "aria-label": "立即刷新运行中心数据" },
    onClick: async () => {
      if (typeof refreshData !== "function" || refreshButton.disabled) return;
      refreshButton.disabled = true;
      refreshButton.textContent = "刷新中…";
      try {
        await refreshData();
      } finally {
        refreshButton.disabled = false;
        refreshButton.textContent = "立即刷新";
      }
    },
  });

  const browser = element("section", { className: "runtime-console", attrs: { "aria-label": "消息链路" } }, [
    element("header", { className: "console-heading" }, [
      element("div", {}, [
        element("span", { text: "实时事件流" }),
        element("h2", { text: "消息链路" }),
      ]),
      element("div", { className: "console-heading-actions" }, [count, refreshButton]),
    ]),
    element("div", { className: "console-toolbar" }, [
      filters,
      element("label", { className: "console-search" }, [search]),
    ]),
    element("div", { className: "console-table-wrap" }, [
      element("table", { className: "trace-table" }, [
        element("thead", {}, [
          element("tr", {}, [
            "时间",
            "收到的消息",
            "处理路径",
            "Groupmate 的理解",
            "决定",
            "最终结果",
          ].map((label) => element("th", { text: label }))),
        ]),
        body,
      ]),
    ]),
    empty,
    loadMore,
  ]);
  refresh();
  return browser;
}

function historicMode(runtime) {
  const current = [...(runtime?.items || [])].reverse().find((item) => item.summary?.runtime_mode);
  return current?.summary?.runtime_mode || "OFF";
}

export function runtimeStatus(bootstrap, runtime) {
  const configured = bootstrap?.configured_runtime_mode || historicMode(runtime);
  const effective = bootstrap?.effective_runtime_mode || configured;
  return {
    configured,
    effective,
    running: bootstrap?.runtime_state === "RUNNING" && effective !== "OFF",
    mismatch: configured !== effective,
  };
}

function isPaused(runtime) {
  return (runtime?.items || []).some((item) => item.summary?.paused === true);
}

function modeCopy(mode, ready, paused, personaName) {
  if (paused) return ["已暂停", `${personaName} 暂停处理新消息；NapCat 与 AstrBot 的到达事实仍会保留。`];
  if (mode === "OFF") return [`${personaName} 当前未运行`, "消息不会进入闲聊判断链路。完成配置并启用后才会开始处理。"];
  if (!ready) return ["尚未就绪", `配置还不完整，${personaName} 暂时不会参与群聊。`];
  if (mode === "SHADOW") return ["SHADOW 正在观察", "会完成理解和参与判断，但不会向群里发送消息。"];
  return [`${personaName} 正在运行`, "符合条件的回复会经过治理后发送到群里。"];
}

function modeBanner(bootstrap, runtime, items, expectedVersion, command) {
  const status = runtimeStatus(bootstrap, runtime);
  const mode = status.effective;
  const ready = bootstrap?.runtime_ready ?? mode !== "OFF";
  const paused = isPaused(runtime);
  const persona = bootstrap?.resolved_persona || {};
  const personaName = persona.name || "Groupmate";
  const [title, description] = modeCopy(mode, ready, paused, personaName);
  const groupmateCount = items.filter((item) => item.summary?.route?.owner === "GROUPMATE").length;
  const sentCount = items.filter((item) => item.summary?.delivery?.status === "SENT").length;
  const wouldReplyCount = items.filter((item) => traceWouldReply(item.summary)).length;
  const cognitionFailureCount = items.filter((item) => traceHasCognitionFailure(item.summary)).length;
  const children = [
    element("div", { className: "mode-copy" }, [
      element("span", { text: "运行概览" }),
      element("strong", { text: title }),
      element("small", { text: description }),
      ...(persona.preset_label
        ? [element("small", { className: "runtime-persona", text: `当前人格：${persona.preset_label}` })]
        : []),
      ...(status.mismatch
        ? [element("p", {
          className: "runtime-blockers",
          text: `配置为 ${status.configured}，实际仍以 ${status.effective} 运行；重载插件后生效。`,
        })]
        : []),
      ...(bootstrap?.runtime_blockers?.length
        ? [element("p", { className: "runtime-blockers", text: bootstrap.runtime_blockers.join("；") })]
        : []),
    ]),
    element("dl", { className: "mode-facts" }, [
      element("div", {}, [element("dt", { text: "已收到" }), element("dd", { text: String(items.length) })]),
      element("div", {}, [element("dt", { text: "进入 Groupmate" }), element("dd", { text: String(groupmateCount) })]),
      element("div", {}, [element("dt", { text: "会回复" }), element("dd", { text: String(wouldReplyCount) })]),
      element("div", {}, [element("dt", { text: "已发送" }), element("dd", { text: String(sentCount) })]),
      element("div", {}, [element("dt", { text: "认知异常" }), element("dd", { text: String(cognitionFailureCount) })]),
    ]),
  ];
  if (mode !== "OFF" && ready) {
    const control = governedAction(paused ? "恢复运行" : "暂停运行", {
      type: "pause",
      expected_version: expectedVersion,
      payload: { paused: !paused },
    }, command, { danger: !paused });
    control.classList.add("mode-action");
    children.push(control);
  }
  return element("section", {
    className: "mode-banner",
    dataset: {
      mode,
      configuredMode: status.configured,
      paused: String(paused),
      ready: String(ready),
    },
  }, children);
}

function chainGuide(health) {
  const degraded = health?.degraded === true;
  return element("div", { className: "runtime-overview-grid" }, [
    element("section", { className: "dashboard-panel chain-guide" }, [
      element("span", { className: "panel-eyebrow", text: "数据链路" }),
      element("h2", { text: "NapCat → AstrBot → Groupmate → 群聊" }),
      element("p", { text: "外部命令和视频解析插件由 AstrBot 优先处理；普通闲聊才进入 Groupmate 的理解与判断。" }),
    ]),
    element("section", { className: "dashboard-panel sync-guide", attrs: { "data-tone": degraded ? "warning" : "ok" } }, [
      element("span", { className: "panel-eyebrow", text: "页面数据" }),
      element("h2", { text: degraded ? "实时连接降级" : "事件流已连接" }),
      element("p", { text: degraded ? "页面将定时刷新，消息处理本身不受影响。" : "新消息及后续判断会持续更新在同一行。" }),
    ]),
  ]);
}

export function renderRuntime(select, command, refreshData) {
  const bootstrap = select("bootstrap");
  const runtime = select("runtime");
  const traces = select("traces");
  const health = select("health");
  const items = [...(traces?.items || [])].sort((left, right) => (
    Number(right.summary?.timing?.received_at || right.as_of || 0)
    - Number(left.summary?.timing?.received_at || left.as_of || 0)
  ));
  const expectedVersion = controlVersion(select("governance"));

  return element("div", { className: "workspace-stack runtime-workspace" }, [
    modeBanner(bootstrap, runtime, items, expectedVersion, command),
    messageBrowser(items, refreshData),
    chainGuide(health),
  ]);
}
