import { EditorState } from "@codemirror/state";
import type { EditorView } from "@codemirror/view";
import { describe, expect, it } from "vitest";

import { __inlineAiTest, currentGhostText } from "./inlineAi";

/** Headless view stub: accept/dismiss only need state + dispatch. */
function mount(doc: string, ghostText: string, from: number): EditorView {
  let state = EditorState.create({
    doc,
    extensions: [__inlineAiTest.ghostField],
  });
  state = state.update({
    effects: __inlineAiTest.setGhost.of({ text: ghostText, from }),
  }).state;
  const view = {
    get state() {
      return state;
    },
    dispatch(spec: Parameters<EditorView["dispatch"]>[0]) {
      state = state.update(spec as never).state;
    },
  };
  return view as unknown as EditorView;
}

describe("inlineAi ghost accept", () => {
  it("keeps Tab latency in the Cursor-class debounce budget", () => {
    expect(__inlineAiTest.IDLE_DELAY_MIN_MS).toBeGreaterThanOrEqual(50);
    expect(__inlineAiTest.IDLE_DELAY_MAX_MS).toBeLessThanOrEqual(150);
    expect(__inlineAiTest.IDLE_DELAY_MIN_MS).toBeLessThanOrEqual(
      __inlineAiTest.IDLE_DELAY_MAX_MS,
    );
  });

  it("adapts idle delay inside the 80-120 ms band", () => {
    expect(__inlineAiTest.adaptiveIdleDelayMs(50)).toBe(80);
    expect(__inlineAiTest.adaptiveIdleDelayMs(500)).toBe(120);
  });

  it("accepts one word with Tab (plan: Tab = word)", () => {
    const view = mount("const x = ", "map.getValue()", 10);
    expect(__inlineAiTest.acceptGhostWord(view)).toBe(true);
    expect(view.state.doc.toString()).toBe("const x = map");
    expect(currentGhostText(view)).toBe(".getValue()");
  });

  it("accepts the first line with Mod-ArrowRight", () => {
    const view = mount("", "one()\ntwo()\n", 0);
    expect(__inlineAiTest.acceptGhostLine(view)).toBe(true);
    expect(view.state.doc.toString()).toBe("one()\n");
    expect(currentGhostText(view)).toBe("two()\n");
  });

  it("accepts the full ghost with Mod-Enter", () => {
    const view = mount("const x = ", "42;", 10);
    expect(__inlineAiTest.acceptGhost(view)).toBe(true);
    expect(view.state.doc.toString()).toBe("const x = 42;");
    expect(currentGhostText(view)).toBe("");
  });

  it("dismisses ghost on Escape without mutating the buffer", () => {
    const view = mount("keep", " trash", 4);
    expect(__inlineAiTest.dismissGhost(view)).toBe(true);
    expect(view.state.doc.toString()).toBe("keep");
    expect(currentGhostText(view)).toBe("");
  });

  it("caches identical prefix/suffix completions", () => {
    __inlineAiTest.clearCompletionCache();
    __inlineAiTest.writeCompletionCache("const x = ", "", "42");
    expect(__inlineAiTest.readCompletionCache("const x = ", "")).toBe("42");
    expect(__inlineAiTest.readCompletionCache("const y = ", "")).toBeNull();
  });
});
