import { describe, expect, it } from "vitest";

import { unifiedToSideBySide } from "./diffSideBySide";

const SAMPLE = `diff --git a/a.py b/a.py
index 111..222 100644
--- a/a.py
+++ b/a.py
@@ -1,3 +1,4 @@
 def hello():
-    return 1
+    return 2
+    # note
     print("ok")
`;

describe("unifiedToSideBySide", () => {
  it("pairs changed lines and keeps trailing additions", () => {
    const rows = unifiedToSideBySide(SAMPLE).filter((r) => r.kind !== "meta");
    const change = rows.find((r) => r.kind === "change");
    expect(change?.left).toContain("return 1");
    expect(change?.right).toContain("return 2");
    expect(rows.some((r) => r.kind === "add" && r.right?.includes("# note"))).toBe(true);
    expect(
      rows.some((r) => r.kind === "context" && r.left?.includes('print("ok")')),
    ).toBe(true);
  });

  it("returns empty for blank input", () => {
    expect(unifiedToSideBySide("")).toEqual([]);
    expect(unifiedToSideBySide("   \n")).toEqual([]);
  });

  it("tolerates a trailing empty line from git", () => {
    const rows = unifiedToSideBySide(`${SAMPLE}\n`);
    expect(rows.some((r) => r.kind === "change")).toBe(true);
  });
});
