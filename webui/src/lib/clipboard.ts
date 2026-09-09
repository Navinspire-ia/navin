// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Copy text to the clipboard from anywhere, browser or desktop shell.
 *
 * Three paths, tried in order, because no single one works everywhere:
 *
 * 1. `navigator.clipboard.writeText`. Available on every modern browser and,
 *    because the desktop shell serves the UI from `http://127.0.0.1`, inside
 *    the webview too - loopback counts as a secure context. WKWebView still
 *    rejects it outside a direct user gesture.
 * 2. The Tauri clipboard command, when the shell granted it. Absent today, so
 *    this is a no-op that costs nothing and picks the capability up the moment
 *    it is added, instead of needing every call site changed again.
 * 3. A hidden textarea plus `document.execCommand("copy")`. Deprecated, still
 *    the only path that works in an old WebKitGTK.
 *
 * Call sites used to reach for `navigator.clipboard` directly and swallow the
 * rejection, which is why copy buttons looked dead on Linux and macOS desktop
 * with no error anywhere. `copyTextOrNotify` is the form to use in the UI: it
 * tells the user when the copy did not happen.
 */

import i18n from "@/i18n";
import { publishNotification } from "@/lib/notification-bus";
import { tauriInvoke } from "@/lib/desktop";

export async function copyTextToClipboard(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // Fall through to the legacy path for browsers/WebViews where the
    // Clipboard API exists but rejects outside a secure context.
  }

  if (await copyTextWithShell(text)) return true;

  return copyTextWithTextarea(text);
}

/**
 * Copy, and tell the user when it failed.
 *
 * A copy button that does nothing is worse than one that says why: the user
 * retries, pastes stale content, and never learns the clipboard was refused.
 */
export async function copyTextOrNotify(text: string): Promise<boolean> {
  const copied = await copyTextToClipboard(text);
  if (copied) return true;
  publishNotification({
    level: "error",
    source: "session",
    toast: true,
    key: "clipboard:blocked",
    title: i18n.t("notifications.clipboardBlockedTitle", {
      defaultValue: "Copy blocked",
    }),
    detail: i18n.t("notifications.clipboardBlockedDetail", {
      defaultValue:
        "This window would not let Navin write to the clipboard. Select the text and copy it by hand.",
    }),
  });
  return false;
}

/** Tauri clipboard plugin, if a build ever grants the capability. */
async function copyTextWithShell(text: string): Promise<boolean> {
  const invoke = tauriInvoke();
  if (!invoke) return false;
  try {
    await invoke("plugin:clipboard-manager|write_text", { label: text, text });
    return true;
  } catch {
    return false;
  }
}

function copyTextWithTextarea(text: string): boolean {
  if (typeof document.execCommand !== "function") {
    return false;
  }

  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.top = "-9999px";
  textarea.style.left = "-9999px";
  textarea.style.opacity = "0";

  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();
  textarea.setSelectionRange(0, textarea.value.length);

  try {
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    document.body.removeChild(textarea);
  }
}
