// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import type { RenderableFileDiffLine } from "@/lib/file-diff";

export type SideBySideRow = {
  left: RenderableFileDiffLine | null;
  right: RenderableFileDiffLine | null;
};

/** Pair deletions with the additions that follow them; context shows on both sides. */
export function pairSideBySide(lines: RenderableFileDiffLine[]): SideBySideRow[] {
  const rows: SideBySideRow[] = [];
  let dels: RenderableFileDiffLine[] = [];
  let adds: RenderableFileDiffLine[] = [];
  const flush = () => {
    const n = Math.max(dels.length, adds.length);
    for (let i = 0; i < n; i += 1) rows.push({ left: dels[i] ?? null, right: adds[i] ?? null });
    dels = [];
    adds = [];
  };
  for (const line of lines) {
    if (line.kind === "delete") {
      if (adds.length) flush();
      dels.push(line);
    } else if (line.kind === "add") {
      adds.push(line);
    } else {
      flush();
      rows.push({ left: line, right: line });
    }
  }
  flush();
  return rows;
}

/** Rebuild the old and new text of each hunk (for a full diff editor); hunks are separated by a blank line. */
export function hunksToTexts(hunks: Array<{ lines: RenderableFileDiffLine[] }>): { original: string; modified: string } {
  const original: string[] = [];
  const modified: string[] = [];
  for (const hunk of hunks) {
    original.push(hunk.lines.filter((l) => l.kind !== "add").map((l) => l.content).join("\n"));
    modified.push(hunk.lines.filter((l) => l.kind !== "delete").map((l) => l.content).join("\n"));
  }
  return { original: original.join("\n\n"), modified: modified.join("\n\n") };
}
