import { governedAction } from "../components/command-dialog.js";
import { element } from "../components/dom.js";
import { valueLabel } from "../components/presenters.js";
import { compactActivity, controlVersion, projectionList, statusNarrative, workspaceSection } from "../components/projection.js";

function metric(label, value, detail, tone = "neutral") {
  return element("div", { className: "summary-card", attrs: { "data-tone": tone } }, [
    element("span", { text: label }), element("strong", { text: value }), element("small", { text: detail }),
  ]);
}

function healthItem(label, status, detail, tone = "ok") {
  return element("li", { attrs: { "data-tone": tone } }, [
    element("span", { className: "health-icon", attrs: { "aria-hidden": "true" } }),
    element("div", {}, [element("strong", { text: label }), element("small", { text: detail })]),
    element("span", { className: "health-status", text: status }),
  ]);
}

export function renderRuntime(select, command) {
  const runtime = select("runtime");
  const activity = select("activity");
  const tasks = select("tasks");
  const health = select("health");
  const expectedVersion = controlVersion(select("governance"));
  const runtimeItems = runtime?.items || [];
  const activityItems = activity?.items || [];
  const taskItems = tasks?.items || [];
  const current = runtimeItems.at(-1)?.summary || {};
  const paused = current.paused === true || runtimeItems.some((item) => item.summary?.paused === true);
  const modeItem = [...runtimeItems].reverse().find((item) => item.summary?.runtime_mode);
  const mode = current.runtime_mode || modeItem?.summary.runtime_mode || "OFF";
  const degraded = Boolean(health?.degraded || health?.stale);
  const fallbackSeconds = Number(health?.fallback_poll_seconds || 15);
  const degradedReason = (health?.degraded_reasons || []).join("；") || "部分数据更新较慢";
  const runningTasks = taskItems.filter((item) => ["RUNNING", "running", "QUEUED", "queued"].includes(item.summary?.task_status)).length;
  const recent = [...activityItems.map((item) => ({ ...item, projection: "activity" })), ...taskItems.map((item) => ({ ...item, projection: "tasks" }))]
    .sort((left, right) => Number(right.as_of || 0) - Number(left.as_of || 0));
  const controls = [governedAction(paused ? "恢复运行" : "暂停运行", {
    type: "pause", expected_version: expectedVersion, payload: { paused: !paused },
  }, command, { danger: !paused })];
  const taskActions = (item) => [governedAction("取消任务", {
    type: "cancel", expected_version: expectedVersion, payload: { entity_ref: item.entity_ref },
  }, command, { danger: true })];

  const overview = element("div", { className: "summary-grid" }, [
    metric("当前状态", paused ? "已暂停" : "运行中", valueLabel("runtime_mode", mode), paused ? "warning" : "ok"),
    metric("近期活动", String(activityItems.length), "当前可见运行记录"),
    metric("进行中任务", String(runningTasks), `共 ${taskItems.length} 条任务记录`, runningTasks ? "warning" : "neutral"),
    metric("运行健康", degraded ? "需留意" : "正常", degraded ? degradedReason : "未发现控制面退化", degraded ? "warning" : "ok"),
  ]);
  const hasFailedTask = taskItems.some((item) => String(item.summary?.task_status || "").toLowerCase() === "failed");
  const healthList = element("ul", { className: "health-grid" }, [
    healthItem("实时更新", degraded ? "降级" : "正常", degraded ? `${degradedReason}；每 ${fallbackSeconds} 秒刷新` : "连接保持正常", degraded ? "warning" : "ok"),
    healthItem("运行数据", runtime?.stale ? "追赶中" : "正常", runtime?.stale ? "展示可能稍有延迟" : "状态已同步", runtime?.stale ? "warning" : "ok"),
    healthItem("任务系统", hasFailedTask ? "有失败" : "正常", `${taskItems.length} 条可见记录`, hasFailedTask ? "danger" : "ok"),
    healthItem("消息发送", mode === "SHADOW" ? "已关闭" : "受治理", mode === "SHADOW" ? "观察模式不会向群里发送" : "发送仍受安全门控制", "neutral"),
  ]);

  return element("div", { className: "workspace-stack" }, [
    statusNarrative([runtime, activity, tasks, health]),
    workspaceSection("运行概览", "先看当前状态，再决定是否需要检查或干预。", overview, controls),
    workspaceSection("近期活动", "只展示对管理员有意义的运行变化，技术细节按需展开。", compactActivity(recent)),
    workspaceSection("任务义务", "直接请求产生的任务和真实交付状态。", projectionList(tasks, { actions: taskActions, limit: 6, empty: "当前没有任务义务。" })),
    workspaceSection("健康状态", "实时更新、运行数据、任务和发送门的实际状态。", healthList),
  ]);
}
