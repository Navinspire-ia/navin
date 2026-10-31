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

  it("keeps the parent height while measuring so WebKit never shows the collapsed box", () => {
    const seen: string[] = [];
    const parent = { offsetHeight: 96, style: { minHeight: "" } };
    const el = {
      clientWidth: 640,
      parentElement: parent,
      style: { height: "72px" },
      get scrollHeight() {
        seen.push(parent.style.minHeight);
        return 72;
      },
    };
    expect(fitTextareaHeight(el)).toBe(72);
    expect(seen).toEqual(["96px"]);
    expect(parent.style.minHeight).toBe("");
    expect(el.style.height).toBe("72px");
  });

  it("tolerates a missing element", () => {
    expect(fitTextareaHeight(null)).toBeNull();
    expect(fitTextareaHeight(undefined)).toBeNull();
  });

  it("measures an empty composer without its placeholder, so it keeps one line", () => {
    // A wrapped placeholder counts in scrollHeight: the long "agent is
    // working" hint made the empty box two lines tall, and the chat moved at
    // every first key and every queued message.
    const placeholder = "L'agent travaille. Entrée met en file, Alt+Entrée envoie tout de suite";
    const el = {
      clientWidth: 320,
      value: "",
      placeholder,
      style: { height: "22px" },
      get scrollHeight() {
        return this.placeholder ? 44 : 22;
      },
    };
    expect(fitTextareaHeight(el)).toBe(22);
    expect(el.style.height).toBe("22px");
    expect(el.placeholder).toBe(placeholder);

    // With text, the text sets the height (the placeholder is not shown).
    const typed = { ...el, value: "a much longer message", get scrollHeight() { return 44; } };
    expect(fitTextareaHeight(typed)).toBe(44);
    expect(typed.placeholder).toBe(placeholder);
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
