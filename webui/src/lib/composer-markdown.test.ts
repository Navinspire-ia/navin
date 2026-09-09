// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import {
  applyComposerFormat,
  insertAtCaret,
  insertCodeBlock,
  insertLink,
  replaceRange,
  toggleBulletList,
  toggleOrderedList,
  toggleQuote,
  toggleWrap,
} from "./composer-markdown";

describe("composer-markdown", () => {
  it("wraps the selection and keeps it selected", () => {
    const out = toggleWrap({ value: "hello world", start: 6, end: 11 }, "**");
    expect(out.value).toBe("hello **world**");
    expect(out.value.slice(out.start, out.end)).toBe("world");
  });

  it("unwraps when the markers are inside the selection", () => {
    const out = toggleWrap({ value: "hello **world**", start: 6, end: 15 }, "**");
    expect(out.value).toBe("hello world");
    expect(out.value.slice(out.start, out.end)).toBe("world");
  });

  it("unwraps when the markers sit just around the selection", () => {
    const out = toggleWrap({ value: "hello **world**", start: 8, end: 13 }, "**");
    expect(out.value).toBe("hello world");
    expect(out.value.slice(out.start, out.end)).toBe("world");
  });

  it("inserts a placeholder at a bare caret and selects it", () => {
    const out = toggleWrap({ value: "say ", start: 4, end: 4 }, "**", "**", "bold");
    expect(out.value).toBe("say **bold**");
    expect(out.value.slice(out.start, out.end)).toBe("bold");
  });

  it("handles asymmetric markers such as underline", () => {
    const on = toggleWrap({ value: "abc", start: 0, end: 3 }, "<u>", "</u>");
    expect(on.value).toBe("<u>abc</u>");
    const off = toggleWrap({ value: on.value, start: on.start, end: on.end }, "<u>", "</u>");
    expect(off.value).toBe("abc");
  });

  it("prefixes every selected line for a bullet list and toggles it back", () => {
    const source = "one\ntwo\nthree";
    const on = toggleBulletList({ value: source, start: 0, end: source.length });
    expect(on.value).toBe("- one\n- two\n- three");
    const off = toggleBulletList({ value: on.value, start: 0, end: on.value.length });
    expect(off.value).toBe(source);
  });

  it("numbers the selected lines in order", () => {
    const source = "one\ntwo\nthree";
    const on = toggleOrderedList({ value: source, start: 0, end: source.length });
    expect(on.value).toBe("1. one\n2. two\n3. three");
    expect(toggleOrderedList({ value: on.value, start: 0, end: on.value.length }).value).toBe(
      source,
    );
  });

  it("quotes only the lines the selection touches", () => {
    const source = "intro\nquoted\ntail";
    const out = toggleQuote({ value: source, start: 7, end: 9 });
    expect(out.value).toBe("intro\n> quoted\ntail");
    expect(toggleQuote({ value: out.value, start: 7, end: 9 }).value).toBe(source);
  });

  it("keeps indentation when toggling a list on an indented line", () => {
    const on = toggleBulletList({ value: "  deep", start: 0, end: 6 });
    expect(on.value).toBe("  - deep");
    expect(toggleBulletList({ value: on.value, start: 0, end: on.value.length }).value).toBe(
      "  deep",
    );
  });

  it("turns a selected url into the link target", () => {
    const out = insertLink({ value: "see https://navin.live", start: 4, end: 22 });
    expect(out.value).toBe("see [text](https://navin.live)");
    expect(out.value.slice(out.start, out.end)).toBe("text");
  });

  it("turns selected words into the link label and focuses the url", () => {
    const out = insertLink({ value: "read the docs", start: 9, end: 13 });
    expect(out.value).toBe("read the [docs](url)");
    expect(out.value.slice(out.start, out.end)).toBe("url");
  });

  it("opens a fenced block on its own lines", () => {
    const out = insertCodeBlock({ value: "before", start: 6, end: 6 });
    expect(out.value).toBe("before\n```\n\n```");
    const wrapped = insertCodeBlock({ value: "let a = 1", start: 0, end: 9 });
    expect(wrapped.value).toBe("```\nlet a = 1\n```");
    expect(wrapped.value.slice(wrapped.start, wrapped.end)).toBe("let a = 1");
  });

  it("inserts at the caret and leaves the caret after the text", () => {
    const out = insertAtCaret({ value: "hi ", start: 3, end: 3 }, "🙂");
    expect(out.value).toBe("hi 🙂");
    expect(out.start).toBe(out.end);
    expect(out.value.slice(0, out.start)).toBe("hi 🙂");
  });

  it("replaces an explicit range for mention completion", () => {
    const out = replaceRange("ping @am rest", 5, 8, "@amira ");
    expect(out.value).toBe("ping @amira  rest");
    expect(out.start).toBe(12);
  });

  it("routes every toolbar action", () => {
    const sel = { value: "word", start: 0, end: 4 };
    expect(applyComposerFormat(sel, "bold").value).toBe("**word**");
    expect(applyComposerFormat(sel, "italic").value).toBe("*word*");
    expect(applyComposerFormat(sel, "strike").value).toBe("~~word~~");
    expect(applyComposerFormat(sel, "highlight").value).toBe("==word==");
    expect(applyComposerFormat(sel, "underline").value).toBe("<u>word</u>");
    expect(applyComposerFormat(sel, "code").value).toBe("`word`");
    expect(applyComposerFormat(sel, "quote").value).toBe("> word");
    expect(applyComposerFormat(sel, "bullet").value).toBe("- word");
    expect(applyComposerFormat(sel, "ordered").value).toBe("1. word");
    expect(applyComposerFormat(sel, "codeBlock").value).toBe("```\nword\n```");
    expect(applyComposerFormat(sel, "link").value).toBe("[word](url)");
  });

  it("clamps a selection that points outside the value", () => {
    const out = toggleWrap({ value: "ab", start: -5, end: 99 }, "**");
    expect(out.value).toBe("**ab**");
  });

  it("stacks formats without nesting the same mark twice", () => {
    const bold = toggleWrap({ value: "hhhh", start: 0, end: 4 }, "**");
    const strike = toggleWrap({ value: bold.value, start: bold.start, end: bold.end }, "~~");
    const italic = toggleWrap(
      { value: strike.value, start: strike.start, end: strike.end },
      "_",
    );
    expect(italic.value).toBe("**~~_hhhh_~~**");
    const boldAgain = toggleWrap(
      { value: italic.value, start: italic.start, end: italic.end },
      "**",
    );
    expect(boldAgain.value).not.toContain("****");
  });
});
