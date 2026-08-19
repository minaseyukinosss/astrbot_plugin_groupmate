import { governedAction } from "../components/command-dialog.js";
import { element } from "../components/dom.js";
import { controlVersion, projectionList, statusNarrative, workspaceSection } from "../components/projection.js";

export function renderRuntime(select, command) {
  const runtime = select("runtime");
  const activity = select("activity");
  const tasks = select("tasks");
  const health = select("health");
  const expectedVersion = controlVersion(select("governance"));
  const paused = (runtime?.items || []).some((item) => item.summary?.paused === true);
  const controls = [
    governedAction(paused ? "恢复" : "暂停", {
      type: "pause",
      expected_version: expectedVersion,
      payload: { paused: !paused },
    }, command, { danger: !paused }),
  ];
  const taskActions = (item) => [
    governedAction("取消任务", {
      type: "cancel",
      expected_version: expectedVersion,
      payload: { entity_ref: item.entity_ref },
    }, command, { danger: true }),
  ];
  const healthImpact = health?.degraded
    ? `控制面退化：${(health.degraded_reasons || []).join("；") || "原因未提供"}。页面将使用 ${Number(health.fallback_poll_seconds || 15)} 秒 polling。`
    : "控制面 Query 与 Projection 未报告退化。Task、Outbox 的实际故障仍以下方事件为准。";
  const healthContent = element("div", {}, [
    element("p", {
      className: health?.degraded ? "status-narrative status-warning" : "status-narrative",
      text: healthImpact,
    }),
    projectionList(health),
  ]);

  return element("div", { className: "workspace-stack" }, [
    statusNarrative([runtime, activity, tasks, health]),
    workspaceSection("当前状态", "人格在本群的实际模式与暂停状态。", projectionList(runtime), controls),
    workspaceSection("实时活动", "只展示 Event Fabric 投影的实际活动，不制造思考动画。", projectionList(activity, { timeline: true })),
    workspaceSection("任务义务", "直接请求产生的义务与真实 Provider/Delivery 状态。", projectionList(tasks, { actions: taskActions })),
    workspaceSection("运行健康", "Projection、Task、Outbox 与连接的真实退化影响。", healthContent),
  ]);
}
