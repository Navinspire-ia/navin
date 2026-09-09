// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  HOST_CHROME_FORCE_KEY,
  initHostChromePreview,
  readForcedHostChrome,
  shouldShowHostChrome,
  writeForcedHostChrome,
} from "./host-chrome";

vi.mock("./bootstrap", () => ({
  consumeUrlHostChromeFlag: vi.fn(() => ""),
}));

vi.mock("./desktop", () => ({
  isDesktopShell: vi.fn(() => false),
}));

import { consumeUrlHostChromeFlag } from "./bootstrap";
import { isDesktopShell } from "./desktop";

function installMemoryStorage() {
  const store = new Map<string, string>();
  const storage = {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => {
      store.set(key, String(value));
    },
    removeItem: (key: string) => {
      store.delete(key);
    },
    clear: () => store.clear(),
    key: (index: number) => [...store.keys()][index] ?? null,
    get length() {
      return store.size;
    },
  };
  vi.stubGlobal("localStorage", storage);
  vi.stubGlobal("window", { localStorage: storage });
}

describe("host-chrome preview", () => {
  beforeEach(() => {
    installMemoryStorage();
    vi.mocked(consumeUrlHostChromeFlag).mockReturnValue("");
    vi.mocked(isDesktopShell).mockReturnValue(false);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("follows the native runtime without storage", () => {
    expect(shouldShowHostChrome(true)).toBe(true);
    expect(shouldShowHostChrome(false)).toBe(false);
  });

  it("follows a real desktop shell even when runtime says browser", () => {
    vi.mocked(isDesktopShell).mockReturnValue(true);
    expect(shouldShowHostChrome(false)).toBe(true);
  });

  it("lets a browser tab opt into the Tauri chrome", () => {
    writeForcedHostChrome(true);
    expect(readForcedHostChrome()).toBe(true);
    expect(shouldShowHostChrome(false)).toBe(true);
    writeForcedHostChrome(false);
    expect(window.localStorage.getItem(HOST_CHROME_FORCE_KEY)).toBeNull();
    expect(shouldShowHostChrome(false)).toBe(false);
  });

  it("persists hostChrome=1 from the URL hash", () => {
    vi.mocked(consumeUrlHostChromeFlag).mockReturnValue("1");
    initHostChromePreview();
    expect(readForcedHostChrome()).toBe(true);
  });

  it("clears the preview on hostChrome=0", () => {
    writeForcedHostChrome(true);
    vi.mocked(consumeUrlHostChromeFlag).mockReturnValue("0");
    initHostChromePreview();
    expect(readForcedHostChrome()).toBe(false);
  });
});
