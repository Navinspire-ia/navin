import { describe, expect, it } from "vitest";

import { filterCommands, fuzzyScore, type DevCommand } from "./devPalette";
import { nearestSymbolIndex } from "./DevOutlinePanel";

describe("fuzzyScore", () => {
  it("ranks exact and prefix matches ahead of subsequence", () => {
    expect(fuzzyScore("Quick Open", "Quick Open")).toBe(0);
    expect(fuzzyScore("Quick Open", "quick")).toBe(1);
    expect(fuzzyScore("Quick Open", "open")).toBe(2);
    expect(fuzzyScore("Quick Open", "qo")).not.toBeNull();
    expect(fuzzyScore("Quick Open", "zzz")).toBeNull();
  });
});

describe("filterCommands", () => {
  const commands: DevCommand[] = [
    { id: "quickOpen", label: "Quick Open", hint: "Ctrl+P", run: () => {} },
    {
      id: "goToSymbol",
      label: "Go to Symbol in Workspace",
      hint: "Ctrl+T",
      keywords: "symbols",
      run: () => {},
    },
    { id: "toggleProblems", label: "Toggle Problems", run: () => {} },
  ];

  it("returns all commands when query is empty", () => {
    expect(filterCommands(commands, "").map((c) => c.id)).toEqual([
      "quickOpen",
      "goToSymbol",
      "toggleProblems",
    ]);
  });

  it("matches label and keywords", () => {
    expect(filterCommands(commands, "symbol").map((c) => c.id)).toEqual([
      "goToSymbol",
    ]);
    expect(filterCommands(commands, "problems").map((c) => c.id)).toEqual([
      "toggleProblems",
    ]);
  });
});

describe("nearestSymbolIndex", () => {
  const items = [
    { name: "a", kind: "function", path: "x.ts", line: 4 },
    { name: "b", kind: "function", path: "x.ts", line: 20 },
    { name: "c", kind: "class", path: "x.ts", line: 40 },
  ];

  it("picks the last symbol at or above the caret line", () => {
    expect(nearestSymbolIndex(items, 1)).toBe(-1);
    expect(nearestSymbolIndex(items, 4)).toBe(0);
    expect(nearestSymbolIndex(items, 25)).toBe(1);
    expect(nearestSymbolIndex(items, 100)).toBe(2);
  });
});
