import { element } from "./dom.js";
import { formatBytes, messageSummary } from "./presenters.js";

function mediaMeta(part = {}) {
  return [part.name, formatBytes(part.size)].filter(Boolean).join(" · ");
}

function mediaPart(part = {}, compact = false) {
  const kind = String(part.kind || "unknown");
  const label = String(part.label || "非文本内容");
  const previewable = Boolean(part.media_ref && part.preview);
  const node = element("span", {
    className: `message-part message-part-${kind}${compact ? " is-compact" : ""}`,
    dataset: previewable && (!compact || part.preview === "image") ? {
      mediaRef: part.media_ref,
      mediaKind: part.preview,
      mediaExpanded: String(!compact),
    } : {},
  }, [
    element("span", { className: "message-part-label", text: label }),
    ...(!compact && mediaMeta(part)
      ? [element("small", { className: "message-part-meta", text: mediaMeta(part) })]
      : []),
  ]);
  return node;
}

export function renderMessageContent(message = {}, { compact = false } = {}) {
  const parts = Array.isArray(message.parts) ? message.parts : [];
  if (!parts.length) {
    return element("span", {
      className: compact ? "two-line" : "message-text",
      text: messageSummary(message),
    });
  }
  const children = [];
  if (compact) {
    children.push(element("small", { className: "two-line", text: messageSummary(message) }));
  } else {
    for (const part of parts) {
      if (part?.kind === "text") {
        children.push(element("p", { className: "message-text", text: part.text || "" }));
      }
    }
  }
  const media = parts.filter((part) => part?.kind !== "text");
  if (media.length) {
    children.push(element("div", {
      className: compact ? "message-parts message-parts-compact" : "message-parts",
    }, media.map((part) => mediaPart(part, compact))));
  }
  return element("div", {
    className: compact ? "message-content message-content-compact" : "message-content",
  }, children);
}
