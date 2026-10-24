// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

export type ActivityVerbosity = "essential" | "standard" | "debug";

export const ACTIVITY_VERBOSITY_KEY = "navin.activity.verbosity";

/** Standard is the default; Debug shows every row, Essential only the last few. */
export function loadActivityVerbosity(): ActivityVerbosity {
  try {
    const v = window.localStorage.getItem(ACTIVITY_VERBOSITY_KEY);
    if (v === "essential" || v === "debug" || v === "standard") return v;
  } catch {
    // storage unavailable
  }
  return "standard";
}

export function saveActivityVerbosity(v: ActivityVerbosity): void {
  try {
    window.localStorage.setItem(ACTIVITY_VERBOSITY_KEY, v);
  } catch {
    // ignore
  }
}

export function previewMaxFor(v: ActivityVerbosity, base: number): number {
  if (v === "essential") return Math.min(base, 2);
  if (v === "debug") return Number.MAX_SAFE_INTEGER;
  return base;
}
