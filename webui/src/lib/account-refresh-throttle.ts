// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Throttle for forced account validations (`/api/webui/account?refresh=1`).
 *
 * `refresh=1` bypasses the gateway's validate cache and hits navin.live, which
 * costs seconds. A module-level timestamp alone is not enough: a full page
 * reload wipes it, so every reload forced another blocking round-trip and left
 * the sidebar stuck on "Loading...". Dev reloads make this loud (vite has no
 * HMR boundary for the i18n JSON, so each locale edit is a full reload), but a
 * user hitting refresh twice paid the same cost. Persisting in sessionStorage
 * keeps the throttle across reloads while still forgetting it on a new tab.
 */

const STORAGE_KEY = "navin.account.forcedRefreshAt";

export const FORCED_REFRESH_MIN_MS = 30_000;

let inMemoryAt = 0;

function readStored(): number {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    const parsed = Number(raw);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : 0;
  } catch {
    // Private mode or a disabled store: the in-memory value still applies.
    return 0;
  }
}

/** Most recent forced validation known to this tab, across reloads. */
export function lastForcedRefreshAt(): number {
  return Math.max(inMemoryAt, readStored());
}

/** Record a forced validation as having just happened. */
export function markForcedRefresh(at: number = Date.now()): void {
  inMemoryAt = Math.max(inMemoryAt, at);
  try {
    sessionStorage.setItem(STORAGE_KEY, String(at));
  } catch {
    // Nothing to do: the in-memory throttle is still in effect.
  }
}

/**
 * True when a forced validation is allowed now. Claims the slot when it is, so
 * concurrent consumers of the hook cannot fan out into one request each.
 */
export function claimForcedRefresh(now: number = Date.now()): boolean {
  if (now - lastForcedRefreshAt() < FORCED_REFRESH_MIN_MS) return false;
  markForcedRefresh(now);
  return true;
}

/** Test seam: drop both the in-memory and the persisted timestamp. */
export function resetForcedRefreshThrottle(): void {
  inMemoryAt = 0;
  try {
    sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    // Ignored: nothing was persisted.
  }
}
