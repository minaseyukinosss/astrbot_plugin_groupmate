import { governedAction } from "../components/command-dialog.js";
import { element } from "../components/dom.js";
import { compactActivity, projectionList, publishedConfigVersion, statusNarrative, workspaceSection } from "../components/projection.js";

const BEHAVIOR_AREAS = [
  ["身份", "角色定位与长期身份"],
  ["在场状态", "参与节奏与当前状态"],
  ["参与方式", "何时说话、何时沉默"],
  ["表达", "语气、长度与回应方式"],
  ["社交印象", "关系和群文化边界"],
  ["媒体", "图片与媒体使用策略"],
  ["工具", "允许的能力与风险边界"],
];

export function renderPersona(select, command) {
  const persona = select("persona");
  const governance = select("governance");
  const activity = select("activity");
  const expectedVersion = publishedConfigVersion(governance);
  const draftId = `page-draft-${expectedVersion + 1}`;
  const action = (label, type, payload, options = {}) => governedAction(label, {
    type, expected_version: expectedVersion, payload,
  }, command, options);
  const draftActions = [
    action("保存草稿", "config_draft", {}, { fields: [
      { name: "config_id", label: "草稿 ID", defaultValue: draftId },
      { name: "config", label: "行为配置（JSON）", format: "json", defaultValue: "{\n  \"reply_length\": \"balanced\"\n}" },
    ] }),
    action("校验", "config_validate", {}, { fields: [{ name: "config_id", label: "草稿 ID", defaultValue: draftId }] }),
    action("预览效果", "config_dry_run", { historical_events: [], worker_outputs: [] }, { fields: [{ name: "config_id", label: "草稿 ID", defaultValue: draftId }] }),
    action("发布到群", "config_publish", {}, { danger: true, fields: [{ name: "config_id", label: "草稿 ID", defaultValue: draftId }] }),
  ];
  const restore = action("恢复历史版本", "config_restore", {}, { danger: true, fields: [
    { name: "config_id", label: "已发布配置 ID", defaultValue: "" },
    { name: "source_version", label: "来源版本", format: "number", defaultValue: Math.max(1, expectedVersion) },
  ] });

  const tabs = element("div", { className: "persona-tabs", attrs: { role: "tablist", "aria-label": "人格配置步骤" } });
  const editorTitle = element("h3", { text: BEHAVIOR_AREAS[0][0] });
  const editorDescription = element("p", { text: BEHAVIOR_AREAS[0][1] });
  BEHAVIOR_AREAS.forEach(([label, description], index) => {
    const tab = element("button", {
      className: index === 0 ? "persona-tab is-active" : "persona-tab",
      text: `${index + 1}  ${label}`,
      attrs: { type: "button", role: "tab", "aria-selected": index === 0 ? "true" : "false" },
    });
    tab.addEventListener("click", () => {
      tabs.querySelectorAll(".persona-tab").forEach((candidate) => {
        const active = candidate === tab;
        candidate.classList.toggle("is-active", active);
        candidate.setAttribute("aria-selected", String(active));
      });
      editorTitle.textContent = label;
      editorDescription.textContent = description;
    });
    tabs.append(tab);
  });

  const editor = element("div", { className: "persona-editor" }, [
    element("div", { className: "persona-editor-heading" }, [editorTitle, editorDescription]),
    projectionList(persona, { limit: 8, empty: "当前还没有已发布的人格状态。" }),
    element("div", { className: "persona-editor-actions" }, draftActions),
  ]);
  const preview = element("aside", { className: "persona-preview" }, [
    element("div", { className: "preview-heading" }, [
      element("div", {}, [element("h3", { text: "实时预览" }), element("p", { text: "来自当前群的真实观察记录" })]),
      element("span", { className: "status-chip", text: "运行中" }),
    ]),
    compactActivity(activity?.items || [], "还没有可预览的群内活动。"),
  ]);

  return element("div", { className: "workspace-stack" }, [
    statusNarrative([persona, governance, activity]),
    tabs,
    element("div", { className: "persona-layout" }, [editor, preview]),
    workspaceSection("版本与发布记录", "正式版本不可变；恢复通过重新发布历史版本完成。", projectionList(governance, { limit: 6 }), [restore]),
  ]);
}
