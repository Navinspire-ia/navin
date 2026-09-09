// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * How code outside the React tree reports something worth telling the user.
 *
 * The API client is a plain module, so it cannot reach the notification
 * provider through context. It publishes here instead, and the provider
 * subscribes once at mount.
 *
 * This exists mainly so that every gateway call funnels its failures into one
 * place. Before it, a failing request showed up as a bare string next to
 * whichever button triggered it - or, in a fair number of call sites, nowhere
 * at all.
 */

import { ApiError } from "./api-error";
import { isTransportError } from "./http";
import type { NotificationInput } from "./notifications";

type Listener = (input: NotificationInput) => void;

const listeners = new Set<Listener>();

export function onNotification(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function publishNotification(input: NotificationInput): void {
  for (const listener of listeners) {
    try {
      listener(input);
    } catch {
      // A broken subscriber must not take down the call that reported.
    }
  }
}

/** Session keys and query strings make every call look unique; they must go. */
function routeLabel(url: string): string {
  const path = url.split("?", 1)[0];
  return path
    .replace(/^https?:\/\/[^/]+/, "")
    .replace(/\/api\/sessions\/[^/]+/, "/api/sessions/…")
    .replace(/\/$/, "");
}

/** Poll / status routes whose failures are not worth a notification. */
function isAmbientPollRoute(route: string): boolean {
  if (route === "/api/settings/pairing") return true;
  // Plan panel GET; mutations live under /board/update and stay visible.
  if (route === "/api/sessions/…/board") return true;
  // Background RAM/disk pressure poll for the bottom-left toast strip.
  if (route === "/api/webui/runtime/health") return true;
  // Sidebar plan chip; a 500 here is not something the user can act on.
  if (route === "/api/webui/account") return true;
  // Linter badges are ambient decoration: the editor works fine without
  // them, and their failure repeats on every open file.
  if (route.startsWith("/api/webui/diagnostics")) return true;
  return false;
}

/**
 * Turn a failed gateway call into a notification, or null to stay silent.
 *
 * Request failures stay silent: routes and HTTP codes are operator noise.
 * The UI degrades quietly (disabled controls, status bar) instead of a toast
 * the user cannot act on.
 */
export function describeRequestFailure(url: string, error: unknown): NotificationInput | null {
  if (error instanceof DOMException && error.name === "AbortError") return null;
  if (error instanceof Error && error.name === "AbortError") return null;

  const route = routeLabel(url);

  // Background pollers (plan board, pairing list, …) retry on their own. A
  // transient 500 while the gateway restarts would otherwise spam the bell
  // with "La passerelle a rencontré une erreur" that the user cannot act on.
  if (isAmbientPollRoute(route)) {
    return null;
  }

  // Pure transport conditions stay out of the notification centre: a slow or
  // unreachable gateway is ambient connection state, shown live in the status
  // bar, not an event about the user's work. Notifying turned every blip into
  // a pile of "unreachable / restored" entries burying the notifications that
  // matter (agent runs, board moves, file operations).
  if (isTransportError(error)) {
    return null;
  }

  // Never surface gateway/API failures to the user: routes and HTTP codes
  // are operator noise. The UI degrades quietly (disabled controls, status
  // bar) instead of a toast the user cannot act on.
  if (error instanceof ApiError) {
    return null;
  }

  // fetch reports every transport problem as the same opaque TypeError - an
  // unreachable gateway. Connection state, so silent here (see above).
  if (error instanceof TypeError) {
    return null;
  }

  return null;
}

export function reportRequestFailure(url: string, error: unknown): void {
  const input = describeRequestFailure(url, error);
  if (input) publishNotification(input);
}
