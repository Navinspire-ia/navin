// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { afterAll, beforeEach, describe, expect, it } from "vitest";

import {
  clearComposerDraft,
  readComposerDraft,
  readComposerDraftState,
  writeComposerDraft,
  composerDraftStorageKey,
} from "./composer-draft";

function installStorageStub(): () => void {
  const store = new Map<string, string>();
  const localStorage = {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => void store.set(key, String(value)),
    removeItem: (key: string) => void store.delete(key),
    clear: () => store.clear(),
    key: (index: number) => [...store.keys()][index] ?? null,
    get length() {
      return store.size;
    },
  } satisfies Storage;
  const globals = globalThis as { window?: unknown };
  const previous = globals.window;
  globals.window = { localStorage } as unknown as Window & typeof globalThis;
  return () => {
    if (previous === undefined) delete globals.window;
    else globals.window = previous;
  };
}

const restore = installStorageStub();
afterAll(restore);

describe("composer-draft", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("round-trips draft text per chat", () => {
    writeComposerDraft("websocket:1", "continue the audit");
    expect(readComposerDraft("websocket:1")).toBe("continue the audit");
    expect(readComposerDraft("websocket:2")).toBe("");
  });

  it("clears empty drafts from storage", () => {
    writeComposerDraft("websocket:1", "hello");
    writeComposerDraft("websocket:1", "   ");
    expect(window.localStorage.getItem(composerDraftStorageKey("websocket:1")!)).toBeNull();
    expect(readComposerDraft("websocket:1")).toBe("");
  });

  it("clearComposerDraft removes the key", () => {
    writeComposerDraft("websocket:1", "queued thought");
    clearComposerDraft("websocket:1");
    expect(readComposerDraft("websocket:1")).toBe("");
  });

  it("keeps pasted bodies next to the chip so a reload can still send them", () => {
    const token = "[Pasted Content 1148 chars]";
    const body = "z".repeat(1148);
    writeComposerDraft("websocket:1", `merci\n${token}`, { [token]: body });
    expect(readComposerDraft("websocket:1")).toBe(`merci\n${token}`);
    expect(readComposerDraftState("websocket:1").pastes[token]).toBe(body);
  });

  it("still reads a plain-text draft written by an older build", () => {
    window.localStorage.setItem(
      composerDraftStorageKey("websocket:1")!,
      "continue the audit",
    );
    expect(readComposerDraftState("websocket:1")).toEqual({
      text: "continue the audit",
      pastes: {},
    });
  });
});
