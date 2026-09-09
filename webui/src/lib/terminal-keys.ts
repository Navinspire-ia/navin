// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/** Ctrl+C / Cmd+C chords for the integrated terminal. */

export function isInterruptChord(event: {
  ctrlKey: boolean;
  metaKey: boolean;
  altKey: boolean;
  shiftKey: boolean;
  key: string;
}): boolean {
  if (event.altKey || event.shiftKey) return false;
  if (event.key.toLowerCase() !== "c") return false;
  return event.ctrlKey || event.metaKey;
}

/**
 * VS Code / xterm: a selection means copy. No selection means SIGINT (ETX).
 * Cmd+C on macOS is copy at the OS layer, so with no selection we still send
 * the interrupt instead of a no-op copy.
 */
export function interruptSendsSigint(hasSelection: boolean): boolean {
  return !hasSelection;
}
