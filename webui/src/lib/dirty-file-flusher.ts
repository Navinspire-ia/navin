// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/** Lets Evolve flush unsaved Code editor buffers before a dirty proof.

 * Pending agent edits are already on disk. User edits in the file space are
 * not, until Save. A proof that skips this step benches HEAD-plus-agent
 * writes and silently drops the buffer the user just changed.
 */

type DirtyFileFlusher = () => Promise<void>;

let flusher: DirtyFileFlusher | null = null;

export function registerDirtyFileFlusher(next: DirtyFileFlusher | null): void {
  flusher = next;
}

export async function flushDirtyFiles(): Promise<void> {
  if (!flusher) return;
  await flusher();
}
