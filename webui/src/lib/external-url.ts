// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { isNavinShellUrl, isTauriAppHost, isTauriCustomScheme } from "./desktop";

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

/**
 * Hash paths the app's router knows (readShellRoute in App.tsx): exact ones,
 * and the ones that take a sub-path. Any other hash fell back to an empty Code
 * chat, so a link to a project dev server (localhost:3000/dashboard) or an
 * in-page anchor (#section) opened "a new chat" instead of the page.
 */
const IDE_ROUTE_PATHS: ReadonlySet<string> = new Set([
  "/new", "/settings", "/tools", "/apps", "/automations", "/skills",
  "/project", "/home", "/templates", "/evolve", "/risklens", "/premortem",
  "/code", "/dev", "/scraping", "/scrape", "/content", "/marketing", "/montage",
  "/ads", "/seo", "/campaigns", "/leads", "/tenders", "/career", "/trading",
  "/meeting", "/ops", "/notes", "/team", "/crm",
]);
const IDE_ROUTE_PREFIXES: readonly string[] = ["/chat/", "/tenders/", "/crm/"];

/** True when the app's router has a screen for this hash (``#/crm``, ``#/chat/<key>``, ``#/``). */
export function isIdeRouteHash(hash: string): boolean {
  const value = hash.startsWith("#") ? hash.slice(1) : hash;
  const path = value.split("?", 1)[0];
  if (path === "/") return true;
  if (IDE_ROUTE_PATHS.has(path)) return true;
  return IDE_ROUTE_PREFIXES.some((prefix) => path.startsWith(prefix) && path.length > prefix.length);
}

/** Hash the IDE must keep (``#/new``, ``#/chat``, ``#/code``, ``#/tenders``, ``#/career``, ``#/trading``, ``#/marketing``, ``#/leads``, ``#/crm``). Never hand this to the OS opener. */
export function ideNavigationHash(
  href: string,
  selfOrigin: string = typeof window !== "undefined" ? window.location.origin : "",
): string | null {
  const value = href.trim();
  if (!value || /^\s*(?:blob|data|javascript|mailto|tel):/i.test(value)) return null;
  if (value.startsWith("#")) return isIdeRouteHash(value) ? value : null;
  try {
    const url = new URL(value, selfOrigin || "http://127.0.0.1");
    if (isTauriCustomScheme(url.protocol)) {
      return url.hash && isIdeRouteHash(url.hash) ? url.hash : null;
    }
    if (url.protocol !== "http:" && url.protocol !== "https:") return null;
    // Only Navin itself: this page, the desktop WebView host or a Navin port on
    // loopback. A project served on localhost:3000 is a website for the browser.
    const sameOrigin = Boolean(selfOrigin && url.origin === selfOrigin);
    if (!sameOrigin && !isTauriAppHost(url.hostname) && !isNavinShellUrl(url.href, selfOrigin)) {
      return null;
    }
    if (url.hash && url.hash !== "#") return isIdeRouteHash(url.hash) ? url.hash : null;
    const path = url.pathname.replace(/\/+$/, "") || "/";
    if (path.startsWith("/api/")) return null;
    if (path === "/" || path.includes(".")) return null;
    const hash = `#${path}${url.search}`;
    return isIdeRouteHash(hash) ? hash : null;
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
  | { kind: "download"; href: string; filename?: string }
  /** In-page anchor (``#section``, a footnote): scroll to it, never touch the route. */
  | { kind: "anchor"; id: string };

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
  // A bare hash that is no screen of the app: letting it through changed the
  // route, and the router fell back to an empty Code chat.
  const bareHash = raw.trim();
  if (bareHash.startsWith("#")) {
    let id = bareHash.slice(1);
    try {
      id = decodeURIComponent(id);
    } catch (error) {
      // A malformed escape ("#100%") stays the id as written; anything else is a bug.
      if (!(error instanceof URIError)) throw error;
    }
    return { kind: "anchor", id };
  }
  return null;
}

/** Decide whether a click on an `<a>` should open in the OS browser. */
export function externalHrefFromClick(event: MouseEvent): string | null {
  const action = hrefActionFromClick(event);
  return action?.kind === "external" ? action.href : null;
}
