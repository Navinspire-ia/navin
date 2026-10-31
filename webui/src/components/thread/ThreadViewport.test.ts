// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import {
  NEAR_BOTTOM_PX,
  PROMPT_JUMP_ECHO_MS,
  USER_SCROLL_INTENT_MS,
  classifyScrollEvent,
  hasRecentUserScrollIntent,
  isNearBottom,
  isUpwardScrollWithSameHeight,
  isScrollIntentKey,
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

describe("user scroll intent", () => {
  it("reads a scroll without a gesture as layout, never as the reader leaving", () => {
    // A fold spring, the virtual window padding or a hydrate moved scrollTop
    // 300px above the tail. Nobody touched the wheel: keep following.
    expect(classifyScrollEvent(null, 400, 1_010, false, false)).toBe("layout");
    expect(classifyScrollEvent(null, 400, 1_010, false, true)).toBe("user");
    // Our own marks still win over intent.
    expect(classifyScrollEvent({ top: 1000, at: 1_000, bottom: true }, 1000, 1_010, false, false))
      .toBe("catch-up");
  });

  it("trusts a wheel, touch or key gesture for a short window only", () => {
    expect(hasRecentUserScrollIntent(0, false, 5_000)).toBe(false);
    expect(hasRecentUserScrollIntent(5_000, false, 5_000 + USER_SCROLL_INTENT_MS)).toBe(true);
    expect(hasRecentUserScrollIntent(5_000, false, 5_001 + USER_SCROLL_INTENT_MS)).toBe(false);
    // Dragging the scrollbar holds intent for as long as the pointer is down.
    expect(hasRecentUserScrollIntent(0, true, 99_000)).toBe(true);
  });

  it("counts scroll keys outside the composer, not typing", () => {
    expect(isScrollIntentKey("PageUp", false)).toBe(true);
    expect(isScrollIntentKey("ArrowUp", false)).toBe(true);
    expect(isScrollIntentKey("Home", false)).toBe(true);
    expect(isScrollIntentKey("ArrowUp", true)).toBe(false);
    expect(isScrollIntentKey("a", false)).toBe(false);
  });
});

describe("prompt jumps while following", () => {
  it("reads the frames of a jump from Find or the navigator as the jump itself", () => {
    const start = 10_000;
    const until = start + PROMPT_JUMP_ECHO_MS;
    // First smooth frames are still near the bottom and no gesture vouches for
    // them (the click came from outside the viewport): never "layout".
    expect(classifyScrollEvent(null, 9_950, start + 16, true, false, until)).toBe("jump");
    expect(classifyScrollEvent(null, 4_000, start + 300, false, false, until)).toBe("jump");
    // A bottom mark left from following does not turn a jump frame into a catch-up.
    expect(classifyScrollEvent({ top: 9_990, at: start, bottom: true }, 9_990, start + 10, false, false, until))
      .toBe("jump");
  });

  it("stops claiming frames once the deadline passed or was cleared", () => {
    const until = 10_000 + PROMPT_JUMP_ECHO_MS;
    expect(classifyScrollEvent(null, 4_000, until, false, false, until)).toBe("layout");
    expect(classifyScrollEvent(null, 4_000, until + 5, false, true, until)).toBe("user");
    expect(classifyScrollEvent(null, 4_000, 10_100, false, false, 0)).toBe("layout");
  });
});

describe("upward move with an unchanged height", () => {
  it("is the reader (selection drag, autoscroll, browser find)", () => {
    expect(isUpwardScrollWithSameHeight({ top: 5_000, height: 9_000 }, 4_600, 9_000)).toBe(true);
  });

  it("is layout when the height changed or the move went down", () => {
    // A fold collapsing clamps scrollTop up and shrinks the content.
    expect(isUpwardScrollWithSameHeight({ top: 5_000, height: 9_000 }, 4_600, 8_600)).toBe(false);
    expect(isUpwardScrollWithSameHeight({ top: 5_000, height: 9_000 }, 5_400, 9_000)).toBe(false);
    // Subpixel jitter is not a move.
    expect(isUpwardScrollWithSameHeight({ top: 5_000, height: 9_000 }, 4_999.5, 9_000)).toBe(false);
    expect(isUpwardScrollWithSameHeight(null, 0, 9_000)).toBe(false);
  });

  it("turns the move into user intent for the classifier", () => {
    const intent = isUpwardScrollWithSameHeight({ top: 5_000, height: 9_000 }, 4_600, 9_000);
    expect(classifyScrollEvent(null, 4_600, 20_000, false, intent)).toBe("user");
  });
});

describe("composer dock", () => {
  it("fades the transcript above the prompt instead of a hard cut behind it", async () => {
    const { readFileSync } = await import("node:fs");
    const source = readFileSync(new URL("./ThreadViewport.tsx", import.meta.url), "utf8");
    const dock = source.slice(source.indexOf('data-testid="thread-composer-dock"'));
    expect(dock).toMatch(/thread-composer-dock-fade[\s\S]{0,200}bottom-full[\s\S]{0,80}from-background/);
  });

  it("follows the tail when the composer grows, never jumps back to the sent prompt", async () => {
    // Typing a line or queueing a message resizes the dock. Re-pinning the
    // prompt there fought the tail-follow while the agent worked: the chat
    // jumped up to the prompt at each key and back down at each frame.
    const { readFileSync } = await import("node:fs");
    const source = readFileSync(new URL("./ThreadViewport.tsx", import.meta.url), "utf8");
    const effect = source.match(
      /useIsomorphicLayoutEffect\(\(\) => \{[^}]*\}, \[composerDockHeight[^\]]*\]\);/,
    );
    expect(effect?.[0]).toBeTruthy();
    expect(effect?.[0]).toContain("scrollToBottom(false, 2)");
    expect(effect?.[0]).not.toContain("scrollToPromptTopNow");
  });
});
