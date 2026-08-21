const MAX_UPLOAD_BYTES = 5_000_000;
const ALLOWED_MIME_TYPES = new Set([
  "image/jpeg",
  "image/png",
  "image/gif",
  "image/webp",
  "audio/mpeg",
  "audio/ogg",
  "audio/wav",
]);

export function validateUpload(file) {
  const name = String(file?.name || "");
  const mime = String(file?.type || "").toLowerCase();
  const size = Number(file?.size || 0);
  if (
    !name
    || name.includes("/")
    || name.includes("\\")
    || name.includes("..")
    || /[\u0000-\u001f]/u.test(name)
  ) {
    return { accepted: false, reason: "unsafe_filename" };
  }
  if (!ALLOWED_MIME_TYPES.has(mime)) {
    return { accepted: false, reason: "unsupported_mime" };
  }
  if (!Number.isFinite(size) || size < 1 || size > MAX_UPLOAD_BYTES) {
    return { accepted: false, reason: "file_too_large" };
  }
  return { accepted: true, reason: null };
}

const PREVIEW_MIME_PREFIXES = Object.freeze({
  image: ["data:image/png;base64,", "data:image/jpeg;base64,", "data:image/webp;base64,", "data:image/gif;base64,", "data:image/avif;base64,"],
  audio: ["data:audio/mpeg;base64,", "data:audio/ogg;base64,", "data:audio/wav;base64,", "data:audio/x-wav;base64,", "data:audio/aac;base64,", "data:audio/mp4;base64,", "data:audio/amr;base64,"],
  video: ["data:video/mp4;base64,", "data:video/webm;base64,", "data:video/quicktime;base64,"],
});

export function safeMediaPreview(payload, expectedKind) {
  const kind = String(payload?.kind || "").toLowerCase();
  const expected = String(expectedKind || "").toLowerCase();
  const source = String(payload?.data_uri || "");
  if (!expected || kind !== expected) return null;
  return (PREVIEW_MIME_PREFIXES[expected] || []).some((prefix) => source.startsWith(prefix))
    ? source
    : null;
}
