import { describe, expect, it } from "vitest";

/** Ranking contract: files preferred over directories when names tie. */
function rankKind(kind: string): number {
  if (kind === "file") return 0;
  if (kind === "symbol") return 1;
  return 2;
}

describe("DevQuickOpen ranking helpers", () => {
  it("orders files before symbols before directories", () => {
    const kinds = ["directory", "symbol", "file"];
    expect([...kinds].sort((a, b) => rankKind(a) - rankKind(b))).toEqual([
      "file",
      "symbol",
      "directory",
    ]);
  });
});
