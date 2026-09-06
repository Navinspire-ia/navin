import { describe, expect, it } from "vitest";

import { computeLineEdit, RecentEditsTracker } from "./recentEdits";

describe("computeLineEdit", () => {
  it("detects a single-line modification", () => {
    const diff = computeLineEdit("a\nb\nc", "a\nB\nc");
    expect(diff).toEqual({ line: 2, removed: "b", inserted: "B" });
  });

  it("detects pure insertion", () => {
    const diff = computeLineEdit("a\nc", "a\nb\nc");
    expect(diff).toEqual({ line: 2, removed: "", inserted: "b" });
  });

  it("detects pure deletion", () => {
    const diff = computeLineEdit("a\nb\nc", "a\nc");
    expect(diff).toEqual({ line: 2, removed: "b", inserted: "" });
  });

  it("returns null when identical", () => {
    expect(computeLineEdit("same", "same")).toBeNull();
  });

  it("handles multi-line replacement", () => {
    const diff = computeLineEdit("h\nold1\nold2\nf", "h\nnew1\nf");
    expect(diff).toEqual({ line: 2, removed: "old1\nold2", inserted: "new1" });
  });

  it("caps huge payloads", () => {
    const diff = computeLineEdit("a", "a" + "x".repeat(10_000));
    expect(diff!.inserted.length).toBeLessThanOrEqual(400);
  });
});

describe("RecentEditsTracker", () => {
  it("merges a typing burst on the same line into one edit", () => {
    const tracker = new RecentEditsTracker();
    tracker.record("a.ts", "const x = 1", "const xy = 1");
    tracker.record("a.ts", "const xy = 1", "const xyz = 1");
    tracker.record("a.ts", "const xyz = 1", "const xyzCount = 1");
    const edits = tracker.snapshot();
    expect(edits).toHaveLength(1);
    expect(edits[0].removed).toBe("const x = 1");
    expect(edits[0].inserted).toBe("const xyzCount = 1");
  });

  it("keeps separate entries for distant edits and other files", () => {
    const tracker = new RecentEditsTracker();
    const before = Array.from({ length: 20 }, (_, i) => `line${i}`).join("\n");
    const afterTop = before.replace("line1", "line1-edited");
    tracker.record("a.ts", before, afterTop);
    const afterBottom = afterTop.replace("line15", "line15-edited");
    tracker.record("a.ts", afterTop, afterBottom);
    tracker.record("b.ts", "one", "two");
    const edits = tracker.snapshot();
    expect(edits).toHaveLength(3);
    expect(edits[0].line).toBe(2);
    expect(edits[1].line).toBe(16);
    expect(edits[2].path).toBe("b.ts");
  });

  it("bounds the history to the most recent edits", () => {
    const tracker = new RecentEditsTracker();
    for (let i = 0; i < 20; i += 1) {
      const before = Array.from({ length: 40 }, (_, n) => `l${n}`).join("\n");
      const lineToChange = (i * 3) % 40;
      const after = before.replace(`l${lineToChange}`, `l${lineToChange}-v${i}`);
      tracker.record(`file${i % 5}.ts`, before, after);
    }
    expect(tracker.snapshot().length).toBeLessThanOrEqual(8);
  });

  it("clear() empties the history", () => {
    const tracker = new RecentEditsTracker();
    tracker.record("a.ts", "x", "y");
    tracker.clear();
    expect(tracker.snapshot()).toEqual([]);
  });
});
