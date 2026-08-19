import { governedAction } from "../components/command-dialog.js";
import { element } from "../components/dom.js";
import { projectionList, publishedConfigVersion, statusNarrative, workspaceSection } from "../components/projection.js";

const BEHAVIOR_AREAS = [
  "Constitution",
  "状态与模式",
  "注意力",
  "自主性",
  "Governor",
  "风格",
  "媒体",
  "工具",
];

export function renderPersona(select, command) {
  const persona = select("persona");
  const governance = select("governance");
  const expectedVersion = publishedConfigVersion(governance);
  const draftId = `page-draft-${expectedVersion + 1}`;
  const action = (label, type, payload, options = {}) => governedAction(label, {
    type,
    expected_version: expectedVersion,
    payload,
  }, command, options);
  const draftActions = [
    action("新建草稿", "config_draft", {}, { fields: [
      { name: "config_id", label: "草稿 ID", defaultValue: draftId },
      { name: "config", label: "行为配置（JSON）", format: "json", defaultValue: "{\n  \"reply_length\": \"balanced\"\n}" },
    ] }),
    action("校验草稿", "config_validate", {}, { fields: [
      { name: "config_id", label: "草稿 ID", defaultValue: draftId },
    ] }),
    action("Dry-run", "config_dry_run", { historical_events: [], worker_outputs: [] }, { fields: [
      { name: "config_id", label: "草稿 ID", defaultValue: draftId },
    ] }),
    action("发布", "config_publish", {}, { danger: true, fields: [
      { name: "config_id", label: "草稿 ID", defaultValue: draftId },
    ] }),
    action("恢复版本", "config_restore", {}, { danger: true, fields: [
      { name: "config_id", label: "已发布配置 ID", defaultValue: "" },
      { name: "source_version", label: "来源版本", format: "number", defaultValue: Math.max(1, expectedVersion) },
    ] }),
  ];
  const areaList = element("ul", { className: "definition-list" },
    BEHAVIOR_AREAS.map((area) => element("li", {}, [
      element("strong", { text: area }),
      element("span", { text: "版本化 Projection" }),
    ])),
  );

  return element("div", { className: "workspace-stack" }, [
    statusNarrative([persona, governance]),
    workspaceSection("人格行为面", "这些值来自已发布版本和当前状态，不是页面推断。", areaList),
    workspaceSection("人格 Projection", "检查状态变化、风格、媒体和工具策略的安全摘要。", projectionList(persona)),
    workspaceSection("草稿与发布", "草稿 → 校验 → 语义差异 → Dry-run → 发布；正式版本不可变。", projectionList(governance), draftActions),
  ]);
}
