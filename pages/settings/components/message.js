import { element } from "./dom.js";
import { formatBytes, messageSummary } from "./presenters.js";

function mediaMeta(part = {}) {
  return [part.name, formatBytes(part.size)].filter(Boolean).join(" · ");
}

function mediaPart(part = {}, compact = false) {
  const kind = String(part.kind || "unknown");
  const label = String(part.label || "非文本内容");
  const previewable = Boolean(part.media_ref && part.preview);
  const mentionAvatar = kind === "at" && part.avatar_ref && !compact
    ? element("span", {
      className: "mention-avatar",
      text: [...String(part.display_name || "群")][0] || "群",
      dataset: { avatarRef: part.avatar_ref },
      attrs: { "aria-hidden": "true" },
    })
    : null;
  const node = element("span", {
    className: `message-part message-part-${kind}${compact ? " is-compact" : ""}`,
    dataset: previewable && (!compact || part.preview === "image") ? {
      mediaRef: part.media_ref,
      mediaKind: part.preview,
      mediaExpanded: String(!compact),
    } : {},
  }, [
    mentionAvatar,
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
    const compactMedia = [];
    for (const part of parts) {
      if (part?.kind !== "text") compactMedia.push(mediaPart(part, true));
    }
    if (compactMedia.length) {
      children.push(element("div", { className: "message-parts message-parts-compact" }, compactMedia));
    }
  } else {
    let mediaRun = [];
    const flushMedia = () => {
      if (!mediaRun.length) return;
      children.push(element("div", { className: "message-parts" }, mediaRun));
      mediaRun = [];
    };
    for (const part of parts) {
      if (part?.kind === "text") {
        flushMedia();
        children.push(element("p", { className: "message-text", text: part.text || "" }));
      } else if (part) {
        mediaRun.push(mediaPart(part, false));
      }
    }
    flushMedia();
  }
  return element("div", {
    className: compact ? "message-content message-content-compact" : "message-content",
  }, children);
}
