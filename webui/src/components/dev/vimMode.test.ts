// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { EditorState } from "@codemirror/state";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  loadVimExtension,
  readVimModeEnabled,
  VIM_STORAGE_KEY,
  writeVimModeEnabled,
} from "./vimMode";

function installMemoryStorage() {
  const store = new Map<string, string>();
  const storage = {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => {
      store.set(key, value);
    },
    removeItem: (key: string) => {
      store.delete(key);
    },
    clear: () => {
      store.clear();
    },
    key: (index: number) => [...store.keys()][index] ?? null,
    get length() {
      return store.size;
    },
  };
  vi.stubGlobal("window", { localStorage: storage });
  vi.stubGlobal("localStorage", storage);
  return storage;
}

describe("vimMode preference", () => {
  beforeEach(() => {
    installMemoryStorage();
  });

  it("defaults to off", () => {
    expect(readVimModeEnabled()).toBe(false);
  });

  it("persists on/off in localStorage", () => {
    writeVimModeEnabled(true);
    expect(window.localStorage.getItem(VIM_STORAGE_KEY)).toBe("1");
    expect(readVimModeEnabled()).toBe(true);
    writeVimModeEnabled(false);
    expect(window.localStorage.getItem(VIM_STORAGE_KEY)).toBe("0");
    expect(readVimModeEnabled()).toBe(false);
  });
});

describe("loadVimExtension", () => {
  it("returns a CodeMirror extension that mounts cleanly", async () => {
    const extension = await loadVimExtension();
    const state = EditorState.create({
      doc: "hello\n",
      extensions: [extension],
    });
    expect(state.doc.toString()).toBe("hello\n");
  });
});
