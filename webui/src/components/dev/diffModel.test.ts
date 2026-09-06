import { describe, expect, it } from "vitest";

import {
  buildDiffBlocks,
  collectFindHits,
  parseUnifiedDiff,
} from "./diffModel";

const SAMPLE = `diff --git a/a.py b/a.py
index 111..222 100644
--- a/a.py
+++ b/a.py
@@ -1,5 +1,6 @@
 line1
 line2
-old
+new
 line4
 line5
`;

describe("parseUnifiedDiff", () => {
  it("numbers add/remove/context lines", () => {
    const parsed = parseUnifiedDiff(SAMPLE);
    const remove = parsed.lines.find((line) => line.kind === "remove");
    const add = parsed.lines.find((line) => line.kind === "add");
    expect(remove?.text).toBe("old");
    expect(add?.text).toBe("new");
    expect(remove?.oldNo).toBeTruthy();
    expect(add?.newNo).toBeTruthy();
  });
});

describe("buildDiffBlocks", () => {
  it("collapses unmodified runs when keepEnds is 0", () => {
    const parsed = parseUnifiedDiff(SAMPLE);
    const blocks = buildDiffBlocks(parsed.lines, { keepEnds: 0 });
    expect(blocks.some((block) => block.type === "unmodified")).toBe(true);
  });

  it("keeps context near changes with default keepEnds", () => {
    const parsed = parseUnifiedDiff(SAMPLE);
    const blocks = buildDiffBlocks(parsed.lines, { keepEnds: 3 });
    const lines = blocks.filter((block) => block.type === "line");
    expect(lines.some((block) => block.type === "line" && block.line.kind === "add")).toBe(
      true,
    );
  });
});

describe("collectFindHits", () => {
  it("finds matching changed lines", () => {
    const parsed = parseUnifiedDiff(SAMPLE);
    const blocks = buildDiffBlocks(parsed.lines, { keepEnds: 3 });
    const hits = collectFindHits(blocks, "new");
    expect(hits.length).toBeGreaterThan(0);
    expect(hits[0]?.line.text).toContain("new");
  });
});
