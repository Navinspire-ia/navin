// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Hand bytes to the user as a file download.
 *
 * Two worlds share this helper:
 *
 * - A plain browser (or Vite on :5173): an <a download> on a blob URL. The
 *   URL must stay alive after the click; revoking it in the same tick is
 *   what made every chat download look dead.
 * - The packaged desktop shell (Windows WebView2, macOS WKWebView, Linux
 *   WebKitGTK): those webviews ignore <a download> on blob URLs, and some
 *   also swallow Content-Disposition. The shell exposes `save_bytes`, which
 *   opens a native save dialog and writes the file. Same path on every OS.
 */

import i18n from "@/i18n";
import { publishNotification } from "@/lib/notification-bus";
import { isDesktopShell, tauriInvoke } from "@/lib/desktop";

/** Long enough for a slow save dialog, short enough not to leak a session. */
const REVOKE_DELAY_MS = 60_000;

// Re-exported because most download call sites reach for it from here.
export { isDesktopShell } from "@/lib/desktop";

/** Last segment of a filesystem path, for naming a download nobody named. */
export function fileNameFromPath(path: string, fallback = "download"): string {
  const segment = path.split(/[/\\]/).filter(Boolean).pop();
  return segment || fallback;
}

/** Same, for a URL: the query and fragment are not part of the name. */
export function fileNameFromUrl(url: string, fallback = "download"): string {
  return fileNameFromPath(url.split(/[?#]/)[0], fallback);
}

/**
 * Tell the user their file landed, and where.
 *
 * Desktop knows the exact path (the user picked it in the dialog). A browser
 * never exposes the destination, so the honest message is the browser's own
 * Downloads folder. `toast: true` makes it pop briefly; it also stays in the
 * bell so the path can be read again later.
 */
function notifyDownloadDone(filename: string, path: string | null): void {
  const name = fileNameFromPath(filename);
  publishNotification({
    level: "success",
    source: "session",
    toast: true,
    key: `download:${name}:${path ?? "browser"}`,
    title: path
      ? i18n.t("notifications.downloadSaved", {
          name,
          defaultValue: "{{name}} saved",
        })
      : i18n.t("notifications.downloadStarted", {
          name,
          defaultValue: "Downloading {{name}}",
        }),
    detail:
      path
      ?? i18n.t("notifications.downloadBrowserFolder", {
        defaultValue: "Your browser saves it in its Downloads folder.",
      }),
  });
}

type DesktopSaveResult =
  | { status: "saved"; path: string | null }
  | { status: "cancelled" }
  | { status: "unavailable" };

/**
 * Native save dialog in the desktop shell (Windows, macOS, Linux).
 *
 * Distinguishes "the user cancelled the dialog" (do nothing, silently) from
 * "the shell is not there / failed" (fall back to the browser path). Newer
 * shells return the saved path; older ones only returned true.
 */
async function desktopSave(blob: Blob, filename: string): Promise<DesktopSaveResult> {
  const invoke = tauriInvoke();
  if (!invoke) return { status: "unavailable" };
  const bytes = new Uint8Array(await blob.arrayBuffer());
  try {
    const saved = await invoke("save_bytes", {
      filename: fileNameFromPath(filename),
      contents: Array.from(bytes),
    });
    if (typeof saved === "string" && saved) return { status: "saved", path: saved };
    if (saved === true) return { status: "saved", path: null };
    return { status: "cancelled" };
  } catch {
    return { status: "unavailable" };
  }
}

/** Browser-only <a download>. Callers that already tried the desktop path use this. */
export function saveBlobInBrowser(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.rel = "noopener";
  // Firefox only honours a click on an anchor that is in the document.
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), REVOKE_DELAY_MS);
  notifyDownloadDone(filename, null);
}

/**
 * Save bytes. Desktop: native dialog on Windows, macOS and Linux, then a
 * notification with the exact path. Browser: blob URL that stays alive past
 * the click, then a notification pointing at the Downloads folder.
 */
export async function saveDownload(blob: Blob, filename: string): Promise<void> {
  const desktop = await desktopSave(blob, filename);
  if (desktop.status === "saved") {
    notifyDownloadDone(filename, desktop.path);
    return;
  }
  // An explicit cancel in the native dialog is an answer, not a failure:
  // no browser fallback, no notification.
  if (desktop.status === "cancelled") return;
  saveBlobInBrowser(blob, filename);
}

/** Sync wrapper: desktop is kicked off, a browser saves immediately. */
export function saveBlob(blob: Blob, filename: string): void {
  if (tauriInvoke()) {
    void saveDownload(blob, filename);
    return;
  }
  saveBlobInBrowser(blob, filename);
}

export function saveTextFile(filename: string, content: string, mime: string): void {
  void saveDownload(new Blob([content], { type: mime }), filename);
}

/**
 * Last-resort HTTP download for a URL that already carries auth (token in
 * the query). The desktop `on_download` handler turns Content-Disposition
 * into a save dialog; a browser just follows the attachment.
 */
export function startHttpAttachmentDownload(url: string): void {
  const frame = document.createElement("iframe");
  frame.setAttribute("hidden", "");
  frame.setAttribute("aria-hidden", "true");
  document.body.appendChild(frame);
  frame.src = url;
  window.setTimeout(() => {
    frame.remove();
  }, 60_000);
  // On desktop this opens the shell's own save dialog whose outcome is
  // unknown here; only a plain browser download is worth announcing.
  if (!isDesktopShell()) {
    notifyDownloadDone(fileNameFromUrl(url), null);
  }
}
