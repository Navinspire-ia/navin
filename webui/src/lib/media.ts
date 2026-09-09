// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import i18n from "@/i18n";
import { publishNotification } from "@/lib/notification-bus";
import { fileNameFromUrl, isDesktopShell, saveDownload, startHttpAttachmentDownload } from "@/lib/save-blob";
import type { UIMediaAttachment, UIMediaKind } from "@/lib/types";

const IMAGE_EXTENSIONS = new Set([
  ".png",
  ".jpg",
  ".jpeg",
  ".gif",
  ".webp",
  ".bmp",
  ".ico",
  ".svg",
  ".tif",
  ".tiff",
]);

const VIDEO_EXTENSIONS = new Set([
  ".mp4",
  ".webm",
  ".mov",
  ".m4v",
  ".avi",
  ".mkv",
  ".3gp",
]);

const AUDIO_EXTENSIONS = new Set([
  ".mp3",
  ".wav",
  ".ogg",
  ".oga",
  ".m4a",
  ".aac",
  ".flac",
  ".opus",
  ".weba",
]);

/** Image MIME types the gateway accepts on upload. Mirrors the ingress allowlist. */
export const ACCEPTED_IMAGE_MIMES: ReadonlySet<string> = new Set([
  "image/png",
  "image/jpeg",
  "image/webp",
  "image/gif",
]);

/** Video MIME types the gateway accepts on upload. Mirrors the ingress allowlist.
 *
 * The agent samples frames from these server-side, so a video attachment
 * reaches vision models as stills. */
export const ACCEPTED_VIDEO_MIMES: ReadonlySet<string> = new Set([
  "video/mp4",
  "video/webm",
  "video/quicktime",
]);

/** Extension fallback: browsers report an empty type for .mov often enough. */
export const VIDEO_MIME_BY_EXTENSION: ReadonlyMap<string, string> = new Map([
  [".mp4", "video/mp4"],
  [".m4v", "video/mp4"],
  [".webm", "video/webm"],
  [".mov", "video/quicktime"],
]);

/** Audio MIME types the gateway accepts on upload. Mirrors the ingress allowlist.
 *
 * The agent transcribes these server-side with the configured speech-to-text
 * provider, so an audio attachment reaches the model as a transcript. */
export const ACCEPTED_AUDIO_MIMES: ReadonlySet<string> = new Set([
  "audio/aac",
  "audio/flac",
  "audio/m4a",
  "audio/mp3",
  "audio/mp4",
  "audio/mpeg",
  "audio/ogg",
  "audio/wav",
  "audio/wave",
  "audio/x-flac",
  "audio/x-m4a",
  "audio/x-wav",
]);

/** Extension fallback for audio files the browser reports without a type. */
export const AUDIO_MIME_BY_EXTENSION: ReadonlyMap<string, string> = new Map([
  [".mp3", "audio/mpeg"],
  [".mpga", "audio/mpeg"],
  [".wav", "audio/wav"],
  [".m4a", "audio/mp4"],
  [".aac", "audio/aac"],
  [".ogg", "audio/ogg"],
  [".oga", "audio/ogg"],
  [".opus", "audio/ogg"],
  [".flac", "audio/flac"],
]);

function cleanPath(value: string): string {
  return value.split(/[?#]/, 1)[0]?.toLowerCase() ?? "";
}

/** Lowercased extension of a file name or URL, dot included; "" when absent. */
export function mediaExtension(value?: string): string {
  if (!value) return "";
  const path = cleanPath(value);
  const dot = path.lastIndexOf(".");
  if (dot < 0) return "";
  return path.slice(dot);
}

function explicitMediaKind(media: { url?: string; name?: string }): UIMediaKind | null {
  const url = media.url ?? "";
  if (url.startsWith("data:image/")) return "image";
  if (url.startsWith("data:video/")) return "video";
  if (url.startsWith("data:audio/")) return "audio";

  const ext = mediaExtension(media.name) || mediaExtension(url);
  if (!ext) return null;
  if (IMAGE_EXTENSIONS.has(ext)) return "image";
  if (VIDEO_EXTENSIONS.has(ext)) return "video";
  if (AUDIO_EXTENSIONS.has(ext)) return "audio";
  return "file";
}

export function inferMediaKind(media: { url?: string; name?: string }): UIMediaKind {
  return explicitMediaKind(media) ?? "file";
}

export function toMediaAttachment(media: {
  url?: string;
  name?: string;
  kind?: UIMediaKind;
}): UIMediaAttachment {
  return {
    kind: explicitMediaKind(media) ?? media.kind ?? "file",
    url: media.url,
    name: media.name,
  };
}

/** Trigger a browser download for a same-origin (or CORS-ok) media URL. */
export async function downloadFromUrl(url: string, filename?: string): Promise<void> {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`download failed with HTTP ${response.status}`);
  }
  const name = filename || fileNameFromUrl(url);
  try {
    await saveDownload(await response.blob(), name);
  } catch {
    if (isDesktopShell() && /^https?:/i.test(url)) {
      startHttpAttachmentDownload(url);
      return;
    }
    throw new Error(`Could not save ${name}`);
  }
}

/**
 * Download a media URL and never leave the user without an answer.
 *
 * Every attachment tile, lightbox and message bubble used to repeat the same
 * fallback: build an `<a download target="_blank">` and click it. That does
 * nothing in a desktop webview - `target="_blank"` has no tab to open and
 * `download` is ignored on a remote URL - so a failed download looked like a
 * dead button. The honest fallback is the HTTP attachment path, which the
 * shell's own download handler turns into a native save dialog, and a visible
 * error when even that is impossible.
 */
export async function downloadMediaAttachment(
  url: string,
  filename?: string,
): Promise<boolean> {
  if (!url) return false;
  try {
    await downloadFromUrl(url, filename);
    return true;
  } catch {
    // ignore: the retry below is the real answer.
  }
  if (/^https?:/i.test(url) || url.startsWith("/")) {
    startHttpAttachmentDownload(url);
    return true;
  }
  const name = filename || fileNameFromUrl(url);
  publishNotification({
    level: "error",
    source: "session",
    toast: true,
    key: `download-failed:${name}`,
    title: i18n.t("notifications.downloadFailedTitle", {
      name,
      defaultValue: "Could not download {{name}}",
    }),
    detail: i18n.t("notifications.downloadFailedDetail", {
      defaultValue: "The file could not be read. Try again, or open it from the workspace.",
    }),
  });
  return false;
}
