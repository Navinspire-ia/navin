import { describe, expect, it } from "vitest";

import { cachedLanguageSupport } from "./editorLanguage";

describe("cachedLanguageSupport", () => {
  it("returns one instance per language, which is what stops the flicker", () => {
    // A fresh LanguageSupport re-parses the whole document and drops the
    // highlighting until the parse lands. The workbench rebuilds the extension
    // list on every poll, so a new instance each time made the editor blink.
    const first = cachedLanguageSupport("python");
    const second = cachedLanguageSupport("python");
    expect(first).not.toBeNull();
    expect(second).toBe(first);
  });

  it("treats an alias and its target as the same language", () => {
    expect(cachedLanguageSupport("typescript")).toBe(cachedLanguageSupport("ts"));
    expect(cachedLanguageSupport("golang")).toBe(cachedLanguageSupport("go"));
  });

  it("ignores case, so a capitalised name is not a second instance", () => {
    expect(cachedLanguageSupport("Python")).toBe(cachedLanguageSupport("python"));
  });

  it("supports dart, so Flutter projects are highlighted", () => {
    expect(cachedLanguageSupport("dart")).not.toBeNull();
  });

  it("answers null for an unknown language instead of throwing", () => {
    expect(cachedLanguageSupport("not-a-language")).toBeNull();
    expect(cachedLanguageSupport("")).toBeNull();
  });

  it("caches the null answer too, so an unsupported file stays stable", () => {
    const first = cachedLanguageSupport("still-not-a-language");
    const second = cachedLanguageSupport("still-not-a-language");
    expect(first).toBeNull();
    expect(second).toBe(first);
  });
});
