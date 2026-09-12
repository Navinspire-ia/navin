// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import {
  allocatePasteToken,
  collapseTextForComposer,
  collapseUserDisplayLabel,
  expandPastedContent,
  pastedContentLabel,
  shouldCollapsePastedText,
  splitLongUserText,
  extractInsertedText,
  splitPastedContentSegments,
} from "./pasted-content";

describe("pasted-content", () => {
  it("chips at 1000 characters", () => {
    expect(shouldCollapsePastedText("a".repeat(999))).toBe(false);
    expect(shouldCollapsePastedText("a".repeat(1000))).toBe(true);
  });

  it("uses the Cursor chip label", () => {
    expect(pastedContentLabel(11448)).toBe("[Pasted Content 11448 chars]");
    expect(pastedContentLabel(11448, "2")).toBe("[Pasted Content 11448 chars #2]");
  });

  it("expands a chip back to the stored body", () => {
    const token = pastedContentLabel(5);
    expect(expandPastedContent(`fix this\n${token}`, { [token]: "hello" })).toBe(
      "fix this\nhello",
    );
  });

  it("keeps a short prompt in front of a dump", () => {
    const rest = "x".repeat(1200);
    expect(splitLongUserText(`regarde ca\n${rest}`)).toEqual({
      prefix: "regarde ca",
      rest,
    });
  });

  it("collapses a composer dump without losing the body", () => {
    const blob = "z".repeat(1148);
    const collapsed = collapseTextForComposer(`merci\n${blob}`);
    expect(collapsed.text).toBe("merci\n[Pasted Content 1148 chars]");
    expect(expandPastedContent(collapsed.text, collapsed.pastes)).toBe(`merci\n${blob}`);
  });

  it("gives a second same-length paste its own token", () => {
    const first = allocatePasteToken(12, {});
    const second = allocatePasteToken(12, { [first]: "aaaaaaaaaaaa" });
    expect(first).toBe("[Pasted Content 12 chars]");
    expect(second).toBe("[Pasted Content 12 chars #2]");
  });

  it("splits tokens out of surrounding prose", () => {
    const token = pastedContentLabel(3);
    expect(splitPastedContentSegments(`voir ${token} svp`)).toEqual([
      { kind: "text", text: "voir " },
      { kind: "paste", text: token },
      { kind: "text", text: " svp" },
    ]);
  });

  it("recovers a dump inserted without a paste event", () => {
    const dump = "w".repeat(1200);
    expect(extractInsertedText("fix this\n", `fix this\n${dump}`)).toBe(dump);
    expect(extractInsertedText("ok", "ok short")).toBeNull();
  });

  it("labels a queued long prompt as a chip", () => {
    expect(collapseUserDisplayLabel("a".repeat(1148))).toBe("[Pasted Content 1148 chars]");
    expect(collapseUserDisplayLabel(`note\n${"b".repeat(1148)}`)).toBe(
      "note [Pasted Content 1148 chars]",
    );
  });
});
