import { governedAction } from "../components/command-dialog.js";
import { element } from "../components/dom.js";
import { controlVersion, projectionList, statusNarrative, workspaceSection } from "../components/projection.js";

const CAUSAL_STAGES = [
  "结构化 Observation",
  "候选意图",
  "Governor",
  "ActionPlan",
  "Task",
  "Delivery Part",
  "故障",
];

export function renderActivity(select, command) {
  const activity = select("activity");
  const scenes = select("scenes");
  const tasks = select("tasks");
  const expectedVersion = controlVersion(select("governance"));
  const taskActions = (item) => [
    governedAction("取消", {
      type: "cancel",
      expected_version: expectedVersion,
      payload: { entity_ref: item.entity_ref },
    }, command, { danger: true }),
  ];

  return element("div", { className: "workspace-stack" }, [
    statusNarrative([activity, scenes, tasks]),
    workspaceSection("因果时间线", CAUSAL_STAGES.join(" → "), projectionList(activity, { timeline: true })),
    workspaceSection("决策检查", "结构化场景、候选与硬约束的 Projection 摘要。", projectionList(scenes, { timeline: true })),
    workspaceSection("任务与交付", "Task、真实 Provider Event、Delivery Part 和故障状态。", projectionList(tasks, { timeline: true, actions: taskActions })),
  ]);
}
