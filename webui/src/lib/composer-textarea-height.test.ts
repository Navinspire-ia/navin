// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  COMPOSER_MIN_MEASURABLE_WIDTH_PX,
  COMPOSER_TEXTAREA_MAX_PX,
  fitTextareaHeight,
  observeTextareaWidth,
} from "./composer-textarea-height";

function fakeTextarea(clientWidth: number, scrollHeight: number) {
  return { clientWidth, scrollHeight, style: { height: "137px" } };
}

describe("fitTextareaHeight", () => {
  it("follows scrollHeight for a laid-out textarea", () => {
    const el = fakeTextarea(640, 72);
    expect(fitTextareaHeight(el)).toBe(72);
    expect(el.style.height).toBe("72px");
  });

  it("caps the height at the composer maximum", () => {
    const el = fakeTextarea(640, 900);
    expect(fitTextareaHeight(el)).toBe(COMPOSER_TEXTAREA_MAX_PX);
    expect(el.style.height).toBe(`${COMPOSER_TEXTAREA_MAX_PX}px`);
  });

  it("does not measure a textarea without layout width (collapsed column)", () => {
    // Inside a collapsed column the placeholder wraps into a tall box; that
    // measurement must never be written as the height.
    const el = fakeTextarea(0, 240);
    expect(fitTextareaHeight(el)).toBeNull();
    expect(el.style.height).toBe("137px");
  });

  it("treats a padding-only width (width: 0 box) as not laid out", () => {
    // clientWidth includes horizontal padding: a `width: 0` textarea with
    // 14 px padding on each side still reports 28 px.
    const el = fakeTextarea(COMPOSER_MIN_MEASURABLE_WIDTH_PX - 1, 240);
    expect(fitTextareaHeight(el)).toBeNull();
    expect(el.style.height).toBe("137px");
    const laidOut = fakeTextarea(COMPOSER_MIN_MEASURABLE_WIDTH_PX, 70);
    expect(fitTextareaHeight(laidOut)).toBe(70);
  });

  it("tolerates a missing element", () => {
    expect(fitTextareaHeight(null)).toBeNull();
    expect(fitTextareaHeight(undefined)).toBeNull();
  });
});

describe("observeTextareaWidth", () => {
  const original = globalThis.ResizeObserver;

  afterEach(() => {
    globalThis.ResizeObserver = original;
  });

  it("re-fits only when the width actually changes", () => {
    let trigger: (() => void) | null = null;
    const disconnect = vi.fn();
    class FakeResizeObserver {
      constructor(cb: () => void) {
        trigger = cb;
      }
      observe() {}
      disconnect = disconnect;
    }
    globalThis.ResizeObserver = FakeResizeObserver as unknown as typeof ResizeObserver;

    const el = { ...fakeTextarea(4, 240) } as unknown as HTMLTextAreaElement;
    const refit = vi.fn();
    const dispose = observeTextareaWidth(el, refit);
    expect(trigger).not.toBeNull();

    // Height-only change: same width, no refit.
    trigger!();
    expect(refit).not.toHaveBeenCalled();

    // Column opened: width settled, refit once.
    Object.defineProperty(el, "clientWidth", { value: 720, configurable: true });
    trigger!();
    expect(refit).toHaveBeenCalledTimes(1);

    dispose();
    expect(disconnect).toHaveBeenCalledTimes(1);
  });

  it("is a no-op without ResizeObserver or element", () => {
    globalThis.ResizeObserver = undefined as unknown as typeof ResizeObserver;
    expect(() => observeTextareaWidth(null, () => {})()).not.toThrow();
    const el = fakeTextarea(100, 20) as unknown as HTMLTextAreaElement;
    expect(() => observeTextareaWidth(el, () => {})()).not.toThrow();
  });
});
