import { governedAction } from "../components/command-dialog.js";
import { element } from "../components/dom.js";
import { controlVersion, projectionList, statusNarrative, workspaceSection } from "../components/projection.js";

const SOCIAL_AREAS = [
  "身份",
  "关系维度",
  "印象",
  "经历",
  "事实",
  "未完事项",
  "承诺",
  "群文化",
  "治理历史",
];

export function renderPeople(select, command) {
  const people = select("people");
  const culture = select("culture");
  const governance = select("governance");
  const expectedVersion = controlVersion(governance);
  const actions = (item) => [
    governedAction("复核证据", {
      type: "review",
      expected_version: expectedVersion,
      payload: { entity_ref: item.evidence_refs?.[0] || item.entity_ref, decision: "needs_more_evidence" },
    }, command),
    governedAction("纠正", {
      type: "correct",
      expected_version: expectedVersion,
      payload: { entity_ref: item.entity_ref },
    }, command, { danger: true, fields: [
      { name: "correction", label: "纠正内容（JSON）", format: "json", defaultValue: "{\n  \"status\": \"reviewed\"\n}" },
    ] }),
    governedAction("遗忘", {
      type: "forget",
      expected_version: expectedVersion,
      payload: { entity_ref: item.entity_ref },
    }, command, { danger: true }),
  ];
  const identityAction = governedAction("建立身份关联", {
    type: "link",
    expected_version: expectedVersion,
    payload: {},
  }, command, { danger: true, fields: [
    { name: "source_ref", label: "源身份 Entity ref" },
    { name: "target_ref", label: "目标身份 Entity ref" },
    { name: "allowed_data_types", label: "允许传递的数据类型（逗号分隔）", format: "csv" },
  ] });

  return element("div", { className: "workspace-stack" }, [
    statusNarrative([people, culture, governance]),
    workspaceSection("社会档案", "按证据组织，不把亲密度当作权限。", element("p", { text: SOCIAL_AREAS.join(" · ") }), [identityAction]),
    workspaceSection("成员与记忆", "身份、关系、印象、经历、事实、未完事项与承诺。", projectionList(people, { actions })),
    workspaceSection("群文化", "只有重复出现或管理员确认的文化事实进入这里。", projectionList(culture)),
    workspaceSection("治理历史", "所有纠正、遗忘和身份关联都保留审计事件。", projectionList(governance)),
  ]);
}
