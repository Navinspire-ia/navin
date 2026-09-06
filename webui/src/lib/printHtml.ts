/**
 * Load HTML into a hidden iframe and open the browser print dialog, where the
 * user can pick "Save as PDF". An iframe (not window.open) avoids popup
 * blockers and keeps the app visible behind the dialog.
 *
 * WebKitGTK, the Linux desktop webview, implements `window.print()` as a
 * no-op: it neither prints nor throws, so a Print button there simply did
 * nothing. When no `beforeprint` fires within a short grace period we assume
 * that is what happened and save the document instead, which is the outcome
 * the user was after anyway.
 */

import i18n from "@/i18n";
import { publishNotification } from "@/lib/notification-bus";
import { saveTextFile } from "@/lib/save-blob";

/** Long enough for a real print dialog to announce itself, short enough not to stall. */
const PRINT_DETECT_MS = 1_200;

export function printHtmlDocument(html: string, filename = "document.html"): void {
  const iframe = document.createElement("iframe");
  iframe.style.position = "fixed";
  iframe.style.right = "0";
  iframe.style.bottom = "0";
  iframe.style.width = "0";
  iframe.style.height = "0";
  iframe.style.border = "0";
  iframe.setAttribute("aria-hidden", "true");
  iframe.srcdoc = html;
  iframe.onload = () => {
    const frameWindow = iframe.contentWindow;
    if (!frameWindow) return;
    let printed = false;
    const markPrinted = () => {
      printed = true;
    };
    frameWindow.addEventListener("beforeprint", markPrinted);
    frameWindow.addEventListener("afterprint", () => {
      markPrinted();
      iframe.remove();
    });
    frameWindow.focus();
    try {
      frameWindow.print();
    } catch {
      // WebView2 can reject a print from a detached frame; handled below.
    }
    window.setTimeout(() => {
      if (printed) return;
      iframe.remove();
      fallbackToDownload(html, filename);
    }, PRINT_DETECT_MS);
    window.setTimeout(() => iframe.remove(), 120_000);
  };
  document.body.appendChild(iframe);
}

function fallbackToDownload(html: string, filename: string): void {
  saveTextFile(filename, html, "text/html;charset=utf-8");
  publishNotification({
    level: "info",
    source: "session",
    toast: true,
    key: "print:unavailable",
    title: i18n.t("notifications.printUnavailableTitle", {
      defaultValue: "Printing is not available here",
    }),
    detail: i18n.t("notifications.printUnavailableDetail", {
      defaultValue:
        "Navin saved the document instead. Open it in your browser and print from there.",
    }),
  });
}
