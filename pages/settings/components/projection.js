import { button, element } from "./dom.js";
import { formatTimestamp, itemTone, kindLabel, visibleFacts } from "./presenters.js";

export function workspaceSection(title, description, content, actions = [], options = {}) {
  const heading = element("div", { className: "section-heading" }, [
    element("div", {}, [
      element("h2", { text: title }),
      description ? element("p", { text: description }) : null,
    ]),
    actions.length ? element("div", { className: "section-actions" }, actions) : null,
  ]);
  return element("section", {
    className: `workspace-section${options.className ? ` ${options.className}` : ""}`,
  }, [heading, content]);
}

export function projectionList(view, options = {}) {
  const items = [...(view?.items || [])].sort((left, right) => Number(right.as_of || 0) - Number(left.as_of || 0));
  if (!items.length) {
    return element("p", { className: "empty-state", text: options.empty || "目前没有需要展示的记录。" });
  }
  const list = element("ol", { className: options.timeline ? "event-feed event-feed-timeline" : "event-feed" });
  for (const item of items.slice(0, options.limit || items.length)) {
    const facts = visibleFacts(item.summary || {}).slice(0, options.factLimit || 3);
    const inspect = button("查看详情", {
      className: "button button-quiet",
      dataset: { entityRef: item.entity_ref, projection: options.projection || view.projection },
      attrs: { "aria-label": `查看${kindLabel(item.kind)}详情` },
    });
    const extraActions = options.actions ? options.actions(item, view) : [];
    list.append(element("li", { attrs: { "data-tone": itemTone(item) } }, [
      element("span", { className: "event-marker", attrs: { "aria-hidden": "true" } }),
      element("div", { className: "event-content" }, [
        element("div", { className: "projection-item-heading" }, [
          element("strong", { text: options.label ? options.label(item) : kindLabel(item.kind) }),
          element("time", { text: formatTimestamp(item.as_of) }),
        ]),
        facts.length
          ? element("dl", { className: "fact-line" }, facts.map((fact) => element("div", {}, [
            element("dt", { text: fact.label }),
            element("dd", { text: fact.value }),
          ])))
          : element("p", { className: "event-description", text: "这条事件没有额外的可见详情。" }),
        element("div", { className: "item-actions" }, [inspect, ...extraActions]),
      ]),
    ]));
  }
  return list;
}

export function compactActivity(items, empty = "还没有近期活动。") {
  if (!items.length) return element("p", { className: "empty-state", text: empty });
  const list = element("ol", { className: "compact-activity" });
  for (const item of items.slice(0, 7)) {
    const facts = visibleFacts(item.summary || {});
    list.append(element("li", { attrs: { "data-tone": itemTone(item) } }, [
      element("time", { text: formatTimestamp(item.as_of) }),
      element("span", { className: "event-icon", attrs: { "aria-hidden": "true" } }),
      element("div", {}, [
        element("strong", { text: kindLabel(item.kind) }),
        element("p", { text: facts[0]?.value || "已写入运行记录" }),
      ]),
      element("span", {
        className: "event-result",
        text: facts.find((fact) => ["disposition", "outcome", "task_status", "result_status", "status"].includes(fact.key))?.value || "已记录",
      }),
    ]));
  }
  return list;
}

export function activityTable(items, options = {}) {
  if (!items.length) return element("p", { className: "empty-state", text: "当前筛选条件下没有事件。" });
  const body = element("tbody");
  for (const item of items) {
    const facts = visibleFacts(item.summary || {});
    const inspect = button("查看", {
      className: "button button-quiet table-action",
      dataset: { entityRef: item.entity_ref, projection: item.projection || options.projection || "activity" },
      attrs: { "aria-label": `查看${kindLabel(item.kind)}详情` },
    });
    body.append(element("tr", {
      dataset: { source: item.projection || "activity", search: `${kindLabel(item.kind)} ${facts.map((fact) => fact.value).join(" ")}` },
    }, [
      element("td", {}, [element("time", { text: formatTimestamp(item.as_of) })]),
      element("td", {}, [
        element("span", { className: "table-event-title", text: kindLabel(item.kind) }),
        element("span", { className: "table-event-detail", text: facts[0]?.value || "已记录" }),
      ]),
      element("td", { text: item.projection === "tasks" ? "任务系统" : "群聊现场" }),
      element("td", { text: facts.find((fact) => ["disposition", "outcome", "task_status", "result_status"].includes(fact.key))?.value || "已记录" }),
      element("td", {}, [inspect]),
    ]));
  }
  const table = element("table", { className: "activity-table" }, [
    element("thead", {}, [element("tr", {}, ["时间", "事件", "参与者", "结果", ""].map((label) => element("th", { text: label }))) ]),
    body,
  ]);
  return element("div", { className: "table-scroll" }, [table]);
}

export function statusNarrative(views) {
  const existing = views.filter(Boolean);
  const stale = existing.filter((view) => view.stale);
  const latest = Math.max(0, ...existing.map((view) => Number(view.as_of || 0)));
  return element("div", { className: stale.length ? "sync-notice sync-notice-warning" : "sync-notice" }, [
    element("span", { className: "sync-dot", attrs: { "aria-hidden": "true" } }),
    element("span", {
      text: stale.length
        ? "部分数据更新较慢，页面正在自动追赶。"
        : latest
          ? `数据已同步 · 更新于 ${formatTimestamp(latest)}`
          : "正在等待第一批运行数据。",
    }),
  ]);
}

export function projectionVersion(view) {
  return Number(view?.projection_version || 0);
}

export function controlVersion(view) {
  return Math.max(0, ...(view?.items || []).map((item) => Number(item.summary?.control_version || 0)));
}

export function publishedConfigVersion(view) {
  return Math.max(0, ...(view?.items || [])
    .filter((item) => item.summary?.status === "PUBLISHED")
    .map((item) => Number(item.summary?.config_version || 0)));
}
