// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { isDesktopAppHttpHost, isTauriAppHost, isTauriCustomScheme } from "./desktop";

/** ``window.open`` is a no-op in the Tauri WebView. Only a real browser may use it. */
export function shouldUseWindowOpenFallback(desktopShell: boolean): boolean {
  return !desktopShell;
}

/** Absolute http(s) URL that is not this editor. */

export function externalHttpUrl(
  href: string,
  selfOrigin: string = typeof window !== "undefined" ? window.location.origin : "",
): string | null {
  const value = href.trim();
  if (!/^https?:\/\//i.test(value)) return null;
  try {
    const url = new URL(value);
    if (isTauriAppHost(url.hostname)) return null;
    if (selfOrigin && url.origin === selfOrigin) return null;
    return url.href;
  } catch {
    return null;
  }
}

/** Hash the IDE must keep (``#/new``, ``#/chat``, ``#/code``, ``#/tenders``, ``#/career``, ``#/trading``, ``#/marketing``, ``#/leads``, ``#/crm``). Never hand this to the OS opener. */
export function ideNavigationHash(
  href: string,
  selfOrigin: string = typeof window !== "undefined" ? window.location.origin : "",
): string | null {
  const value = href.trim();
  if (!value || /^\s*(?:blob|data|javascript|mailto|tel):/i.test(value)) return null;
  if (value.startsWith("#")) return value.length > 1 ? value : null;
  try {
    const url = new URL(value, selfOrigin || "http://127.0.0.1");
    if (isTauriCustomScheme(url.protocol)) {
      return url.hash && url.hash !== "#" ? url.hash : null;
    }
    if (url.protocol !== "http:" && url.protocol !== "https:") return null;
    const sameOrigin = Boolean(selfOrigin && url.origin === selfOrigin);
    if (!sameOrigin && !isDesktopAppHttpHost(url.hostname) && !isTauriAppHost(url.hostname)) {
      return null;
    }
    if (url.hash && url.hash !== "#") return url.hash;
    const path = url.pathname.replace(/\/+$/, "") || "/";
    if (path.startsWith("/api/")) return null;
    if (path === "/" || path.includes(".")) return null;
    return `#${path}${url.search}`;
  } catch {
    return null;
  }
}

function clickTarget(event: MouseEvent): Element | null {
  const target = event.target;
  return target instanceof Element ? target : null;
}

function resolveHref(raw: string): string {
  const value = raw.trim();
  if (!value) return "";
  try {
    return new URL(value, typeof window !== "undefined" ? window.location.href : "http://local").href;
  } catch {
    return value;
  }
}

/** Same-origin URL that must download, not navigate the app away. */
export function attachmentDownloadUrl(
  href: string,
  selfOrigin: string = typeof window !== "undefined" ? window.location.origin : "",
): string | null {
  const absolute = resolveHref(href);
  try {
    const url = new URL(absolute);
    if (selfOrigin && url.origin !== selfOrigin) return null;
    const path = url.pathname;
    if (
      /\/file-download(?:\/|$)/i.test(path)
      || /\/api\/(?:media|notes\/file)\b/i.test(path)
    ) {
      return url.href;
    }
  } catch {
    return null;
  }
  return null;
}

export type ClickedHref =
  | { kind: "internal"; hash: string }
  | { kind: "external"; href: string }
  | { kind: "download"; href: string; filename?: string };

/** Decide whether a click on a link or `[data-open-url]` should leave the app. */
export function hrefActionFromClick(event: MouseEvent): ClickedHref | null {
  if (event.defaultPrevented) return null;
  if (event.button !== 0 && event.button !== 1) return null;
  const el = clickTarget(event);
  if (!el) return null;

  const openUrl = el.closest("[data-open-url]")?.getAttribute("data-open-url");
  if (openUrl) {
    const internal = ideNavigationHash(openUrl);
    if (internal) return { kind: "internal", hash: internal };
    const external = externalHttpUrl(openUrl);
    if (external) return { kind: "external", href: external };
    const download = attachmentDownloadUrl(openUrl);
    if (download) return { kind: "download", href: download };
  }

  const anchor = el.closest("a[href]");
  if (!(anchor instanceof HTMLAnchorElement)) return null;
  const raw = anchor.getAttribute("href") || "";
  // A blob: or data: anchor is a download already in flight (saveBlobInBrowser
  // clicks exactly such an anchor). Intercepting it re-entered the download
  // path in a loop: preventDefault killed the real save, the handler re-saved
  // the blob, which clicked a new blob anchor, forever.
  if (/^\s*(?:blob|data|javascript):/i.test(raw)) return null;
  const filename = anchor.getAttribute("download") || undefined;
  if (anchor.hasAttribute("download")) {
    const download = attachmentDownloadUrl(raw) || resolveHref(raw);
    return { kind: "download", href: download, filename };
  }
  const download = attachmentDownloadUrl(raw);
  if (download) return { kind: "download", href: download, filename };
  const internal = ideNavigationHash(raw);
  if (internal) return { kind: "internal", hash: internal };
  const external = externalHttpUrl(raw);
  if (external) return { kind: "external", href: external };
  return null;
}

/** Decide whether a click on an `<a>` should open in the OS browser. */
export function externalHrefFromClick(event: MouseEvent): string | null {
  const action = hrefActionFromClick(event);
  return action?.kind === "external" ? action.href : null;
}
