import { button, element, textValue } from "./dom.js";

export function workspaceSection(title, description, content, actions = []) {
  const heading = element("div", { className: "section-heading" }, [
    element("div", {}, [
      element("h2", { text: title }),
      description ? element("p", { text: description }) : null,
    ]),
    actions.length ? element("div", { className: "section-actions" }, actions) : null,
  ]);
  return element("section", { className: "workspace-section" }, [heading, content]);
}

export function projectionList(view, options = {}) {
  const items = [...(view?.items || [])];
  if (!items.length) {
    return element("p", {
      className: "empty-state",
      text: options.empty || "当前 Projection 没有可展示的事实。",
    });
  }
  const list = element("ol", { className: options.timeline ? "projection-list timeline" : "projection-list" });
  for (const item of items) {
    const summary = Object.entries(item.summary || {}).map(([key, value]) =>
      element("span", { className: "fact", text: `${key}: ${textValue(value)}` }),
    );
    const inspect = button("检查", {
      className: "button button-quiet",
      dataset: {
        entityRef: item.entity_ref,
        projection: view.projection,
      },
      attrs: { "aria-label": `检查 ${item.kind || "Projection 项"}` },
    });
    const extraActions = options.actions ? options.actions(item, view) : [];
    list.append(element("li", {}, [
      element("div", { className: "projection-item-heading" }, [
        element("strong", { text: options.label ? options.label(item) : item.kind }),
        element("span", { className: "version-tag", text: `v${item.projection_version}` }),
      ]),
      element("div", { className: "fact-line" }, summary),
      element("div", { className: "item-actions" }, [inspect, ...extraActions]),
    ]));
  }
  return list;
}

export function statusNarrative(views) {
  const stale = views.filter((view) => view?.stale);
  const latest = Math.max(0, ...views.map((view) => Number(view?.projection_version || 0)));
  return element("p", {
    className: stale.length ? "status-narrative status-warning" : "status-narrative",
    text: stale.length
      ? `${stale.map((view) => view.projection).join("、")} 数据滞后；当前展示到 Projection v${latest}。`
      : `Projection 已同步，当前可见版本 v${latest}。`,
  });
}

export function projectionVersion(view) {
  return Number(view?.projection_version || 0);
}

export function controlVersion(view) {
  return Math.max(
    0,
    ...(view?.items || []).map((item) => Number(item.summary?.control_version || 0)),
  );
}

export function publishedConfigVersion(view) {
  return Math.max(
    0,
    ...(view?.items || [])
      .filter((item) => item.summary?.status === "PUBLISHED")
      .map((item) => Number(item.summary?.config_version || 0)),
  );
}
