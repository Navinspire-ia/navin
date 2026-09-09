// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Per-conversation model choice.
 *
 * Selecting a model in the composer pins it to the current chat and records it
 * as the last pick, so a chat with no pin of its own still opens on the model
 * the user last chose. It deliberately does not touch
 * `agents.defaults.model_preset`: that account-wide default is what other
 * sessions fall back to, and writing it from the composer switched the model
 * of chats that were already running.
 */

const STORAGE_KEY = "navin.thread-model-presets";
const LAST_PICK_KEY = "navin.thread-model-last-pick";
const MAX_ENTRIES = 200;

function readAll(): Record<string, string> {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return {};
    const entries: Record<string, string> = {};
    for (const [key, value] of Object.entries(parsed)) {
      if (typeof value === "string" && value) entries[key] = value;
    }
    return entries;
  } catch {
    return {};
  }
}

function writeAll(entries: Record<string, string>): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(entries));
  } catch {
    // Quota or private mode: the pin just will not survive a reload.
  }
}

export function loadThreadModelPreset(chatId: string): string | null {
  return readAll()[chatId] ?? null;
}

/** Model the user picked last, for a chat that has no pin of its own. */
export function loadLastModelPick(): string | null {
  try {
    return window.localStorage.getItem(LAST_PICK_KEY) || null;
  } catch {
    return null;
  }
}

export function saveLastModelPick(preset: string | null): void {
  try {
    if (preset) window.localStorage.setItem(LAST_PICK_KEY, preset);
    else window.localStorage.removeItem(LAST_PICK_KEY);
  } catch {
    // The pick just will not survive a reload.
  }
}

export function saveThreadModelPreset(chatId: string, preset: string | null): void {
  const entries = readAll();
  if (preset) {
    delete entries[chatId]; // re-insert so recency matches insertion order
    entries[chatId] = preset;
  } else {
    delete entries[chatId];
  }
  const keys = Object.keys(entries);
  for (const stale of keys.slice(0, Math.max(0, keys.length - MAX_ENTRIES))) {
    delete entries[stale];
  }
  writeAll(entries);
}
