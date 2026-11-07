// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Per-chat prompt history recalled with ArrowUp / ArrowDown in the composer.
 *
 * Entries are what the user typed, with pasted blocks kept as their
 * `[Pasted Content N chars]` placeholders, never the expanded text: a paste
 * of several megabytes must not land in localStorage. Each entry and the whole
 * list are capped, and the key is removed when its chat is deleted.
 */

export const PROMPT_HISTORY_LIMIT = 100;
export const PROMPT_HISTORY_ENTRY_MAX_CHARS = 4000;
export const PROMPT_HISTORY_TOTAL_MAX_CHARS = 100_000;

export function promptHistoryKey(chatKey: string | null): string {
  return `navin:prompt-history:${chatKey ?? "new-chat"}`;
}

/** Keep the newest entries within the count and total size caps. */
export function capPromptHistory(entries: string[]): string[] {
  const kept: string[] = [];
  let total = 0;
  for (let i = entries.length - 1; i >= 0 && kept.length < PROMPT_HISTORY_LIMIT; i -= 1) {
    const entry = entries[i].slice(0, PROMPT_HISTORY_ENTRY_MAX_CHARS);
    if (total + entry.length > PROMPT_HISTORY_TOTAL_MAX_CHARS) break;
    total += entry.length;
    kept.unshift(entry);
  }
  return kept;
}

export function readPromptHistory(chatKey: string | null): string[] {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(promptHistoryKey(chatKey)) ?? "[]");
    return Array.isArray(parsed)
      ? capPromptHistory(parsed.filter((item): item is string => typeof item === "string"))
      : [];
  } catch {
    return [];
  }
}

/** Append a prompt (collapsed text) and persist; returns the new list. */
export function appendPromptHistory(chatKey: string | null, history: string[], prompt: string): string[] {
  const entry = prompt.slice(0, PROMPT_HISTORY_ENTRY_MAX_CHARS);
  const next = history[history.length - 1] === entry ? history : capPromptHistory([...history, entry]);
  try { localStorage.setItem(promptHistoryKey(chatKey), JSON.stringify(next)); } catch { /* storage unavailable */ }
  return next;
}

/** Drop the stored history of a deleted chat. */
export function forgetPromptHistory(chatKey: string): void {
  try { localStorage.removeItem(promptHistoryKey(chatKey)); } catch { /* storage unavailable */ }
}
