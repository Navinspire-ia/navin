import { describe, expect, it } from "vitest";

import {
  computeVirtualWindow,
  prefixHeights,
  VIRTUALIZE_AFTER,
} from "./virtualWindow";

describe("prefixHeights", () => {
  it("uses the estimate until a row has been measured", () => {
    expect(prefixHeights(3, [], 10)).toEqual([0, 10, 20, 30]);
    expect(prefixHeights(3, [12, undefined, 8], 10)).toEqual([0, 12, 22, 30]);
  });
});

describe("computeVirtualWindow", () => {
  it("returns an empty window for an empty list", () => {
    expect(
      computeVirtualWindow({
        count: 0,
        scrollTop: 0,
        viewportHeight: 400,
        heights: [],
      }),
    ).toEqual({ start: 0, end: 0, paddingTop: 0, paddingBottom: 0 });
  });

  it("keeps only the viewport plus overscan in a long thread", () => {
    const heights = Array.from({ length: 80 }, () => 100);
    const window = computeVirtualWindow({
      count: 80,
      scrollTop: 2000,
      viewportHeight: 400,
      heights,
      estimatedHeight: 100,
      overscan: 2,
    });
    // Viewport 2000-2400 covers items 20-23 (item 24 starts on the edge);
    // overscan of 2 widens that to 18-26.
    expect(window.start).toBe(18);
    expect(window.end).toBe(26);
    expect(window.paddingTop).toBe(1800);
    expect(window.paddingBottom).toBe(80 * 100 - 26 * 100);
  });

  it("pins a jump target even when it sits above the viewport", () => {
    const heights = Array.from({ length: 40 }, () => 50);
    const window = computeVirtualWindow({
      count: 40,
      scrollTop: 1500,
      viewportHeight: 200,
      heights,
      estimatedHeight: 50,
      overscan: 0,
      pinIndexes: [2],
    });
    expect(window.start).toBe(2);
    expect(window.end).toBeGreaterThan(2);
  });

  it("clamps pin indexes onto the list", () => {
    const window = computeVirtualWindow({
      count: 5,
      scrollTop: 0,
      viewportHeight: 100,
      heights: [20, 20, 20, 20, 20],
      overscan: 0,
      pinIndexes: [-4, 99],
    });
    expect(window.start).toBe(0);
    expect(window.end).toBe(5);
  });
});

describe("VIRTUALIZE_AFTER", () => {
  it("stays high enough that short chats do not pay the spacer cost", () => {
    expect(VIRTUALIZE_AFTER).toBeGreaterThanOrEqual(16);
  });
});
