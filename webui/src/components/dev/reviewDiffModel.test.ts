// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import {
  addedLineNumbers,
  computeReviewBlocks,
  removedBlockRowCount,
  removedGutterRows,
  type ReviewBlock,
} from "./reviewDiffModel";

describe("computeReviewBlocks", () => {
  it("keeps the old number on removed lines and the new number on added ones", () => {
    const baseline = ["a", "b", "c", "d"].join("\n") + "\n";
    const current = ["a", "B1", "B2", "c"].join("\n") + "\n";
    const blocks = computeReviewBlocks(baseline, current);
    expect(blocks).toEqual<ReviewBlock[]>([
      { kind: "removed", anchor: 2, oldStart: 2, lines: ["b"] },
      { kind: "added", line: 2, count: 2 },
      { kind: "removed", anchor: 5, oldStart: 4, lines: ["d"] },
    ]);
    expect(addedLineNumbers(blocks)).toEqual([2, 3]);
  });

  it("numbers removed lines after an insertion from the baseline, not the document", () => {
    const baseline = ["x", "y", "z"].join("\n") + "\n";
    const current = ["n1", "n2", "x", "y"].join("\n") + "\n";
    const blocks = computeReviewBlocks(baseline, current);
    expect(blocks).toEqual<ReviewBlock[]>([
      { kind: "added", line: 1, count: 2 },
      { kind: "removed", anchor: 5, oldStart: 3, lines: ["z"] },
    ]);
  });

  it("treats a lost trailing newline as a changed last line, like git and the server hunks", () => {
    const blocks = computeReviewBlocks("x\ny\n", "x\ny");
    expect(blocks).toEqual<ReviewBlock[]>([
      { kind: "removed", anchor: 2, oldStart: 2, lines: ["y"] },
      { kind: "added", line: 2, count: 1 },
    ]);
  });

  it("anchors a removal at the top to line 1 and a whole-file rewrite to both", () => {
    expect(computeReviewBlocks("gone\nkept\n", "kept\n")).toEqual<ReviewBlock[]>([
      { kind: "removed", anchor: 1, oldStart: 1, lines: ["gone"] },
    ]);
    expect(computeReviewBlocks("old\n", "new\n")).toEqual<ReviewBlock[]>([
      { kind: "removed", anchor: 1, oldStart: 1, lines: ["old"] },
      { kind: "added", line: 1, count: 1 },
    ]);
  });

  it("returns nothing for identical text and keeps blank removed lines", () => {
    expect(computeReviewBlocks("same\n", "same\n")).toEqual([]);
    const blocks = computeReviewBlocks("a\n\n\nb\n", "a\nb\n");
    expect(blocks).toEqual<ReviewBlock[]>([
      { kind: "removed", anchor: 2, oldStart: 2, lines: ["", ""] },
    ]);
  });
});

describe("removed block gutter", () => {
  it("emits one old number per drawn line", () => {
    expect(removedGutterRows(40, 3, 200)).toEqual([
      { kind: "removed", number: 40 },
      { kind: "removed", number: 41 },
      { kind: "removed", number: 42 },
    ]);
    expect(removedBlockRowCount(3, 200)).toBe(3);
  });

  it("caps the rows and reserves one for the footer on long blocks", () => {
    expect(removedGutterRows(40, 3, 2)).toEqual([
      { kind: "removed", number: 40 },
      { kind: "removed", number: 41 },
    ]);
    expect(removedBlockRowCount(3, 2)).toBe(3);
    expect(removedBlockRowCount(500, 200)).toBe(201);
  });
});
