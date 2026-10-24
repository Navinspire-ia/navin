import { describe, expect, it } from "vitest";

import { pairSideBySide } from "@/components/thread/activity/sideBySideDiff";

describe("pairSideBySide", () => {
  it("pairs a replaced line and keeps context on both sides", () => {
    const rows = pairSideBySide([
      { kind: "context", content: "a", old_lineno: 1, new_lineno: 1 },
      { kind: "delete", content: "b", old_lineno: 2 },
      { kind: "add", content: "B", new_lineno: 2 },
      { kind: "add", content: "C", new_lineno: 3 },
    ]);
    expect(rows).toHaveLength(3);
    expect(rows[0].left?.content).toBe("a");
    expect(rows[0].right?.content).toBe("a");
    expect([rows[1].left?.content, rows[1].right?.content]).toEqual(["b", "B"]);
    expect([rows[2].left, rows[2].right?.content]).toEqual([null, "C"]);
  });
  it("returns nothing for an empty hunk", () => {
    expect(pairSideBySide([])).toEqual([]);
  });
});

import { previewMaxFor } from "@/lib/activity-verbosity";
import { redactSecrets } from "@/lib/redact-secrets";

describe("journal output safety", () => {
  it("masks a token printed by a command", () => {
    expect(redactSecrets("GITHUB_TOKEN=ghp_abcdefghijklmnopqrstuv")).not.toContain("ghp_");
  });
  it("debug mode never folds rows", () => {
    expect(previewMaxFor("debug", 4)).toBeGreaterThan(10_000);
  });
});
