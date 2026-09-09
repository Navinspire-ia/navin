// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/** Attachment preview kind, decided by file extension. */

export type AttachmentKind =
  | "image"
  | "pdf"
  | "audio"
  | "video"
  | "markdown"
  | "text"
  | "other";

const IMAGE_EXT = new Set(["png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "ico", "avif"]);
const AUDIO_EXT = new Set(["mp3", "wav", "ogg", "m4a", "flac", "aac", "opus"]);
const VIDEO_EXT = new Set(["mp4", "webm", "mov", "mkv", "m4v"]);
const MARKDOWN_EXT = new Set(["md", "markdown"]);
const TEXT_EXT = new Set([
  "txt", "json", "yaml", "yml", "toml", "ini", "csv", "tsv",
  "log", "xml", "html", "css", "js", "jsx", "ts", "tsx", "py", "rb", "go",
  "rs", "java", "c", "h", "cpp", "hpp", "sh", "bash", "sql", "env", "conf",
]);

export function attachmentKind(name: string): AttachmentKind {
  const ext = (name.split(".").pop() ?? "").toLowerCase();
  if (IMAGE_EXT.has(ext)) return "image";
  if (ext === "pdf") return "pdf";
  if (AUDIO_EXT.has(ext)) return "audio";
  if (VIDEO_EXT.has(ext)) return "video";
  if (MARKDOWN_EXT.has(ext)) return "markdown";
  if (TEXT_EXT.has(ext)) return "text";
  return "other";
}
