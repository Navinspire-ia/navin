// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/** Calm folder tints that tell projects apart in the sidebar: muted tones,
 *  readable on the light and the dark theme, nothing loud. */
export const PROJECT_FOLDER_TINTS = [
  "text-sky-600 dark:text-sky-400/90",
  "text-emerald-600 dark:text-emerald-400/90",
  "text-amber-600 dark:text-amber-400/90",
  "text-violet-600 dark:text-violet-400/90",
  "text-rose-600 dark:text-rose-400/90",
  "text-teal-600 dark:text-teal-400/90",
  "text-indigo-600 dark:text-indigo-400/90",
  "text-orange-600 dark:text-orange-400/90",
] as const;

function normalize(key: string): string {
  return key.trim().replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
}

function slotOf(key: string): number {
  let hash = 0x811c9dc5;
  for (let i = 0; i < key.length; i += 1) {
    hash ^= key.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 0) % PROJECT_FOLDER_TINTS.length;
}

/** One tint per project, the same every time: the project's path picks its
 *  slot; two projects on the same slot take the next free one, in path order,
 *  so the colors do not move when the list is re-sorted by activity. */
export function projectFolderTints(keys: Array<string | null | undefined>): Map<string, string> {
  const unique = [...new Set(keys.filter((key): key is string => Boolean(key && key.trim())).map(normalize))].sort();
  const taken = new Set<number>();
  const bySlot = new Map<string, string>();
  for (const key of unique) {
    let slot = slotOf(key);
    if (taken.size < PROJECT_FOLDER_TINTS.length) {
      while (taken.has(slot)) slot = (slot + 1) % PROJECT_FOLDER_TINTS.length;
    }
    taken.add(slot);
    bySlot.set(key, PROJECT_FOLDER_TINTS[slot]);
  }
  const out = new Map<string, string>();
  for (const key of keys) {
    if (key && key.trim()) out.set(key, bySlot.get(normalize(key)) ?? PROJECT_FOLDER_TINTS[0]);
  }
  return out;
}
