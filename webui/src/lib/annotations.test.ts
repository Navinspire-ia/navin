// @vitest-environment jsdom
// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  addAnnotation,
  annotationIsOnMessage,
  clearAnnotations,
  contentFingerprint,
  dismissAnnotationRequest,
  formatReviewMessage,
  getAnnotationRequest,
  getAnnotations,
  locateQuote,
  parseReviewMessage,
  removeAnnotation,
  requestAnnotation,
  resetAnnotationsForTests,
  setActiveAnnotationScope,
  snapToWords,
  subscribeAnnotations,
  updateAnnotation,
  type AnnotationSource,
} from "./annotations";

const MESSAGE: AnnotationSource = { kind: "message", id: "m1", key: "k1" };

// jsdom runs on an opaque origin here: it exposes no localStorage.
const memory = new Map<string, string>();
Object.defineProperty(window, "localStorage", {
  configurable: true,
  value: {
    getItem: (key: string) => memory.get(key) ?? null,
    setItem: (key: string, value: string) => void memory.set(key, String(value)),
    removeItem: (key: string) => void memory.delete(key),
    clear: () => memory.clear(),
    key: (index: number) => [...memory.keys()][index] ?? null,
    get length() {
      return memory.size;
    },
  } satisfies Storage,
});

beforeEach(() => {
  window.localStorage.clear();
  resetAnnotationsForTests();
});

describe("text anchoring", () => {
  it("grows a selection to whole words and trims whitespace", () => {
    const text = "Use a message queue here.";
    expect(snapToWords(text, 8, 15)).toEqual({ start: 6, end: 19 });
    expect(text.slice(6, 19)).toBe("message queue");
    expect(snapToWords(text, 5, 6)).toEqual({ start: 6, end: 6 });
  });

  it("prefers the stored offset, then the nearest occurrence", () => {
    const text = "step one. step two. step three.";
    expect(locateQuote(text, "step", 10)).toBe(10);
    expect(locateQuote(text, "step", 18)).toBe(20);
    expect(locateQuote(text, "missing", 0)).toBe(-1);
  });

  it("fingerprints content stably", () => {
    expect(contentFingerprint("abc")).toBe(contentFingerprint("abc"));
    expect(contentFingerprint("abc")).not.toBe(contentFingerprint("abd"));
  });
});

describe("draft store", () => {
  it("adds, updates, removes and notifies", () => {
    const listener = vi.fn();
    const unsubscribe = subscribeAnnotations(listener);
    const first = addAnnotation("chat", { source: MESSAGE, quote: "alpha", offset: 4, action: "clarify", comment: " why? " });
    expect(first?.comment).toBe("why?");
    expect(getAnnotations("chat")).toHaveLength(1);
    const before = getAnnotations("chat");
    expect(getAnnotations("chat")).toBe(before);

    updateAnnotation("chat", first!.id, { action: "keep" });
    expect(getAnnotations("chat")[0].action).toBe("keep");
    removeAnnotation("chat", first!.id);
    expect(getAnnotations("chat")).toHaveLength(0);
    expect(listener).toHaveBeenCalledTimes(3);
    unsubscribe();
  });

  it("refuses an empty quote and keeps conversations apart", () => {
    expect(addAnnotation("chat", { source: MESSAGE, quote: "   ", offset: 0, action: "keep", comment: "" })).toBeNull();
    addAnnotation("chat", { source: MESSAGE, quote: "alpha", offset: 0, action: "keep", comment: "" });
    expect(getAnnotations("other")).toHaveLength(0);
  });

  it("survives a reload and forgets a cleared review", () => {
    addAnnotation("chat", { source: MESSAGE, quote: "alpha", offset: 0, action: "expand", comment: "more" });
    resetAnnotationsForTests();
    expect(getAnnotations("chat").map((item) => item.quote)).toEqual(["alpha"]);
    clearAnnotations("chat");
    resetAnnotationsForTests();
    expect(getAnnotations("chat")).toHaveLength(0);
  });

  it("matches a message by id or by content", () => {
    const item = addAnnotation("chat", { source: MESSAGE, quote: "alpha", offset: 0, action: "keep", comment: "" })!;
    expect(annotationIsOnMessage(item, "m1", "other")).toBe(true);
    expect(annotationIsOnMessage(item, "renamed", "k1")).toBe(true);
    expect(annotationIsOnMessage(item, "renamed", "other")).toBe(false);
  });
});

describe("selection requests", () => {
  const rect = { left: 0, top: 0, right: 10, bottom: 10 };

  it("needs a conversation to land in", () => {
    expect(requestAnnotation({ source: MESSAGE, quote: "alpha", offset: 0, rect })).toBe(false);
    setActiveAnnotationScope("chat");
    expect(requestAnnotation({ source: MESSAGE, quote: "alpha", offset: 0, rect })).toBe(true);
    expect(getAnnotationRequest()?.scope).toBe("chat");
    dismissAnnotationRequest();
    expect(getAnnotationRequest()).toBeNull();
  });
});

describe("feedback message", () => {
  it("lists annotations in document order and reads back", () => {
    addAnnotation("chat", { source: MESSAGE, quote: "second passage", offset: 90, action: "keep", comment: "" });
    addAnnotation("chat", { source: MESSAGE, quote: "first\npassage", offset: 5, action: "clarify", comment: "what does it mean?\nbe precise" });
    addAnnotation("chat", {
      source: { kind: "file", path: "src/app.ts", fromLine: 3, toLine: 9 },
      quote: "const a = 1;",
      offset: 0,
      action: "comment",
      comment: "rename this",
    });
    const text = formatReviewMessage(getAnnotations("chat"));
    expect(text.split("\n")[0]).toBe("[Review] 3 annotations on your answer and on files");
    expect(text).toContain("3. Comment - @src/app.ts (L3-9)");
    expect(text).toContain("do not start building");

    const items = parseReviewMessage(text);
    expect(items?.map((item) => item.action)).toEqual(["clarify", "keep", "comment"]);
    expect(items?.[0]).toMatchObject({
      quote: "first\npassage",
      comment: "what does it mean?\nbe precise",
      location: "",
    });
    expect(items?.[1].comment).toBe("");
    expect(items?.[2]).toMatchObject({ location: "@src/app.ts (L3-9)", comment: "rename this" });
  });

  it("ignores ordinary messages", () => {
    expect(parseReviewMessage("1. do this\n2. do that")).toBeNull();
    expect(formatReviewMessage([])).toBe("");
  });
});
