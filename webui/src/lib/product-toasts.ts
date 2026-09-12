// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/** Admin-published release card. Hidden when the install toast already covers it. */
export function isReleaseAnnouncement(item: { id: string; kind?: string }): boolean {
  return item.kind === "release" || item.id.startsWith("release-");
}

/**
 * Later / X on the update card. Persisted so a focus flicker or a token
 * refresh cannot bring the same version back two seconds later.
 */
export const DISMISSED_UPDATE_TOAST_KEY = "navin.update.dismissed-toast";
export const UPDATE_TOAST_SNOOZE_MS = 7 * 24 * 60 * 60 * 1000;
/** Only re-check after the window was actually away, not on every focus. */
export const UPDATE_RECHECK_AFTER_HIDDEN_MS = 30 * 60 * 1000;

export type DismissedUpdateToast = {
  version: string;
  until: number;
};

export type UpdateToastInfo = {
  available: boolean;
  latestVersion?: string;
  currentVersion?: string;
};

let memoryDismissed: DismissedUpdateToast | null = null;

export function resetDismissedUpdateToast() {
  memoryDismissed = null;
}

export function parseDismissedUpdateToast(raw: string | null): DismissedUpdateToast | null {
  if (!raw) return null;
  const trimmed = raw.trim();
  if (!trimmed) return null;
  try {
    const parsed = JSON.parse(trimmed) as unknown;
    if (!parsed || typeof parsed !== "object") return null;
    const version = String((parsed as { version?: unknown }).version ?? "").trim();
    const until = (parsed as { until?: unknown }).until;
    if (!version || typeof until !== "number" || !Number.isFinite(until)) return null;
    return { version, until };
  } catch {
    return null;
  }
}

export function isUpdateToastDismissed(
  version: string | undefined,
  dismissed: DismissedUpdateToast | null,
  now: number = Date.now(),
): boolean {
  const latest = version?.trim();
  if (!latest || !dismissed) return false;
  if (dismissed.version !== latest) return false;
  return dismissed.until > now;
}

function browserStorage(): Storage | null {
  try {
    return globalThis.localStorage ?? null;
  } catch {
    return null;
  }
}

export function readDismissedUpdateToast(): DismissedUpdateToast | null {
  if (memoryDismissed) return memoryDismissed;
  const storage = browserStorage();
  if (!storage) return null;
  try {
    memoryDismissed = parseDismissedUpdateToast(
      storage.getItem(DISMISSED_UPDATE_TOAST_KEY),
    );
  } catch {
    return memoryDismissed;
  }
  return memoryDismissed;
}

export function dismissUpdateToast(
  version: string,
  now: number = Date.now(),
  snoozeMs: number = UPDATE_TOAST_SNOOZE_MS,
): DismissedUpdateToast {
  const next: DismissedUpdateToast = {
    version: version.trim(),
    until: now + snoozeMs,
  };
  memoryDismissed = next;
  try {
    browserStorage()?.setItem(DISMISSED_UPDATE_TOAST_KEY, JSON.stringify(next));
  } catch {
    // Memory still wins for this session.
  }
  return next;
}

/** Decide whether the corner card should stay, hide, or keep its object. */
export function nextAvailableUpdate<T extends UpdateToastInfo>(
  current: T | null,
  info: T,
  dismissed: DismissedUpdateToast | null,
  now: number = Date.now(),
): T | null {
  if (!info.available) {
    return current === null ? current : null;
  }
  if (isUpdateToastDismissed(info.latestVersion, dismissed, now)) {
    return current === null ? current : null;
  }
  if (
    current
    && current.latestVersion === info.latestVersion
    && current.currentVersion === info.currentVersion
  ) {
    return current;
  }
  return info;
}
