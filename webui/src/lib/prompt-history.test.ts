// @vitest-environment jsdom
// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  PROMPT_HISTORY_ENTRY_MAX_CHARS,
  PROMPT_HISTORY_LIMIT,
  PROMPT_HISTORY_TOTAL_MAX_CHARS,
  appendPromptHistory,
  forgetPromptHistory,
  promptHistoryKey,
  readPromptHistory,
} from "./prompt-history";

beforeEach(() => { localStorage.clear(); });

describe("prompt history", () => {
  it("stores the collapsed prompt with its paste placeholder", () => {
    const next = appendPromptHistory("websocket:a", [], "Review this [Pasted Content 50000 chars]");
    expect(next).toEqual(["Review this [Pasted Content 50000 chars]"]);
    expect(readPromptHistory("websocket:a")).toEqual(next);
  });

  it("caps each entry, the entry count and the total size", () => {
    const long = appendPromptHistory("websocket:b", [], "x".repeat(PROMPT_HISTORY_ENTRY_MAX_CHARS + 500));
    expect(long[0]).toHaveLength(PROMPT_HISTORY_ENTRY_MAX_CHARS);
    let history: string[] = [];
    for (let i = 0; i < PROMPT_HISTORY_LIMIT + 20; i += 1) history = appendPromptHistory("websocket:b", history, `p${i}`);
    expect(history).toHaveLength(PROMPT_HISTORY_LIMIT);
    expect(history[history.length - 1]).toBe(`p${PROMPT_HISTORY_LIMIT + 19}`);
    let big: string[] = [];
    for (let i = 0; i < PROMPT_HISTORY_LIMIT; i += 1) big = appendPromptHistory("websocket:c", big, `${i}`.padEnd(PROMPT_HISTORY_ENTRY_MAX_CHARS, "y"));
    expect(big.join("").length).toBeLessThanOrEqual(PROMPT_HISTORY_TOTAL_MAX_CHARS);
    expect(big[big.length - 1].startsWith(`${PROMPT_HISTORY_LIMIT - 1}`)).toBe(true);
  });

  it("forgets the history of a deleted chat only", () => {
    appendPromptHistory("websocket:keep", [], "keep");
    appendPromptHistory("websocket:gone", [], "gone");
    forgetPromptHistory("websocket:gone");
    expect(localStorage.getItem(promptHistoryKey("websocket:gone"))).toBeNull();
    expect(readPromptHistory("websocket:keep")).toEqual(["keep"]);
  });
});

describe("deleteSession", () => {
  it("forgets the composer's prompt history of a deleted chat, and only then", async () => {
    const { deleteSession } = await import("./api");
    const key = "websocket:chat-9";
    localStorage.setItem(promptHistoryKey(key), JSON.stringify(["secret paste"]));
    const fetchMock = vi.spyOn(globalThis, "fetch");
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ deleted: false, blocked_by_automations: true }), {
        status: 200, headers: { "Content-Type": "application/json" },
      }),
    );
    await deleteSession("t", key);
    // Blocked by its automations: the chat is still there, so is its history.
    expect(localStorage.getItem(promptHistoryKey(key))).not.toBeNull();
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ deleted: true }), {
        status: 200, headers: { "Content-Type": "application/json" },
      }),
    );
    await deleteSession("t", key, { deleteAutomations: true });
    expect(localStorage.getItem(promptHistoryKey(key))).toBeNull();
    fetchMock.mockRestore();
  });
});
