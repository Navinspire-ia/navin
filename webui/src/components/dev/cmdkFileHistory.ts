// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/** Per-file Cmd+K edit history (applied replacements), persisted locally. */

export type CmdkFileEdit = {
  at: number;
  instruction: string;
  before: string;
  after: string;
};

const PREFIX = "navin.dev.cmdkEdits:";
const MAX_ENTRIES = 20;
/** In-memory fallback when ``localStorage`` is unavailable (tests / SSR). */
const memoryStore = new Map<string, string>();

function storageGet(key: string): string | null {
  try {
    if (typeof localStorage !== "undefined") {
      return localStorage.getItem(key);
    }
  } catch {
    /* ignore */
  }
  return memoryStore.get(key) ?? null;
}

function storageSet(key: string, value: string): void {
  try {
    if (typeof localStorage !== "undefined") {
      localStorage.setItem(key, value);
      return;
    }
  } catch {
    /* fall through to memory */
  }
  memoryStore.set(key, value);
}

function storageClearPrefix(): void {
  try {
    if (typeof localStorage !== "undefined") {
      const keys: string[] = [];
      for (let i = 0; i < localStorage.length; i += 1) {
        const key = localStorage.key(i);
        if (key?.startsWith(PREFIX)) keys.push(key);
      }
      for (const key of keys) localStorage.removeItem(key);
    }
  } catch {
    /* ignore */
  }
  memoryStore.clear();
}

export function cmdkHistoryKey(filePath: string): string {
  return `${PREFIX}${filePath || "untitled"}`;
}

export function loadCmdkFileEdits(filePath: string): CmdkFileEdit[] {
  try {
    const raw = storageGet(cmdkHistoryKey(filePath));
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter(
        (item): item is CmdkFileEdit =>
          !!item &&
          typeof item === "object" &&
          typeof (item as CmdkFileEdit).instruction === "string" &&
          typeof (item as CmdkFileEdit).before === "string" &&
          typeof (item as CmdkFileEdit).after === "string",
      )
      .slice(0, MAX_ENTRIES);
  } catch {
    return [];
  }
}

export function rememberCmdkFileEdit(
  filePath: string,
  entry: Omit<CmdkFileEdit, "at"> & { at?: number },
): CmdkFileEdit[] {
  const nextEntry: CmdkFileEdit = {
    at: entry.at ?? Date.now(),
    instruction: entry.instruction.trim(),
    before: entry.before,
    after: entry.after,
  };
  if (!nextEntry.instruction || nextEntry.before === nextEntry.after) {
    return loadCmdkFileEdits(filePath);
  }
  const prev = loadCmdkFileEdits(filePath).filter(
    (item) =>
      !(
        item.instruction === nextEntry.instruction &&
        item.before === nextEntry.before &&
        item.after === nextEntry.after
      ),
  );
  const next = [nextEntry, ...prev].slice(0, MAX_ENTRIES);
  storageSet(cmdkHistoryKey(filePath), JSON.stringify(next));
  return next;
}

/** Test helper. */
export function __clearCmdkFileHistoryForTests(): void {
  storageClearPrefix();
}
