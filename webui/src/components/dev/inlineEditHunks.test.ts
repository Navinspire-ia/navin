// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import {
  buildInlineEditHunks,
  composeInlineEdit,
  countChangedHunks,
  defaultAcceptedHunkIds,
} from "./inlineEditHunks";

describe("inlineEditHunks", () => {
  it("builds change hunks for multi-region rewrites", () => {
    const before = "a = 1\nb = 2\nc = 3\n";
    const after = "a = 10\nb = 2\nc = 30\n";
    const hunks = buildInlineEditHunks(before, after);
    expect(countChangedHunks(hunks)).toBe(2);
    const accepted = defaultAcceptedHunkIds(hunks);
    expect(composeInlineEdit(hunks, accepted)).toBe(after);
  });

  it("keeps rejected hunks as the original text", () => {
    const before = "foo()\nbar()\n";
    const after = "foo2()\nbar2()\n";
    const hunks = buildInlineEditHunks(before, after);
    const changes = hunks.filter((h) => h.kind === "change");
    expect(changes.length).toBeGreaterThanOrEqual(1);
    const onlyFirst = new Set([changes[0]!.id]);
    const composed = composeInlineEdit(hunks, onlyFirst);
    expect(composed.includes("foo2()")).toBe(true);
    expect(composed.includes("bar()")).toBe(true);
  });

  it("returns a single equal hunk when nothing changed", () => {
    const text = "same\n";
    const hunks = buildInlineEditHunks(text, text);
    expect(hunks).toHaveLength(1);
    expect(hunks[0]?.kind).toBe("equal");
    expect(countChangedHunks(hunks)).toBe(0);
  });
});
