import { governedAction } from "../components/command-dialog.js";
import { element } from "../components/dom.js";
import { controlVersion, projectionList, statusNarrative, workspaceSection } from "../components/projection.js";

const GOVERNANCE_AREAS = [
  "待复核",
  "纠正",
  "遗忘",
  "身份关联",
  "配置版本",
  "校准",
  "导出",
  "保留策略",
  "目标效果评估",
];

export function renderGovernance(select, command) {
  const governance = select("governance");
  const evaluation = select("evaluation");
  const expectedVersion = controlVersion(governance);
  const governanceActions = (item) => [
    governedAction("批准校准", {
      type: "approve_calibration",
      expected_version: expectedVersion,
      payload: { entity_ref: item.entity_ref },
    }, command, { danger: true }),
  ];
  const reset = governedAction("重置状态", {
    type: "reset",
    expected_version: expectedVersion,
    payload: { target: "group-runtime" },
  }, command, { danger: true });

  return element("div", { className: "workspace-stack" }, [
    statusNarrative([governance, evaluation]),
    workspaceSection("治理范围", "高影响动作必须有 Expected Version、原因和二次确认。", element("p", { text: GOVERNANCE_AREAS.join(" · ") }), [reset]),
    workspaceSection("待复核与版本", "治理事件、配置版本、校准和保留策略的审计摘要。", projectionList(governance, { actions: governanceActions })),
    workspaceSection("目标效果评估", "只展示标注与效果 Projection，不展示模型思维链。", projectionList(evaluation)),
  ]);
}
