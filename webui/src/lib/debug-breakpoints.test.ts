import { describe, expect, it } from "vitest";

import {
  getAllBreakpoints,
  getBreakpointLines,
  setBreakpointLines,
  toggleBreakpoint,
} from "@/lib/debug-breakpoints";

describe("debug breakpoints store", () => {
  it("toggles lines per path", () => {
    setBreakpointLines("a.py", []);
    expect(toggleBreakpoint("a.py", 3)).toEqual([3]);
    expect(toggleBreakpoint("a.py", 5)).toEqual([3, 5]);
    expect(toggleBreakpoint("a.py", 3)).toEqual([5]);
    expect(getBreakpointLines("a.py")).toEqual([5]);
    expect(getAllBreakpoints().some((row) => row.path === "a.py")).toBe(true);
  });
});
