// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import { disableNativeAutocorrectOn } from "./disable-native-autocorrect";

function fakeField(tagName: "INPUT" | "TEXTAREA" | "DIV", contentEditable = false) {
  const attrs = new Map<string, string>();
  return {
    tagName,
    isContentEditable: contentEditable,
    getAttribute: (key: string) => attrs.get(key) ?? null,
    setAttribute: (key: string, value: string) => {
      attrs.set(key, value);
    },
  } as unknown as HTMLElement;
}

describe("disableNativeAutocorrectOn", () => {
  it("turns off spellcheck on a chat textarea", () => {
    const el = fakeField("TEXTAREA");
    disableNativeAutocorrectOn(el);
    expect(el.getAttribute("spellcheck")).toBe("false");
    expect(el.getAttribute("autocorrect")).toBe("off");
    expect(el.getAttribute("autocapitalize")).toBe("off");
    expect(el.getAttribute("autocomplete")).toBe("off");
  });

  it("turns off spellcheck on a model search input", () => {
    const el = fakeField("INPUT");
    disableNativeAutocorrectOn(el);
    expect(el.getAttribute("spellcheck")).toBe("false");
    expect(el.getAttribute("autocomplete")).toBe("off");
  });

  it("does not strip password autofill", () => {
    const el = fakeField("INPUT");
    el.setAttribute("type", "password");
    el.setAttribute("autocomplete", "current-password");
    disableNativeAutocorrectOn(el);
    expect(el.getAttribute("spellcheck")).toBe("false");
    expect(el.getAttribute("autocomplete")).toBe("current-password");
  });

  it("leaves a non-editable node alone", () => {
    const el = fakeField("DIV");
    disableNativeAutocorrectOn(el);
    expect(el.getAttribute("spellcheck")).toBeNull();
  });
});
