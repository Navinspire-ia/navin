// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import type { BootstrapResponse } from "./types";
import { fetchWithTimeout } from "./http";
import {
  currentPageLocation,
  resolveGatewayWsUrl,
  resolveRuntimeProfile,
  type RuntimeProfile,
} from "./desktop";

const SECRET_STORAGE_KEY = "navin-webui.bootstrap-secret";
const URL_SECRET_PARAM = "bootstrapSecret";

export class BootstrapAuthRequiredError extends Error {
  constructor(message = "bootstrap authentication required") {
    super(message);
    this.name = "BootstrapAuthRequiredError";
  }
}

/** Read a previously saved bootstrap secret from localStorage. */
export function loadSavedSecret(): string {
  if (typeof window === "undefined") return "";
  try {
    return window.localStorage.getItem(SECRET_STORAGE_KEY) ?? "";
  } catch {
    return "";
  }
}

/** Persist the bootstrap secret so page reloads don't re-prompt. */
export function saveSecret(secret: string): void {
  try {
    window.localStorage.setItem(SECRET_STORAGE_KEY, secret);
  } catch {
    // ignore storage errors (private mode, etc.)
  }
}

/** Clear the saved bootstrap secret (sign out). */
export function clearSavedSecret(): void {
  try {
    window.localStorage.removeItem(SECRET_STORAGE_KEY);
  } catch {
    // ignore
  }
}

function consumeUrlHashParam(name: string): string {
  if (typeof window === "undefined") return "";
  const hash = window.location.hash || "";
  const queryStart = hash.indexOf("?");
  if (queryStart < 0) return "";

  const path = hash.slice(0, queryStart) || "#/";
  // Users often paste ``#/code?chat=...&token?hostChrome=1`` (second ``?``).
  // Treat every ``?`` after the first as ``&`` so flags still parse.
  const query = hash.slice(queryStart + 1).replace(/\?/g, "&");
  const params = new URLSearchParams(query);
  const value = params.get(name)?.trim() || "";
  if (!value) return "";

  params.delete(name);
  const nextQuery = params.toString();
  const nextHash = `${path}${nextQuery ? `?${nextQuery}` : ""}`;
  window.history.replaceState(
    null,
    "",
    `${window.location.pathname}${window.location.search}${nextHash}`,
  );
  return value;
}

export function consumeUrlBootstrapSecret(): string {
  return consumeUrlHashParam(URL_SECRET_PARAM);
}

/** Read and strip the `project` hash param set by `navin <dir>` / `navin webui --project`. */
export function consumeUrlProjectPath(): string {
  return consumeUrlHashParam("project");
}

/** Read and strip the `zoomBaseline` hash param set by the desktop shell. */
export function consumeUrlZoomBaseline(): string {
  return consumeUrlHashParam("zoomBaseline");
}

/** Read and strip the `uiZoom` flag the desktop shell always sets. */
export function consumeUrlUiZoomFlag(): string {
  return consumeUrlHashParam("uiZoom");
}

/**
 * Read and strip ``hostChrome`` from the hash. Used to preview the Tauri
 * host chrome (``native-host`` CSS, drag strip) in a plain browser tab.
 */
export function consumeUrlHostChromeFlag(): string {
  return consumeUrlHashParam("hostChrome");
}

const BOOTSTRAP_RETRY_ATTEMPTS = 3;
const BOOTSTRAP_RETRY_BASE_MS = 400;

function isRetryableBootstrapFailure(error: unknown): boolean {
  if (error instanceof BootstrapAuthRequiredError) return false;
  return true;
}

async function fetchBootstrapOnce(
  baseUrl: string,
  secret: string,
  timeoutMs?: number,
): Promise<BootstrapResponse> {
  const headers: Record<string, string> = {};
  if (secret) {
    headers["X-Navin-Auth"] = secret;
  }
  const res = await fetchWithTimeout(`${baseUrl}/webui/bootstrap`, {
    method: "GET",
    credentials: "same-origin",
    headers,
  }, timeoutMs);
  if (!res.ok) {
    if (res.status === 401 || res.status === 403) {
      throw new BootstrapAuthRequiredError(`bootstrap failed: HTTP ${res.status}`);
    }
    throw new Error(`bootstrap failed: HTTP ${res.status}`);
  }
  const body = (await res.json()) as BootstrapResponse;
  if (!body.token || !body.ws_path) {
    throw new Error("bootstrap response missing token or ws_path");
  }
  if (!body.api_token) {
    throw new BootstrapAuthRequiredError(
      "bootstrap authentication required: missing api_token",
    );
  }
  return body;
}

/**
 * Fetch a short-lived token + the WebSocket path from the gateway's
 * ``/webui/bootstrap`` endpoint.
 *
 * Retries transient gateway/proxy failures (HTTP 500, timeouts) so a
 * briefly busy or restarting process does not brick the first paint.
 */
export async function fetchBootstrap(
  baseUrl: string = "",
  secret: string = "",
  timeoutMs?: number,
): Promise<BootstrapResponse> {
  let lastError: unknown;
  for (let attempt = 0; attempt < BOOTSTRAP_RETRY_ATTEMPTS; attempt += 1) {
    try {
      return await fetchBootstrapOnce(baseUrl, secret, timeoutMs);
    } catch (error) {
      lastError = error;
      if (!isRetryableBootstrapFailure(error) || attempt === BOOTSTRAP_RETRY_ATTEMPTS - 1) {
        throw error;
      }
      await new Promise((resolve) => {
        setTimeout(resolve, BOOTSTRAP_RETRY_BASE_MS * (attempt + 1));
      });
    }
  }
  throw lastError instanceof Error ? lastError : new Error("bootstrap failed");
}

/** Derive a WebSocket URL from the current window location and the server-provided path.
 *
 * Keeps the path segment exactly as the server registered it: the root ``/``
 * stays ``/`` and non-root paths are not given an extra trailing slash. This
 * matters because some WS servers dispatch handshakes based on the literal
 * path, not a normalised form.
 *
 * The resolution itself lives in `lib/desktop.ts` so browser, Vite dev and the
 * packaged desktop shell all answer the same way, and so it can be unit tested
 * without a DOM.
 */
export function deriveWsUrl(
  wsPath: string,
  token: string,
  wsUrl?: string | null,
): string {
  const profile = deriveRuntimeProfile(wsUrl);
  return resolveGatewayWsUrl({
    wsPath,
    token,
    advertisedWsUrl: wsUrl ?? null,
    devBundle: import.meta.env.DEV,
    profile,
  });
}

/** Resolve the launch profile once from bootstrap and browser context. */
export function deriveRuntimeProfile(wsUrl?: string | null): RuntimeProfile {
  return resolveRuntimeProfile({
    advertisedWsUrl: wsUrl ?? null,
    devBundle: import.meta.env.DEV,
    location: currentPageLocation(),
  });
}
