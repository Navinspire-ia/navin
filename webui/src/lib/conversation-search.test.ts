// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import {
  haystackMatches,
  matchingRowIds,
  searchTerms,
  splitHighlight,
  stepMatchIndex,
  teamMessageHaystack,
  uiMessageHaystack,
} from "./conversation-search";

describe("conversation-search", () => {
  it("splits a query into terms", () => {
    expect(searchTerms("  Auth  login ")).toEqual(["auth", "login"]);
    expect(searchTerms("   ")).toEqual([]);
  });

  it("matches a team message on text, author, or file name", () => {
    const row = {
      id: "1",
      author: "Aymen",
      text: "salut ca va",
      file: { name: "voice-1.webm" },
    };
    expect(haystackMatches(teamMessageHaystack(row), ["salut"])).toBe(true);
    expect(haystackMatches(teamMessageHaystack(row), ["aymen"])).toBe(true);
    expect(haystackMatches(teamMessageHaystack(row), ["voice"])).toBe(true);
    expect(haystackMatches(teamMessageHaystack(row), ["missing"])).toBe(false);
  });

  it("skips trace rows in a chat conversation", () => {
    expect(uiMessageHaystack({ kind: "trace", content: "searching files" })).toBe("");
    expect(uiMessageHaystack({ role: "user", content: "fix login" })).toContain("fix login");
  });

  it("returns matching ids in order", () => {
    const rows = [
      { id: "a", text: "hello" },
      { id: "b", text: "later" },
      { id: "c", text: "hello again" },
    ];
    expect(matchingRowIds(rows, "hello", (row) => row.text)).toEqual(["a", "c"]);
  });

  it("steps through matches in a loop", () => {
    expect(stepMatchIndex(0, 3, 1)).toBe(1);
    expect(stepMatchIndex(2, 3, 1)).toBe(0);
    expect(stepMatchIndex(0, 3, -1)).toBe(2);
  });

  it("splits highlight chunks", () => {
    const parts = splitHighlight("Hello from Team", "from");
    expect(parts).toEqual([
      { text: "Hello ", hit: false },
      { text: "from", hit: true },
      { text: " Team", hit: false },
    ]);
  });
});
