// @vitest-environment jsdom
// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setVisibleInterval } from "./visible-interval";

let visibility: DocumentVisibilityState = "visible";

function setVisibility(next: DocumentVisibilityState) {
  visibility = next;
  document.dispatchEvent(new Event("visibilitychange"));
}

beforeEach(() => {
  vi.useFakeTimers();
  visibility = "visible";
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    get: () => visibility,
  });
});
afterEach(() => {
  vi.useRealTimers();
});

describe("setVisibleInterval", () => {
  it("ticks while visible, stays quiet while hidden, catches up once on return", () => {
    const tick = vi.fn();
    const stop = setVisibleInterval(tick, 1_000);
    vi.advanceTimersByTime(2_000);
    expect(tick).toHaveBeenCalledTimes(2);

    setVisibility("hidden");
    vi.advanceTimersByTime(5_000);
    expect(tick).toHaveBeenCalledTimes(2);

    setVisibility("visible");
    expect(tick).toHaveBeenCalledTimes(3);
    setVisibility("visible");
    expect(tick).toHaveBeenCalledTimes(3);
    stop();
  });

  it("does not refresh on return when no tick was skipped", () => {
    const tick = vi.fn();
    const stop = setVisibleInterval(tick, 10_000);
    setVisibility("hidden");
    setVisibility("visible");
    expect(tick).not.toHaveBeenCalled();
    stop();
  });

  it("stops ticking and listening after cleanup", () => {
    const tick = vi.fn();
    const stop = setVisibleInterval(tick, 1_000);
    stop();
    setVisibility("hidden");
    vi.advanceTimersByTime(3_000);
    setVisibility("visible");
    expect(tick).not.toHaveBeenCalled();
  });
});
