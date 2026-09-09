// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Progressive Web App bootstrap for the Navin usage client (chat WebUI).
 *
 * Distinct from Dev mobile preview (`navin/mobile/`): this only registers the
 * shell service worker so the WebUI is installable. Cache is shell-only;
 * chat / API traffic is never served offline as AI.
 */

import { isDesktopShell } from "@/lib/desktop";

export function registerUsagePwa(): void {
  if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) {
    return;
  }
  // Skip Vite HMR: SW caching must not fight the dev server.
  if (!import.meta.env.PROD) {
    return;
  }
  // The desktop shell is already an installed app, and its bundle ships with
  // the release. A service worker there only adds a second cache with its own
  // lifetime, which is how an updated app kept painting the previous UI.
  if (isDesktopShell()) {
    return;
  }
  window.addEventListener("load", () => {
    void navigator.serviceWorker.register("/sw.js").catch(() => {
      // Install still works without a SW; ignore registration failures.
    });
  });
}
