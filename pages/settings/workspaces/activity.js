import { element } from "../components/dom.js";
import { activityTable, statusNarrative, workspaceSection } from "../components/projection.js";

export function renderActivity(select, command) {
  void command;
  const activity = select("activity");
  const scenes = select("scenes");
  const tasks = select("tasks");
  const items = [
    ...(activity?.items || []).map((item) => ({ ...item, projection: "activity" })),
    ...(scenes?.items || []).map((item) => ({ ...item, projection: "scenes" })),
    ...(tasks?.items || []).map((item) => ({ ...item, projection: "tasks" })),
  ].sort((left, right) => Number(right.as_of || 0) - Number(left.as_of || 0));

  const search = element("input", { attrs: { type: "search", placeholder: "搜索事件或结果", "aria-label": "筛选事件" } });
  const source = element("select", { attrs: { "aria-label": "事件类型" } }, [
    element("option", { text: "全部事件", attrs: { value: "" } }),
    element("option", { text: "群聊活动", attrs: { value: "activity" } }),
    element("option", { text: "决策判断", attrs: { value: "scenes" } }),
    element("option", { text: "任务交付", attrs: { value: "tasks" } }),
  ]);
  const table = activityTable(items);
  const applyFilter = () => {
    const query = search.value.trim().toLowerCase();
    table.querySelectorAll("tbody tr").forEach((row) => {
      row.hidden = Boolean((source.value && row.dataset.source !== source.value)
        || (query && !String(row.dataset.search || "").toLowerCase().includes(query)));
    });
  };
  search.addEventListener("input", applyFilter);
  source.addEventListener("change", applyFilter);
  const toolbar = element("div", { className: "filter-toolbar" }, [
    element("label", { className: "search-field" }, [element("span", { text: "筛选事件" }), search]),
    source,
    element("span", { className: "result-count", text: `${items.length} 条记录` }),
  ]);

  return element("div", { className: "workspace-stack" }, [
    statusNarrative([activity, scenes, tasks]),
    workspaceSection("事件流", "按时间检查参与判断、任务与交付；点击查看结构化证据。", element("div", { className: "activity-browser" }, [toolbar, table])),
  ]);
}
