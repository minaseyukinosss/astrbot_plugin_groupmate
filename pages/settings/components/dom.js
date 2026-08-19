export function element(tag, options = {}, children = []) {
  const node = document.createElement(tag);
  if (options.className) node.className = options.className;
  if (options.text !== undefined) node.textContent = String(options.text);
  for (const [name, value] of Object.entries(options.attrs || {})) {
    if (value !== undefined && value !== null) node.setAttribute(name, String(value));
  }
  for (const [name, value] of Object.entries(options.dataset || {})) {
    if (value !== undefined && value !== null) node.dataset[name] = String(value);
  }
  const values = Array.isArray(children) ? children : [children];
  for (const child of values) {
    if (child !== undefined && child !== null) node.append(child);
  }
  return node;
}

export function button(label, options = {}) {
  const node = element("button", {
    className: options.className || "button button-secondary",
    text: label,
    attrs: { type: "button", ...options.attrs },
    dataset: options.dataset,
  });
  if (options.onClick) node.addEventListener("click", options.onClick);
  return node;
}

export function textValue(value) {
  if (value === true) return "是";
  if (value === false) return "否";
  if (value === null || value === undefined || value === "") return "—";
  if (Array.isArray(value)) return value.map(textValue).join("、");
  if (typeof value === "object") {
    return Object.entries(value)
      .map(([key, item]) => `${key}: ${textValue(item)}`)
      .join(" · ");
  }
  return String(value);
}
