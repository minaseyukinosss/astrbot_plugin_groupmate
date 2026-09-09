import { button, element } from "../components/dom.js";
import { governedAction } from "../components/command-dialog.js";
import { validateUpload } from "../components/security.js";

const STATUS_LABELS = Object.freeze({
  candidate: "待认知",
  ready: "可用",
  disabled: "已停用",
  rejected: "已拒绝",
});

const CAPTION_SOURCE_LABELS = Object.freeze({
  admin: "手写",
  vision: "视觉",
  mixed: "视觉与手写",
});

const JUDGMENT_LABELS = Object.freeze({
  sticker: "判定为表情",
});

const ATTITUDE_LABELS = Object.freeze({
  amused: "好笑",
  tease: "调侃",
  proud: "得意",
  helpless: "无奈",
  confused: "懵",
  startle: "惊讶",
  wronged: "委屈",
  refuse: "拒绝",
  agree: "同意",
  acknowledge: "附和",
  warm: "温暖",
  close: "收束",
});

const AFFECTION_FLOORS = Object.freeze([
  [0, "谁都行"],
  [10, "至少认识"],
  [30, "至少熟悉"],
  [55, "至少亲近"],
  [80, "仅默契"],
]);

function affectionFloorValue(value) {
  const number = Number(value);
  if (!Number.isFinite(number) || number <= 0) return 0;
  return AFFECTION_FLOORS.reduce((best, [floor]) => (number >= floor ? floor : best), 0);
}

function affectionFloorLabel(value) {
  const floor = affectionFloorValue(value);
  const match = AFFECTION_FLOORS.find(([stored]) => stored === floor);
  return match ? match[1] : "谁都行";
}

const ORIGIN_LABELS = Object.freeze({
  admin_import: "管理员导入",
  group_captured: "群友消息",
});

function statusChip(value) {
  const normalized = String(value || "candidate");
  return element("span", {
    className: "knowledge-status",
    text: STATUS_LABELS[normalized] || normalized,
    dataset: { status: normalized },
  });
}

function previewNode(card) {
  return element("div", {
    className: "sticker-preview",
    dataset: {
      mediaRef: card.preview_ref || card.asset_id,
      mediaKind: "image",
    },
  }, [
    element("span", { className: "message-part-label", text: card.meaning || "表情预览" }),
  ]);
}

function cardButton(card, onSelect) {
  const node = element("button", {
    className: "sticker-card",
    attrs: { type: "button" },
    dataset: { status: card.status, assetId: card.asset_id },
  }, [
    previewNode(card),
    element("strong", { text: card.meaning || "认知尚未形成" }),
    element("span", { className: "sticker-card-meta" }, [
      statusChip(card.status),
      element("small", { text: ORIGIN_LABELS[card.origin_kind] || card.origin_kind }),
    ]),
  ]);
  node.addEventListener("click", () => onSelect(card));
  return node;
}

function emptyState(message) {
  return element("p", { className: "knowledge-empty", text: message });
}

function capacityNote(capacity) {
  const items = [];
  if (capacity?.candidate_full) {
    items.push("待认知池已满（30），不再收入新图，已有卡片都还在。");
  }
  if (capacity?.library_full) {
    items.push("正式图鉴已满（200），停止自动捕获，不会删旧图。");
  }
  if (!items.length) return null;
  return element("p", { className: "sticker-capacity-alert", text: items.join(" ") });
}

function phraseField(name, label, values) {
  return element("label", { className: "sticker-field" }, [
    element("span", { text: label }),
    element("input", {
      attrs: {
        name,
        value: (Array.isArray(values) ? values : []).join("，"),
        placeholder: "最多 3 条，用逗号分隔",
      },
    }),
  ]);
}

function splitPhrases(value) {
  return String(value || "")
    .split(/[，,]/)
    .map((item) => item.trim())
    .filter(Boolean)
    .slice(0, 3);
}

function selectedAttitudes(form) {
  return [...form.querySelectorAll("input[name='attitudes']:checked")].map((input) => input.value);
}

function detailForm(card, submitCommand, refresh, onClose, visionEnabled) {
  const form = element("form", { className: "sticker-detail-form" });
  const meaning = element("textarea", {
    attrs: {
      name: "meaning",
      rows: "3",
      maxlength: "48",
      placeholder: "这张图具体在说什么，换成另一张就应不成立",
    },
  });
  meaning.value = card.meaning_raw || "";
  form.append(
    element("label", { className: "sticker-field" }, [
      element("span", { text: "含义" }),
      meaning,
    ]),
    phraseField("use_when", "适用", card.use_when),
    phraseField("do_not_use", "禁用", card.do_not_use),
    element("fieldset", { className: "sticker-attitudes" }, [
      element("legend", { text: "口气（可多选，可不选）" }),
      element("p", {
        className: "sticker-field-hint",
        text: "这张图适合哪种闲聊口气。对得上更容易配上；不勾就只按含义和适用来选。",
      }),
      ...Object.entries(ATTITUDE_LABELS).map(([value, label]) => {
        const checked = (card.attitudes || []).includes(value);
        return element("label", {}, [
          element("input", {
            attrs: {
              type: "checkbox",
              name: "attitudes",
              value,
              ...(checked ? { checked: "" } : {}),
            },
          }),
          element("span", { text: label }),
        ]);
      }),
    ]),
    element("label", { className: "sticker-field" }, [
      element("span", { text: "好感门槛" }),
      element("select", { attrs: { name: "min_familiarity" } }, AFFECTION_FLOORS.map(([floor, label]) => element("option", {
        text: label,
        attrs: {
          value: String(floor),
          ...(floor === affectionFloorValue(card.min_familiarity) ? { selected: "" } : {}),
        },
      }))),
      element("small", {
        className: "sticker-field-hint",
        text: "按好感度榜的关系阶段。谁都行对陌生和更低也发；仅默契只对最熟的人发。",
      }),
    ]),
  );

  async function save(extra = {}) {
    await submitCommand({
      type: "sticker_caption",
      asset_id: card.asset_id,
      meaning: meaning.value,
      use_when: splitPhrases(form.elements.use_when.value),
      do_not_use: splitPhrases(form.elements.do_not_use.value),
      attitudes: selectedAttitudes(form),
      min_familiarity: Number(form.elements.min_familiarity.value || 0),
      ...extra,
    });
    await refresh();
  }

  const actions = [
    button("保存含义", {
      className: "button button-secondary",
      onClick: () => save(),
    }),
  ];
  if (card.status === "candidate" || card.status === "disabled") {
    actions.push(button("确认为可用", {
      className: "button",
      onClick: async () => {
        await save();
        await submitCommand({ type: "sticker_confirm", asset_id: card.asset_id });
        await refresh();
      },
    }));
  }
  if (card.status === "ready") {
    actions.push(button("停用", {
      className: "button button-secondary",
      onClick: async () => {
        await submitCommand({ type: "sticker_disable", asset_id: card.asset_id });
        await refresh();
      },
    }));
  }
  if (visionEnabled) {
    actions.push(button("重新认知", {
      className: "button button-secondary",
      onClick: async () => {
        await submitCommand({ type: "sticker_recaption", asset_id: card.asset_id });
        await refresh();
      },
    }));
  }
  actions.push(
    governedAction("驳回", { type: "sticker_reject" }, async () => {
      await submitCommand({ type: "sticker_reject", asset_id: card.asset_id });
      onClose();
      await refresh();
    }, { danger: true }),
    governedAction("删除", { type: "sticker_delete" }, async () => {
      await submitCommand({ type: "sticker_delete", asset_id: card.asset_id });
      onClose();
      await refresh();
    }, { danger: true }),
    button("关闭", { className: "button button-quiet", onClick: onClose }),
  );

  return element("aside", { className: "sticker-detail" }, [
    element("header", {}, [
      element("h3", { text: card.meaning || "认知尚未形成" }),
      element("p", {
        text: `${ORIGIN_LABELS[card.origin_kind] || card.origin_kind} · 见过 ${card.sighting_count || 1} 次 · 用过 ${card.use_count || 0} 次`,
      }),
    ]),
    previewNode(card),
    element("dl", { className: "sticker-detail-meta" }, [
      ["状态", STATUS_LABELS[card.status] || card.status],
      ["判定", JUDGMENT_LABELS[card.is_sticker_judgment] || card.judgment_reason || "尚未判定"],
      ["含义来源", CAPTION_SOURCE_LABELS[card.caption_source] || "未形成"],
      ["来源群", card.source_group_id || "无（导入或不限制发送群）"],
      ["许可证", card.license_status],
      ["好感门槛", card.affection_floor_label || affectionFloorLabel(card.min_familiarity)],
    ].map(([label, value]) => element("div", {}, [
      element("dt", { text: label }),
      element("dd", { text: value }),
    ]))),
    form,
    element("div", { className: "sticker-detail-actions" }, actions),
  ]);
}

async function readUpload(file) {
  const verdict = validateUpload(file);
  if (!verdict.accepted) {
    throw new Error("这张图不能上传：文件名或格式不安全。");
  }
  const buffer = await file.arrayBuffer();
  const bytes = new Uint8Array(buffer);
  let binary = "";
  bytes.forEach((value) => {
    binary += String.fromCharCode(value);
  });
  return {
    filename: file.name,
    mime_type: file.type,
    content_base64: btoa(binary),
  };
}

export function renderStickers(selectView, submitCommand, refresh) {
  const view = selectView("stickers") || {};
  const inbox = Array.isArray(view.inbox) ? view.inbox : [];
  const library = Array.isArray(view.library) ? view.library : [];
  const capacity = view.capacity || {};
  const root = element("div", { className: "sticker-workspace workspace-stack" });
  const detailHost = element("div", { className: "sticker-detail-host" });
  let selectedId = "";

  function showDetail(card) {
    selectedId = card.asset_id;
        detailHost.replaceChildren(detailForm(card, submitCommand, refresh, () => {
      selectedId = "";
      detailHost.replaceChildren();
    }, Boolean(view.vision_enabled)));
  }

  const upload = element("input", {
    attrs: { type: "file", accept: "image/png,image/jpeg,image/gif,image/webp" },
  });
  upload.hidden = true;
  upload.addEventListener("change", async () => {
    const file = upload.files?.[0];
    upload.value = "";
    if (!file) return;
    try {
      const payload = await readUpload(file);
      await submitCommand({ type: "sticker_upload", ...payload });
      await refresh();
    } catch (error) {
      detailHost.replaceChildren(element("p", {
        className: "knowledge-detail-error",
        text: error.message || "上传失败",
      }));
    }
  });

  root.append(
    element("section", { className: "knowledge-health" }, [
      element("header", { className: "knowledge-section-heading" }, [
        element("div", {}, [
          element("h2", { text: "表情图鉴" }),
          element("p", { text: "每张图先有含义，闲聊成文后再从可用图里抽一张配上。没有合适的图就只发文字。" }),
        ]),
        element("div", { className: "knowledge-section-actions" }, [
          button("上传表情", {
            className: "button",
            onClick: () => upload.click(),
          }),
        ]),
      ]),
      element("dl", { className: "knowledge-metrics" }, [
        ["待认知", String(capacity.candidate_count || inbox.length || 0)],
        ["正式图鉴", String(capacity.library_count || library.length || 0)],
        ["待认知上限", String(capacity.candidate_limit || 30)],
        ["图鉴上限", String(capacity.library_limit || 200)],
      ].map(([label, value]) => element("div", {}, [
        element("dt", { text: label }),
        element("dd", { text: value }),
      ]))),
      capacityNote(capacity),
      upload,
    ]),
    element("section", { className: "knowledge-panel sticker-inbox" }, [
      element("header", { className: "knowledge-section-heading" }, [
        element("div", {}, [
          element("h2", { text: "待认知" }),
          element("p", { text: view.vision_enabled
            ? "配了视觉模型后，新图会在后台判定并写含义。GIF 和静图都须点「确认为可用」后才能配图。"
            : "新收到或刚上传的图。空含义不能拿去配图，先写清它在说什么，再确认。未配置视觉模型时只能手写。" }),
        ]),
      ]),
      inbox.length
        ? element("div", { className: "sticker-grid" }, inbox.map((card) => cardButton(card, showDetail)))
        : emptyState("还没有待认知的表情。先在插件配置打开「从群友消息里收表情」；自定义图会进这里，商城表情和系统黄豆不会收集。"),
    ]),
    element("section", { className: "knowledge-panel" }, [
      element("header", { className: "knowledge-section-heading" }, [
        element("div", {}, [
          element("h2", { text: "图鉴" }),
          element("p", { text: "可用和停用的表情。卡片上直接是含义句，点开可改写、停用或删除。" }),
        ]),
      ]),
      library.length
        ? element("div", { className: "sticker-grid" }, library.map((card) => cardButton(card, showDetail)))
        : emptyState("图鉴还是空的。待认知里确认过的图才会出现在这里，也可以先上传一张。"),
    ]),
    detailHost,
  );

  if (selectedId) {
    const selected = [...inbox, ...library].find((card) => card.asset_id === selectedId);
    if (selected) showDetail(selected);
  }
  return root;
}
