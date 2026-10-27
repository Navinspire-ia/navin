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

const EFFORT_KEY = "navin.thread-reasoning-efforts";
const LAST_EFFORT_KEY = "navin.thread-reasoning-last-pick";

/**
 * Reasoning effort chosen in a chat, keyed by chat then preset, so reopening
 * the chat restores both the model and the effort the user left it on.
 */
function readEfforts(): Record<string, Record<string, string>> {
  try {
    const raw = window.localStorage.getItem(EFFORT_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : {};
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return {};
    const out: Record<string, Record<string, string>> = {};
    for (const [chat, value] of Object.entries(parsed)) {
      if (typeof value !== "object" || value === null || Array.isArray(value)) continue;
      const row: Record<string, string> = {};
      for (const [preset, effort] of Object.entries(value)) {
        if (typeof effort === "string") row[preset] = effort;
      }
      out[chat] = row;
    }
    return out;
  } catch {
    return {};
  }
}

export function loadThreadEffort(chatId: string | null, preset: string): string | null {
  if (chatId) {
    const value = readEfforts()[chatId]?.[preset];
    if (value !== undefined) return value;
  }
  try {
    const raw = window.localStorage.getItem(LAST_EFFORT_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : null;
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      const value = (parsed as Record<string, unknown>)[preset];
      if (typeof value === "string") return value;
    }
  } catch {
    // ignore
  }
  return null;
}

export function saveThreadEffort(chatId: string | null, preset: string, effort: string): void {
  try {
    if (chatId) {
      const all = readEfforts();
      const prev = all[chatId] ?? {};
      delete all[chatId];
      all[chatId] = { ...prev, [preset]: effort };
      const keys = Object.keys(all);
      for (const stale of keys.slice(0, Math.max(0, keys.length - MAX_ENTRIES))) {
        delete all[stale];
      }
      window.localStorage.setItem(EFFORT_KEY, JSON.stringify(all));
    }
    const raw = window.localStorage.getItem(LAST_EFFORT_KEY);
    const last: Record<string, string> = raw ? JSON.parse(raw) : {};
    last[preset] = effort;
    window.localStorage.setItem(LAST_EFFORT_KEY, JSON.stringify(last));
  } catch {
    // The effort just will not survive a reload.
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
