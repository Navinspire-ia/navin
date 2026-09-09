// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Open tabs in the Code workbench, shared with the rest of the app.
 *
 * Two readers, two needs. Agent context packing wants the paths. The artifact
 * canvas wants to know whether something it is about to render is already on
 * screen in the editor, which it cannot answer from a path alone: an artifact
 * presented as inline content carries no `source_path`. So each entry also
 * carries a fingerprint of the tab's content, and matching bodies count as the
 * same result no matter how they were addressed.
 */

export interface DevOpenFile {
  path: string;
  /** Fingerprint of the tab's loaded body; empty while it is still loading. */
  hash: string;
}

let openFiles: DevOpenFile[] = [];
const listeners = new Set<() => void>();

function sameEntries(a: DevOpenFile[], b: DevOpenFile[]): boolean {
  if (a.length !== b.length) return false;
  return a.every((entry, i) => entry.path === b[i]?.path && entry.hash === b[i]?.hash);
}

/** Called by DevWorkbench whenever the open tab set or its contents change. */
export function setDevOpenFiles(files: DevOpenFile[]): void {
  const next: DevOpenFile[] = [];
  const seen = new Set<string>();
  for (const file of files) {
    const cleaned = (file?.path || "").trim();
    if (!cleaned || seen.has(cleaned)) continue;
    seen.add(cleaned);
    next.push({ path: cleaned, hash: file.hash || "" });
    if (next.length >= 12) break;
  }
  // Bailing out on an unchanged set keeps useSyncExternalStore from looping:
  // it compares snapshots by identity.
  if (sameEntries(openFiles, next)) return;
  openFiles = next;
  for (const listener of listeners) listener();
}

/** Read by ThreadShell when sending a Code-module message. */
export function getDevOpenFiles(): string[] {
  return openFiles.map((file) => file.path);
}

/** Stable snapshot for useSyncExternalStore. */
export function getDevOpenFileEntries(): DevOpenFile[] {
  return openFiles;
}

export function subscribeDevOpenFiles(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
