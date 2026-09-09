// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Line-level diff between a file's pre-agent baseline and its current text,
 * shaped for an inline review in the editor: green lines stay in the document,
 * red lines are drawn as blocks where they used to be, and both keep their
 * line number from the version they belong to.
 */

import { diffLines } from "diff";

export type ReviewBlock =
  | {
      kind: "added";
      /** 1-based first line in the current document. */
      line: number;
      count: number;
    }
  | {
      kind: "removed";
      /**
       * 1-based line of the current document the removed text used to sit
       * before. `docLines + 1` when it was cut from the very end.
       */
      anchor: number;
      /** 1-based line number of the first removed line in the baseline. */
      oldStart: number;
      lines: string[];
    };

export type ReviewGutterRow = {
  kind: "added" | "removed";
  /** Line number in the version the row belongs to (new for added, old for removed). */
  number: number;
};

/** Baseline vs current, as ordered blocks. Unchanged lines are left out. */
export function computeReviewBlocks(baseline: string, current: string): ReviewBlock[] {
  const blocks: ReviewBlock[] = [];
  let newLine = 1;
  let oldLine = 1;
  for (const part of diffLines(baseline, current)) {
    const count = part.count ?? 0;
    if (part.added) {
      if (count > 0) blocks.push({ kind: "added", line: newLine, count });
      newLine += count;
    } else if (part.removed) {
      const lines = part.value.split("\n");
      if (lines.length && lines[lines.length - 1] === "") lines.pop();
      if (lines.length) {
        blocks.push({ kind: "removed", anchor: newLine, oldStart: oldLine, lines });
      }
      oldLine += lines.length;
    } else {
      newLine += count;
      oldLine += count;
    }
  }
  return blocks;
}

/** 1-based current-document lines that are new since the baseline. */
export function addedLineNumbers(blocks: readonly ReviewBlock[]): number[] {
  const lines: number[] = [];
  for (const block of blocks) {
    if (block.kind !== "added") continue;
    for (let i = 0; i < block.count; i += 1) lines.push(block.line + i);
  }
  return lines;
}

/**
 * Gutter rows for a removed block: one old line number per drawn line, then a
 * blank row for the "+N more" footer when the block is longer than `maxLines`.
 */
export function removedGutterRows(
  oldStart: number,
  lineCount: number,
  maxLines: number,
): ReviewGutterRow[] {
  const shown = Math.min(lineCount, maxLines);
  const rows: ReviewGutterRow[] = [];
  for (let i = 0; i < shown; i += 1) {
    rows.push({ kind: "removed", number: oldStart + i });
  }
  return rows;
}

/** Lines a removed block occupies on screen, footer included. */
export function removedBlockRowCount(lineCount: number, maxLines: number): number {
  return lineCount > maxLines ? maxLines + 1 : lineCount;
}
