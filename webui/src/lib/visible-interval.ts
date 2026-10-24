// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * ``setInterval`` that stays quiet while the window is hidden.
 *
 * A background refresh only matters to someone looking at it. While the
 * desktop window is minimized or the tab is in the background the ticks are
 * skipped, and one refresh runs when the window shows again so the panel is
 * current at once. Returns the cleanup function.
 */
export function setVisibleInterval(callback: () => void, intervalMs: number): () => void {
  const hidden = () => typeof document !== "undefined" && document.visibilityState === "hidden";
  let missed = false;
  const timer = window.setInterval(() => {
    if (hidden()) {
      missed = true;
      return;
    }
    callback();
  }, intervalMs);
  const onVisibility = () => {
    if (!hidden() && missed) {
      missed = false;
      callback();
    }
  };
  document.addEventListener("visibilitychange", onVisibility);
  return () => {
    window.clearInterval(timer);
    document.removeEventListener("visibilitychange", onVisibility);
  };
}
