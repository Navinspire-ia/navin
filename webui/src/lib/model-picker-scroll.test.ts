// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it, vi } from "vitest";

import { bindMenuListWheel, modelPickerListMaxPx } from "./model-picker-scroll";

describe("modelPickerListMaxPx", () => {
  it("caps the list so it fits above the composer trigger", () => {
    expect(modelPickerListMaxPx(520, true)).toBe(348);
    expect(modelPickerListMaxPx(520, false)).toBe(360);
  });

  it("never shrinks below the minimum row window", () => {
    expect(modelPickerListMaxPx(80, true)).toBe(160);
  });
});

describe("bindMenuListWheel", () => {
  function fakeList(scrollHeight: number, clientHeight: number) {
    let listener: ((event: WheelEvent) => void) | undefined;
    const node = {
      clientHeight,
      scrollHeight,
      scrollTop: 0,
      addEventListener(_type: string, fn: (event: WheelEvent) => void) {
        listener = fn;
      },
      removeEventListener() {
        listener = undefined;
      },
    };
    return {
      node: node as unknown as HTMLElement,
      fire(deltaY: number) {
        const event = {
          deltaY,
          deltaMode: 0,
          preventDefault: vi.fn(),
          stopPropagation: vi.fn(),
        };
        listener?.(event as unknown as WheelEvent);
        return event;
      },
    };
  }

  it("scrolls an overflowing list and eats the event", () => {
    const list = fakeList(400, 100);
    const stop = bindMenuListWheel(list.node);
    const event = list.fire(80);
    expect(event.preventDefault).toHaveBeenCalledTimes(1);
    expect(list.node.scrollTop).toBe(80);
    stop();
  });

  it("leaves the event alone when the list is not a scrollport", () => {
    const list = fakeList(400, 400);
    const stop = bindMenuListWheel(list.node);
    const event = list.fire(80);
    expect(event.preventDefault).not.toHaveBeenCalled();
    expect(list.node.scrollTop).toBe(0);
    stop();
  });
});
