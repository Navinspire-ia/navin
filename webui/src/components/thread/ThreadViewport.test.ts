// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import {
  NEAR_BOTTOM_PX,
  classifyScrollEvent,
  isNearBottom,
  isProgrammaticScroll,
  softKeyboardForcesScrollToBottom,
  visibleAnchorMessageCount,
} from "./ThreadViewport";

describe("visibleAnchorMessageCount", () => {
  it("leaves a block unanchored while a run is live", () => {
    // No anchor is how the plan card trails the last message mid-run, so Stop
    // and the live activity stay in view.
    expect(visibleAnchorMessageCount(null, 0)).toBeNull();
    expect(visibleAnchorMessageCount(undefined, 12)).toBeNull();
  });

  it("keeps the anchor where it is when nothing is hidden", () => {
    expect(visibleAnchorMessageCount(7, 0)).toBe(7);
  });

  it("shifts it by the messages the window does not render", () => {
    expect(visibleAnchorMessageCount(30, 12)).toBe(18);
  });

  it("pins an anchor scrolled past the window to the first visible turn", () => {
    // Regression guard: falling back to "no anchor" would drop the card back
    // onto the bottom of the thread, which is the behaviour being fixed.
    expect(visibleAnchorMessageCount(3, 40)).toBe(1);
    expect(visibleAnchorMessageCount(40, 40)).toBe(1);
  });
});

describe("softKeyboardForcesScrollToBottom", () => {
  it("only overrides the reading guard when a keyboard took space", () => {
    expect(softKeyboardForcesScrollToBottom(320, true)).toBe(true);
  });

  it("does not override it on desktop, where there is no inset", () => {
    // Regression guard: this used to be keyed on composer focus, which is the
    // resting state on desktop, so scrolling up was undone a few frames later
    // by any viewport, resize or focus event.
    expect(softKeyboardForcesScrollToBottom(0, true)).toBe(false);
  });

  it("stays out of the way on an empty thread", () => {
    expect(softKeyboardForcesScrollToBottom(320, false)).toBe(false);
    expect(softKeyboardForcesScrollToBottom(0, false)).toBe(false);
  });
});

describe("isNearBottom", () => {
  it("treats the very bottom and small gaps as following the thread", () => {
    expect(isNearBottom(1000, 800, 200)).toBe(true);
    expect(isNearBottom(1000, 760, 200)).toBe(true);
  });

  it("treats a real scroll up as reading history", () => {
    expect(isNearBottom(1000, 752, 200)).toBe(false);
    expect(isNearBottom(1000, 0, 200)).toBe(false);
  });

  it("handles a thread shorter than its viewport", () => {
    expect(isNearBottom(200, 0, 400)).toBe(true);
  });
});

describe("isProgrammaticScroll", () => {
  it("recognises the echo of a jump it just made", () => {
    expect(isProgrammaticScroll({ top: 1200, at: 1_000 }, 1200, 1_010)).toBe(true);
    expect(isProgrammaticScroll({ top: 1200, at: 1_000 }, 1201, 1_010)).toBe(true);
  });

  it("does not claim a scroll to a different position", () => {
    expect(isProgrammaticScroll({ top: 1200, at: 1_000 }, 900, 1_010)).toBe(false);
    expect(isProgrammaticScroll(null, 1200, 1_010)).toBe(false);
  });

  it("lets the mark expire", () => {
    // A jump that never moved scrollTop produces no scroll event, so the mark
    // used to survive until the reader's next scroll and swallow it.
    expect(isProgrammaticScroll({ top: 1200, at: 1_000 }, 1200, 9_000)).toBe(false);
  });
});

describe("classifyScrollEvent (follow-the-tail contract)", () => {
  const bottomMark = { top: 1000, at: 1_000, bottom: true };
  const jumpMark = { top: 300, at: 1_000 };

  it("keeps following when the thread grows under our own scroll to the tail", () => {
    // Issue #6. We scrolled to the bottom (scrollTop 1000); before the scroll
    // event fired, a tool result of 300px landed, so the event reports the
    // viewport 300px above the new bottom. scrollTop never moved: the reader
    // did not leave, and the thread has to catch up rather than stop.
    expect(classifyScrollEvent(bottomMark, 1000, 1_010, false)).toBe("catch-up");
  });

  it("treats the echo of a scroll to the tail as nothing new", () => {
    expect(classifyScrollEvent(bottomMark, 1000, 1_010, true)).toBe("echo");
  });

  it("never turns a prompt jump into a catch-up", () => {
    // Jumping to a prompt far above the tail is a reading gesture; growth
    // below it must not drag the reader back down.
    expect(classifyScrollEvent(jumpMark, 300, 1_010, false)).toBe("echo");
  });

  it("recognises the reader scrolling away, mark or no mark", () => {
    expect(classifyScrollEvent(bottomMark, 400, 1_010, false)).toBe("user");
    expect(classifyScrollEvent(null, 400, 1_010, false)).toBe("user");
    // An expired mark says nothing about a scroll that happens much later.
    expect(classifyScrollEvent(bottomMark, 1000, 9_000, false)).toBe("user");
  });

  it("recognises the reader coming back to the tail", () => {
    expect(classifyScrollEvent(null, 1000, 1_010, true)).toBe("user");
  });

  it("keeps the near-bottom band where a reader can still be said to follow", () => {
    // The listener flips the reading guard past this distance; too narrow and
    // one delta ends the follow, too wide and a reader parked above the tail
    // gets yanked. The follow effect depends on this band staying sane.
    expect(NEAR_BOTTOM_PX).toBeGreaterThanOrEqual(24);
    expect(NEAR_BOTTOM_PX).toBeLessThanOrEqual(96);
    expect(isNearBottom(2000, 2000 - 600 - (NEAR_BOTTOM_PX - 1), 600)).toBe(true);
    expect(isNearBottom(2000, 2000 - 600 - NEAR_BOTTOM_PX, 600)).toBe(false);
  });
});

describe("streaming follow simulation", () => {
  /**
   * The scroll listener as a pure state machine over the reading guard, so the
   * turn-long sequence that used to kill the follow can be replayed here.
   */
  function replay(
    events: Array<{ mark: Parameters<typeof classifyScrollEvent>[0]; scrollTop: number; near: boolean }>,
  ): { reading: boolean; catchUps: number } {
    let reading = false;
    let catchUps = 0;
    for (const event of events) {
      const meaning = classifyScrollEvent(event.mark, event.scrollTop, 1_010, event.near);
      if (meaning === "catch-up") {
        catchUps += 1;
        continue;
      }
      if (meaning === "echo") {
        if (event.near) reading = false;
        continue;
      }
      reading = !event.near;
    }
    return { reading, catchUps };
  }

  it("survives a long tool result landing between our scroll and its event", () => {
    const outcome = replay([
      { mark: { top: 800, at: 1_000, bottom: true }, scrollTop: 800, near: true },
      // 400px tool result arrives before the next scroll event is processed.
      { mark: { top: 900, at: 1_000, bottom: true }, scrollTop: 900, near: false },
      { mark: { top: 1300, at: 1_000, bottom: true }, scrollTop: 1300, near: true },
    ]);
    expect(outcome.reading).toBe(false);
    expect(outcome.catchUps).toBe(1);
  });

  it("still stops the moment the reader scrolls up, and resumes when they return", () => {
    const away = replay([
      { mark: { top: 800, at: 1_000, bottom: true }, scrollTop: 800, near: true },
      { mark: null, scrollTop: 200, near: false },
    ]);
    expect(away.reading).toBe(true);
    const back = replay([
      { mark: null, scrollTop: 200, near: false },
      { mark: null, scrollTop: 1400, near: true },
    ]);
    expect(back.reading).toBe(false);
  });
});
