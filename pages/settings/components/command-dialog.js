import { button, element } from "./dom.js";

const HIGH_IMPACT = new Set([
  "reset",
  "config_publish",
  "config_restore",
  "forget",
  "correct",
  "link",
  "cancel",
  "approve_calibration",
]);

export function requiresConfirmation(type) {
  return HIGH_IMPACT.has(String(type));
}

export function governedAction(label, commandSpec, command, options = {}) {
  return button(label, {
    className: options.danger ? "button button-danger" : "button button-secondary",
    onClick: () => openCommandDialog(commandSpec, command, options),
  });
}

export function openCommandDialog(commandSpec, command, options = {}) {
  const dialog = element("dialog", { className: "command-dialog" });
  const form = element("form", { attrs: { method: "dialog" } });
  const reason = element("textarea", {
    attrs: {
      id: `reason-${commandSpec.type}`,
      name: "reason",
      required: "",
      rows: "3",
      placeholder: "说明本次操作的依据和预期影响",
    },
  });
  const mustConfirm = requiresConfirmation(commandSpec.type);
  const confirmation = element("input", {
    attrs: { type: "checkbox", name: "confirmed", value: "true" },
  });
  const error = element("p", { className: "form-error", attrs: { role: "alert" } });
  const fieldNodes = new Map();
  const cancel = button("取消", { onClick: () => dialog.close() });
  const submit = element("button", {
    className: options.danger ? "button button-danger" : "button button-primary",
    text: options.submitLabel || "提交命令",
    attrs: { type: "submit" },
  });

  form.append(
    element("header", {}, [
      element("h2", { text: options.title || labelFor(commandSpec.type) }),
      element("p", { text: "服务端会再次验证管理员、作用域、版本、原因与确认。" }),
    ]),
    element("label", { text: "操作原因", attrs: { for: `reason-${commandSpec.type}` } }),
    reason,
  );
  for (const field of options.fields || []) {
    const fieldId = `command-${commandSpec.type}-${field.name}`;
    const control = field.multiline || field.format === "json"
      ? element("textarea", {
        attrs: {
          id: fieldId,
          rows: field.format === "json" ? "5" : "3",
          required: field.required === false ? null : "",
        },
      })
      : element("input", {
        attrs: {
          id: fieldId,
          type: field.format === "number" ? "number" : "text",
          required: field.required === false ? null : "",
        },
      });
    control.value = String(field.defaultValue ?? "");
    fieldNodes.set(field.name, { control, field });
    form.append(
      element("label", { text: field.label, attrs: { for: fieldId } }),
      control,
    );
  }
  if (mustConfirm) {
    form.append(element("label", { className: "confirmation-field" }, [
      confirmation,
      element("span", { text: "我已复核目标、作用域和影响，确认执行此高影响操作。" }),
    ]));
  }
  form.append(error, element("footer", {}, [cancel, submit]));
  dialog.append(form);
  document.body.append(dialog);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!reason.value.trim()) {
      error.textContent = "必须填写操作原因。";
      reason.focus();
      return;
    }
    if (mustConfirm && !confirmation.checked) {
      error.textContent = "高影响操作需要二次确认。";
      confirmation.focus();
      return;
    }
    const payload = { ...(commandSpec.payload || {}) };
    try {
      for (const [name, { control, field }] of fieldNodes) {
        const raw = control.value.trim();
        if (!raw && field.required !== false) {
          throw new Error(`${field.label}不能为空。`);
        }
        if (field.format === "json") payload[name] = raw ? JSON.parse(raw) : {};
        else if (field.format === "csv") {
          payload[name] = raw.split(",").map((item) => item.trim()).filter(Boolean);
        } else if (field.format === "number") payload[name] = Number(raw);
        else payload[name] = raw;
      }
    } catch (parseError) {
      error.textContent = parseError.message || "命令字段格式无效。";
      return;
    }
    submit.disabled = true;
    try {
      await command({
        ...commandSpec,
        payload,
        expected_version: Number(commandSpec.expected_version || 0),
        reason: reason.value.trim(),
        confirmed: mustConfirm ? confirmation.checked : false,
      });
      dialog.close();
    } finally {
      submit.disabled = false;
    }
  });
  dialog.addEventListener("close", () => dialog.remove(), { once: true });
  dialog.showModal();
}

function labelFor(type) {
  return ({
    pause: "变更运行状态",
    reset: "重置运行状态",
    config_publish: "发布配置版本",
    config_restore: "恢复配置版本",
    forget: "遗忘记忆",
    correct: "纠正社会状态",
    link: "建立身份关联",
    cancel: "取消任务",
    approve_calibration: "批准校准",
  })[type] || "提交受治理命令";
}
