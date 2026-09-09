// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  appendCacheBust,
  bookmarkTitleFromUrl,
  clearPreviewRecents,
  displayPreviewHref,
  MAX_PREVIEW_RECENTS,
  persistPreviewBookmarkBar,
  PREVIEW_BOOKMARK_BAR_KEY,
  PREVIEW_RECENTS_KEY,
  readPreviewBookmarkBar,
  readPreviewBookmarks,
  readPreviewRecents,
  rememberPreviewRecent,
  removePreviewBookmark,
  upsertPreviewBookmark,
} from "./preview-browser-storage";

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
  vi.stubGlobal("localStorage", storage);
  return storage;
}

describe("preview browser storage", () => {
  beforeEach(() => {
    installMemoryStorage();
  });

  it("formats recents like Cursor (host + path, no protocol)", () => {
    expect(displayPreviewHref("http://localhost:4321/index.html")).toBe(
      "localhost:4321/index.html",
    );
    expect(displayPreviewHref("http://localhost:4321/index.html#how-it-works")).toBe(
      "localhost:4321/index.html#how-it-works",
    );
    expect(displayPreviewHref("http://localhost:4321/")).toBe("localhost:4321");
  });

  it("moves a revisited URL to the front and caps the list", () => {
    rememberPreviewRecent("http://localhost:4321/");
    rememberPreviewRecent("http://localhost:4321/trust.html");
    rememberPreviewRecent("http://localhost:4321/");
    expect(readPreviewRecents()[0]).toBe("http://localhost:4321/");
    expect(readPreviewRecents()).toHaveLength(2);

    for (let i = 0; i < MAX_PREVIEW_RECENTS + 4; i += 1) {
      rememberPreviewRecent(`http://localhost:${4000 + i}/`);
    }
    expect(readPreviewRecents()).toHaveLength(MAX_PREVIEW_RECENTS);
  });

  it("clears recents without touching the bookmark bar flag", () => {
    rememberPreviewRecent("http://localhost:4321/");
    persistPreviewBookmarkBar(true);
    clearPreviewRecents();
    expect(readPreviewRecents()).toEqual([]);
    expect(localStorage.getItem(PREVIEW_RECENTS_KEY)).toBeNull();
    expect(readPreviewBookmarkBar()).toBe(true);
    expect(localStorage.getItem(PREVIEW_BOOKMARK_BAR_KEY)).toBe("1");
  });

  it("adds and removes bookmarks by URL", () => {
    upsertPreviewBookmark("localhost:4321/trust.html");
    expect(readPreviewBookmarks()[0]?.url).toBe("http://localhost:4321/trust.html");
    expect(bookmarkTitleFromUrl("http://localhost:4321/trust.html")).toBe("trust.html");
    removePreviewBookmark("http://localhost:4321/trust.html");
    expect(readPreviewBookmarks()).toEqual([]);
  });

  it("appends a cache-bust query without dropping the path", () => {
    expect(appendCacheBust("http://127.0.0.1:4173/app", 99)).toContain("_navin_reload=99");
    expect(appendCacheBust("http://127.0.0.1:4173/app?x=1", 99)).toContain("x=1");
    expect(appendCacheBust("", 99)).toBe("");
  });
});
