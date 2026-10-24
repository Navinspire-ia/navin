// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { beforeEach, describe, expect, it, vi } from "vitest";

import { commitOnPointerDown, commitOnSelect, resetMenuChoiceForTests } from "./menuChoice";

type PointerType = "mouse" | "pen" | "touch";

function pointer(overrides: Partial<{ button: number; pointerType: PointerType }> = {}) {
  return {
    button: 0,
    pointerType: "mouse" as PointerType,
    preventDefault: vi.fn(),
    ...overrides,
  };
}

describe("composer menu choice", () => {
  beforeEach(() => {
    resetMenuChoiceForTests();
  });

  it("commits a mouse pointerdown once and lets the follow-up select only close the menu", () => {
    const commit = vi.fn();
    const event = pointer();
    let clock = 1_000;
    const now = () => clock;

    expect(commitOnPointerDown(event, commit, now)).toBe(true);
    expect(event.preventDefault).toHaveBeenCalledTimes(1);
    expect(commit).toHaveBeenCalledTimes(1);

    // pointerup click on the same row 80ms later (WKWebView may even land on another row)
    clock += 80;
    const other = vi.fn();
    expect(commitOnSelect(other, now)).toBe(false);
    expect(other).not.toHaveBeenCalled();
  });

  it("commits from the keyboard when no pointerdown preceded the select", () => {
    const commit = vi.fn();
    expect(commitOnSelect(commit, () => 5_000)).toBe(true);
    expect(commit).toHaveBeenCalledTimes(1);
  });

  it("commits again once the pointer gesture is clearly over", () => {
    const now = vi.fn<() => number>();
    now.mockReturnValueOnce(1_000);
    commitOnPointerDown(pointer(), vi.fn(), now);
    now.mockReturnValueOnce(1_000 + 2_000);
    const commit = vi.fn();
    expect(commitOnSelect(commit, now)).toBe(true);
    expect(commit).toHaveBeenCalledTimes(1);
  });

  it("ignores secondary buttons on pointerdown", () => {
    const commit = vi.fn();
    const event = pointer({ button: 2 });
    expect(commitOnPointerDown(event, commit)).toBe(false);
    expect(commit).not.toHaveBeenCalled();
    expect(event.preventDefault).not.toHaveBeenCalled();
  });

  it("leaves touch to the click path so a scroll gesture does not pick a row", () => {
    const commit = vi.fn();
    expect(commitOnPointerDown(pointer({ pointerType: "touch" }), commit)).toBe(false);
    expect(commit).not.toHaveBeenCalled();
    // the tap's click then commits and closes
    expect(commitOnSelect(commit)).toBe(true);
    expect(commit).toHaveBeenCalledTimes(1);
  });
});

describe("opening gesture", () => {
  it("ignores the release of the click that opened the menu, not a later pick", async () => {
    const { isOpeningGesture, noteMenuOpened, resetMenuChoiceForTests } = await import("./menuChoice");
    resetMenuChoiceForTests();
    expect(isOpeningGesture(() => 1_000)).toBe(false);
    noteMenuOpened(() => 1_000);
    expect(isOpeningGesture(() => 1_200)).toBe(true);
    expect(isOpeningGesture(() => 1_600)).toBe(false);
  });
});
