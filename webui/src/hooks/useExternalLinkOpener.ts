// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useEffect } from "react";

import { openInOsBrowser } from "@/lib/api";
import { hrefActionFromClick } from "@/lib/external-url";
import { fileNameFromUrl, saveDownload, startHttpAttachmentDownload } from "@/lib/save-blob";

/**
 * Desktop WebView swallows ``target=_blank`` and popup calls. Route every
 * in-app hash (``#/new``, ``#/code``, ``#/tenders``, ``#/career``, ``#/trading``, ``#/crm``) into this window, every external http(s) link
 * through the OS browser (opener plugin or gateway), and every in-app file
 * URL through the same download path the rest of the UI uses, so a click
 * never navigates the window away on Windows, macOS or Linux.
 */
export function useExternalLinkOpener(token: string): void {
  useEffect(() => {
    const onClick = (event: MouseEvent) => {
      const action = hrefActionFromClick(event);
      if (!action) return;
      event.preventDefault();
      event.stopPropagation();
      if (action.kind === "internal") {
        window.location.hash = action.hash;
        return;
      }
      if (action.kind === "external") {
        void openInOsBrowser(token, action.href);
        return;
      }
      void (async () => {
        try {
          const res = await fetch(action.href);
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          await saveDownload(
            await res.blob(),
            action.filename || fileNameFromUrl(action.href),
          );
        } catch {
          startHttpAttachmentDownload(action.href);
        }
      })();
    };
    document.addEventListener("click", onClick);
    document.addEventListener("auxclick", onClick);
    return () => {
      document.removeEventListener("click", onClick);
      document.removeEventListener("auxclick", onClick);
    };
  }, [token]);
}
