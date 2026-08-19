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
