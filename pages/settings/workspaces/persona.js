import { governedAction } from "../components/command-dialog.js";
import { element } from "../components/dom.js";
import { compactActivity, projectionList, publishedConfigVersion, statusNarrative, workspaceSection } from "../components/projection.js";

const DEFAULT_PROFILE = {
  identity: {
    name: "Groupmate",
    role: "群聊中的长期伙伴，先理解现场，再在有价值时自然参与。",
    background: "熟悉群内关系和共同经历，但不冒充任何真实成员。",
  },
  presence: {
    default_mode: "social",
    rhythm: "保持在场感，不追求每条消息都回应；忙碌或信息不足时安静观察。",
  },
  participation: {
    initiative: "balanced",
    speak_when: "被直接询问、能够提供帮助、适合接住情绪或自然延续共同话题时参与。",
    stay_silent_when: "外置插件正在处理、话题已自然结束、插话会打断他人或没有新增价值时沉默。",
  },
  expression: {
    tone: "自然、真诚、简洁，像熟悉群氛围的朋友而不是客服。",
    reply_length: "short",
    language_habits: "优先口语化短句；避免模板化总结、说教和重复复述。",
    emoji_style: "light",
  },
  social: {
    stance: "友好但有边界，不虚构亲密关系。",
    relationship_style: "关系随真实互动逐步形成，对不同成员保持连续但不过度迎合。",
    culture_adaptation: "学习本群称呼、梗和节奏，但不复制敏感经历或跨群传播。",
  },
  media: {
    policy: "disabled",
    notes: "只有媒体比文字更合适且来源与权限明确时才使用。",
  },
  tools: {
    autonomy: "read_only",
    confirmation_policy: "只自主使用管理员允许的只读能力；外部副作用必须明确确认。",
  },
};

const PROFILE_SECTIONS = [
  {
    key: "identity",
    label: "身份",
    description: "定义 Groupmate 在群里的长期身份，不依赖 AstrBot 的普通会话人格。",
    fields: [
      { key: "name", label: "角色名称", kind: "input", help: "群成员看到并记住的称呼。" },
      { key: "role", label: "角色定位", kind: "textarea", wide: true, help: "说明它与群成员的关系，以及存在的主要价值。" },
      { key: "background", label: "身份背景", kind: "textarea", wide: true, help: "只写稳定设定，不虚构现实身份或群内经历。" },
    ],
  },
  {
    key: "presence",
    label: "在场状态",
    description: "设置默认在场方式与参与节奏。",
    fields: [
      { key: "default_mode", label: "默认状态", kind: "select", choices: [["social", "自然参与"], ["quiet_observer", "安静观察"]] },
      { key: "rhythm", label: "在场节奏", kind: "textarea", wide: true, help: "描述活跃、观望和退场的自然切换。" },
    ],
  },
  {
    key: "participation",
    label: "参与方式",
    description: "明确何时主动加入，何时把话语权留给群成员。",
    fields: [
      { key: "initiative", label: "参与主动性", kind: "select", choices: [["reserved", "克制"], ["balanced", "平衡"], ["proactive", "积极"]] },
      { key: "speak_when", label: "适合发言", kind: "textarea", wide: true, help: "用自然语言写出值得参与的场景。" },
      { key: "stay_silent_when", label: "保持沉默", kind: "textarea", wide: true, help: "外置插件请求、打断风险和低价值插话应在这里明确排除。" },
    ],
  },
  {
    key: "expression",
    label: "表达",
    description: "控制语气、篇幅和口语习惯，让回复更像群友而不是客服。",
    fields: [
      { key: "tone", label: "表达语气", kind: "textarea", wide: true },
      { key: "reply_length", label: "回复长度", kind: "select", choices: [["short", "短句优先"], ["balanced", "适中"], ["detailed", "详细"]] },
      { key: "emoji_style", label: "表情使用", kind: "select", choices: [["none", "不使用"], ["light", "少量"], ["natural", "自然使用"]] },
      { key: "language_habits", label: "语言习惯", kind: "textarea", wide: true },
    ],
  },
  {
    key: "social",
    label: "社交印象",
    description: "规定关系形成、群文化适应和社交边界。",
    fields: [
      { key: "stance", label: "基本立场", kind: "textarea", wide: true },
      { key: "relationship_style", label: "关系方式", kind: "textarea", wide: true },
      { key: "culture_adaptation", label: "群文化适应", kind: "textarea", wide: true },
    ],
  },
  {
    key: "media",
    label: "媒体",
    description: "决定图片和其他媒体是否适合由 Groupmate 主动使用。",
    fields: [
      { key: "policy", label: "媒体策略", kind: "select", choices: [["disabled", "暂不主动使用"], ["contextual", "只在合适场景使用"]] },
      { key: "notes", label: "使用边界", kind: "textarea", wide: true },
    ],
  },
  {
    key: "tools",
    label: "工具",
    description: "只定义 Groupmate 自主使用已获准能力的边界，不在群里发送命令冒充用户。",
    fields: [
      { key: "autonomy", label: "自主能力", kind: "select", choices: [["disabled", "禁用"], ["read_only", "仅只读"], ["low_impact", "只读与低影响"]] },
      { key: "confirmation_policy", label: "确认规则", kind: "textarea", wide: true },
    ],
  },
];

function cloneProfile(profile) {
  return JSON.parse(JSON.stringify(profile));
}

function currentProfile(view) {
  const item = (view?.items || []).find((candidate) => candidate.kind === "persona.profile");
  return {
    item,
    profile: cloneProfile(item?.summary?.profile || DEFAULT_PROFILE),
  };
}

function profileControl(sectionKey, field, value) {
  const id = `persona-${sectionKey}-${field.key}`;
  let control;
  if (field.kind === "select") {
    control = element("select", { attrs: { id, required: "" } }, field.choices.map(([choice, label]) => {
      const option = element("option", { text: label, attrs: { value: choice } });
      option.selected = choice === value;
      return option;
    }));
  } else if (field.kind === "textarea") {
    control = element("textarea", { attrs: { id, required: "", rows: "4", maxlength: "1200" } });
    control.value = value;
  } else {
    control = element("input", { attrs: { id, required: "", type: "text", maxlength: "1200" } });
    control.value = value;
  }
  control.dataset.profileSection = sectionKey;
  control.dataset.profileField = field.key;
  return element("div", { className: field.wide ? "persona-field persona-field-wide" : "persona-field" }, [
    element("label", { text: field.label, attrs: { for: id } }),
    field.help ? element("small", { text: field.help }) : null,
    control,
  ]);
}

export function collectProfile(root) {
  const profile = {};
  for (const section of PROFILE_SECTIONS) profile[section.key] = {};
  root.querySelectorAll("[data-profile-section][data-profile-field]").forEach((control) => {
    const value = String(control.value || "").trim();
    if (!value) throw new Error("人格字段不能为空。");
    profile[control.dataset.profileSection][control.dataset.profileField] = value;
  });
  return profile;
}

export function renderPersona(select, command) {
  const persona = select("persona");
  const governance = select("governance");
  const activity = select("activity");
  const { item: profileItem, profile } = currentProfile(persona);
  const expectedVersion = Number(profileItem?.summary?.config_version ?? publishedConfigVersion(governance));
  const groupId = String(persona?.scope?.group_id || "default");
  const configId = `persona-profile:${groupId}`;
  const action = (label, type, payload, options = {}) => governedAction(label, {
    type, expected_version: expectedVersion, payload,
  }, command, options);

  const tabs = element("div", { className: "persona-tabs", attrs: { role: "tablist", "aria-label": "人格配置步骤" } });
  const panels = element("div", { className: "persona-form" });
  const editorTitle = element("h3", { text: PROFILE_SECTIONS[0].label });
  const editorDescription = element("p", { text: PROFILE_SECTIONS[0].description });

  PROFILE_SECTIONS.forEach((section, index) => {
    const panelId = `persona-panel-${section.key}`;
    const tab = element("button", {
      className: index === 0 ? "persona-tab is-active" : "persona-tab",
      text: `${index + 1}  ${section.label}`,
      attrs: {
        type: "button",
        role: "tab",
        "aria-controls": panelId,
        "aria-selected": index === 0 ? "true" : "false",
      },
    });
    const panel = element("section", {
      className: "persona-panel",
      attrs: { id: panelId, role: "tabpanel", "aria-label": section.label },
    }, [
      element("div", { className: "persona-field-grid" }, section.fields.map((field) => (
        profileControl(section.key, field, String(profile[section.key]?.[field.key] || ""))
      ))),
    ]);
    panel.hidden = index !== 0;
    tab.addEventListener("click", () => {
      tabs.querySelectorAll(".persona-tab").forEach((candidate) => {
        const active = candidate === tab;
        candidate.classList.toggle("is-active", active);
        candidate.setAttribute("aria-selected", String(active));
      });
      panels.querySelectorAll(".persona-panel").forEach((candidate) => {
        candidate.hidden = candidate !== panel;
      });
      editorTitle.textContent = section.label;
      editorDescription.textContent = section.description;
    });
    tabs.append(tab);
    panels.append(panel);
  });

  const draftActions = [
    action("保存草稿", "config_draft", { config_id: configId }, {
      payloadFactory: () => ({ config: collectProfile(panels) }),
      submitLabel: "保存人格草稿",
    }),
    action("校验", "config_validate", { config_id: configId }),
    action("预览效果", "config_dry_run", { config_id: configId, historical_events: [], worker_outputs: [] }),
    action("发布到群", "config_publish", { config_id: configId }, { danger: true, submitLabel: "确认发布" }),
  ];
  const restore = action("恢复历史版本", "config_restore", { config_id: configId }, { danger: true, fields: [
    { name: "source_version", label: "来源版本", format: "number", defaultValue: Math.max(1, expectedVersion) },
  ] });

  const editor = element("div", { className: "persona-editor" }, [
    element("div", { className: "persona-editor-heading" }, [
      editorTitle,
      editorDescription,
      element("span", { className: "persona-version", text: expectedVersion ? `当前已发布 v${expectedVersion}` : "使用内置默认人格" }),
    ]),
    panels,
    element("div", { className: "persona-editor-actions" }, draftActions),
  ]);
  const preview = element("aside", { className: "persona-preview" }, [
    element("div", { className: "preview-heading" }, [
      element("div", {}, [element("h3", { text: "当前人格" }), element("p", { text: "运行时会按当前群冻结这一版档案" })]),
      element("span", { className: "status-chip", text: expectedVersion ? `v${expectedVersion}` : "默认" }),
    ]),
    element("dl", { className: "persona-profile-summary" }, [
      element("div", {}, [element("dt", { text: "名称" }), element("dd", { text: profile.identity.name })]),
      element("div", {}, [element("dt", { text: "定位" }), element("dd", { text: profile.identity.role })]),
      element("div", {}, [element("dt", { text: "表达" }), element("dd", { text: profile.expression.tone })]),
      element("div", {}, [element("dt", { text: "主动性" }), element("dd", { text: ({ reserved: "克制", balanced: "平衡", proactive: "积极" })[profile.participation.initiative] || profile.participation.initiative })]),
    ]),
    element("div", { className: "persona-live-heading" }, [
      element("h4", { text: "群内观察" }),
      element("p", { text: "用于判断人格在真实群聊中的表现，不会把事件内容写回人格设定。" }),
    ]),
    compactActivity(activity?.items || [], "还没有可预览的群内活动。"),
  ]);

  return element("div", { className: "workspace-stack" }, [
    statusNarrative([persona, governance, activity]),
    tabs,
    element("div", { className: "persona-layout" }, [editor, preview]),
    workspaceSection("版本与发布记录", "人格按群独立发布；正式版本不可变，恢复会创建新的已发布版本。", projectionList(governance, { limit: 6 }), [restore]),
  ]);
}
