import { describe, expect, it } from "vitest";

import { interruptSendsSigint, isInterruptChord } from "./terminal-keys";

describe("isInterruptChord", () => {
  it("matches Ctrl+C and Cmd+C", () => {
    expect(
      isInterruptChord({
        ctrlKey: true,
        metaKey: false,
        altKey: false,
        shiftKey: false,
        key: "c",
      }),
    ).toBe(true);
    expect(
      isInterruptChord({
        ctrlKey: false,
        metaKey: true,
        altKey: false,
        shiftKey: false,
        key: "C",
      }),
    ).toBe(true);
  });

  it("leaves Ctrl+Shift+C (copy on Linux terminals) and Alt+C alone", () => {
    expect(
      isInterruptChord({
        ctrlKey: true,
        metaKey: false,
        altKey: false,
        shiftKey: true,
        key: "c",
      }),
    ).toBe(false);
    expect(
      isInterruptChord({
        ctrlKey: false,
        metaKey: false,
        altKey: true,
        shiftKey: false,
        key: "c",
      }),
    ).toBe(false);
  });
});

describe("interruptSendsSigint", () => {
  it("sends SIGINT when nothing is selected, and copies when something is", () => {
    expect(interruptSendsSigint(false)).toBe(true);
    expect(interruptSendsSigint(true)).toBe(false);
  });
});
