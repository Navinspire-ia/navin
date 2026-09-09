// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { isTauriAppHost } from "@/lib/desktop";

/** Detect when a Preview URL would load Navin's own editor UI. */

const LOOPBACK = new Set(["127.0.0.1", "localhost", "::1", "[::1]"]);

/** Common local app ports (project servers). Never include Navin :8765. */
const PROJECT_PORT_CANDIDATES = [
  3000, 3001, 3100, 4173, 4200, 4321, 5000, 5174, 5175, 5176, 5180, 8000, 8080,
  8081,
  5173, // last: often Navin Vite - only used if HTML is clearly NOT Navin
] as const;

/** HTML fingerprints unique enough for the Navin WebUI shell. */
const NAVIN_UI_MARKERS = [
  "data-navin-webui",
  "data-boot-copy",
  "navin-webui",
  "Loading Navin",
  "Couldn't reach navin",
  "Connexion à navin",
  "Chargement de Navin",
] as const;

/**
 * Ports Navin itself listens on, so Preview never iframes Navin inside Navin.
 *
 * The dev defaults are 8765 / 18790; the desktop shell deliberately uses its
 * own 8766 / 18791 so it can coexist with a source checkout. The shell also
 * slides to the next free port when those are taken, which is why the page's
 * own port is added at call time: whatever port served this page is Navin by
 * definition, and hard-coding a list can never keep up with a dynamic one.
 */
const NAVIN_EDITOR_PORTS = new Set(["8765", "8766", "18790", "18791"]);

/** True when `port` belongs to Navin: a well-known port, or the page's own. */
export function isNavinOwnPort(port: string): boolean {
  if (!port) return false;
  if (NAVIN_EDITOR_PORTS.has(port)) return true;
  if (typeof window === "undefined") return false;
  try {
    return portOf(new URL(window.location.href)) === port;
  } catch {
    return false;
  }
}

function normalizePreviewUrl(raw: string): string {
  const value = raw.trim();
  if (!value) return value;
  return /^https?:\/\//i.test(value) ? value : `http://${value}`;
}

function isLoopbackHost(hostname: string): boolean {
  const host = hostname.toLowerCase();
  return LOOPBACK.has(host) || isTauriAppHost(host);
}

function portOf(url: URL): string {
  return url.port || (url.protocol === "https:" ? "443" : "80");
}

/**
 * Sync guards: the editor itself (loopback + same port), or a Navin service port.
 *
 * ``localhost`` and ``127.0.0.1`` are different origins. Treating them as the
 * same host is what stops Preview from iframing Navin inside Navin.
 */
export function isNavinSelfPreviewUrlSync(raw: string): boolean {
  const value = normalizePreviewUrl(raw);
  if (!value) return false;
  try {
    const target = new URL(value);
    if (!isLoopbackHost(target.hostname || "")) return false;
    const targetPort = portOf(target);
    if (NAVIN_EDITOR_PORTS.has(targetPort)) return true;
    const self = new URL(window.location.href);
    if (target.origin === self.origin) return true;
    if (isLoopbackHost(self.hostname || "") && portOf(self) === targetPort) {
      return true;
    }
    return false;
  } catch {
    return false;
  }
}

async function fetchHtml(url: string): Promise<string | null> {
  try {
    const response = await fetch(url, {
      method: "GET",
      signal: AbortSignal.timeout(2000),
      redirect: "follow",
    });
    if (!response.ok) return null;
    const ctype = (response.headers.get("content-type") || "").toLowerCase();
    if (ctype && !ctype.includes("text/html") && !ctype.includes("application/xhtml")) {
      return null;
    }
    return (await response.text()).slice(0, 16_000);
  } catch {
    return null;
  }
}

function htmlLooksLikeNavin(html: string): boolean {
  return NAVIN_UI_MARKERS.some((marker) => html.includes(marker));
}

/**
 * True when the URL serves Navin's own shell (gateway or Vite editor).
 */
export async function isNavinSelfPreviewUrl(raw: string): Promise<boolean> {
  if (isNavinSelfPreviewUrlSync(raw)) return true;
  const value = normalizePreviewUrl(raw);
  if (!value) return false;
  let port: string;
  try {
    const target = new URL(value);
    if (!isLoopbackHost(target.hostname || "")) return false;
    port = portOf(target);
  } catch {
    return false;
  }
  const html = await fetchHtml(value);
  if (html) return htmlLooksLikeNavin(html);
  // Cross-origin fetch to the editor Vite often fails (CORS) while the iframe
  // would still load Navin. Fail closed on the editor port and on 5173.
  try {
    const selfPort = portOf(new URL(window.location.href));
    if (port === selfPort) return true;
  } catch {
    // ignore
  }
  return port === "5173";
}

/**
 * Find a local HTTP server that is NOT Navin (the project the user built).
 * Prefer ports other than 5173; only accept 5173 if HTML is clearly not Navin.
 */
export async function discoverProjectPreviewUrl(): Promise<string | null> {
  const self = new URL(window.location.href);
  const selfPort = portOf(self);
  for (const port of PROJECT_PORT_CANDIDATES) {
    const url = `http://127.0.0.1:${port}/`;
    if (String(port) === selfPort && isLoopbackHost(self.hostname || "")) {
      continue;
    }
    const html = await fetchHtml(url);
    if (!html) continue;
    if (htmlLooksLikeNavin(html)) continue;
    // Any other HTML page counts as a project preview candidate.
    return `http://127.0.0.1:${port}`;
  }
  return null;
}

/**
 * URL the OS browser should open from Preview. Prefer the typed project URL
 * over the telemetry proxy (another loopback port). Never return Navin itself.
 */
export function osBrowserPreviewUrl(options: {
  browserUrl?: string;
  browserSrc?: string | null;
  previewTargetPort?: number | null;
}): string {
  const typed = (options.browserUrl || "").trim();
  if (typed) {
    const normalized = normalizePreviewUrl(typed);
    if (!isNavinSelfPreviewUrlSync(normalized)) return normalized;
  }
  const port = options.previewTargetPort;
  if (typeof port === "number" && port > 0 && port <= 65535) {
    const reconstructed = `http://127.0.0.1:${port}`;
    if (!isNavinSelfPreviewUrlSync(reconstructed)) return reconstructed;
  }
  const src = (options.browserSrc || "").trim();
  if (src) {
    const normalized = normalizePreviewUrl(src);
    if (!isNavinSelfPreviewUrlSync(normalized)) return normalized;
  }
  return "";
}

export { htmlLooksLikeNavin, normalizePreviewUrl };
