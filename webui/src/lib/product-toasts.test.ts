// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  DISMISSED_UPDATE_TOAST_KEY,
  dismissUpdateToast,
  isReleaseAnnouncement,
  isUpdateToastDismissed,
  nextAvailableUpdate,
  parseDismissedUpdateToast,
  readDismissedUpdateToast,
  resetDismissedUpdateToast,
} from "./product-toasts";

describe("isReleaseAnnouncement", () => {
  it("matches an admin-published release card", () => {
    expect(isReleaseAnnouncement({ id: "release-1.0.4", kind: "release" })).toBe(true);
  });

  it("matches by id when kind was stripped", () => {
    expect(isReleaseAnnouncement({ id: "release-1.0.4" })).toBe(true);
  });

  it("leaves ordinary news alone", () => {
    expect(isReleaseAnnouncement({ id: "models-qwen", kind: "models" })).toBe(false);
    expect(isReleaseAnnouncement({ id: "news-welcome", kind: "news" })).toBe(false);
  });
});

const available = {
  currentVersion: "2.0.2",
  latestVersion: "2.0.3",
  available: true,
};

const gone = {
  currentVersion: "2.0.3",
  available: false,
};

describe("parseDismissedUpdateToast", () => {
  it("reads a version plus expiry", () => {
    expect(
      parseDismissedUpdateToast(JSON.stringify({ version: "2.0.3", until: 9 })),
    ).toEqual({ version: "2.0.3", until: 9 });
  });

  it("rejects junk", () => {
    expect(parseDismissedUpdateToast("")).toBeNull();
    expect(parseDismissedUpdateToast("{")).toBeNull();
    expect(parseDismissedUpdateToast(JSON.stringify({ version: "2.0.3" }))).toBeNull();
  });
});

describe("isUpdateToastDismissed", () => {
  it("hides only that version while the snooze is live", () => {
    const dismissed = { version: "2.0.3", until: 100 };
    expect(isUpdateToastDismissed("2.0.3", dismissed, 99)).toBe(true);
    expect(isUpdateToastDismissed("2.0.3", dismissed, 100)).toBe(false);
    expect(isUpdateToastDismissed("2.0.4", dismissed, 99)).toBe(false);
  });
});

describe("nextAvailableUpdate", () => {
  it("does not bring a Later-dismissed version back", () => {
    expect(
      nextAvailableUpdate(null, available, { version: "2.0.3", until: 50 }, 10),
    ).toBeNull();
  });

  it("keeps the same object so the card does not remount", () => {
    expect(nextAvailableUpdate(available, { ...available }, null, 10)).toBe(available);
  });

  it("shows a newer version after a previous snooze", () => {
    const newer = { ...available, latestVersion: "2.0.4" };
    expect(
      nextAvailableUpdate(null, newer, { version: "2.0.3", until: 50 }, 10),
    ).toBe(newer);
  });

  it("clears the card once the install is gone", () => {
    expect(nextAvailableUpdate(available, gone, null, 10)).toBeNull();
  });
});

function installMemoryLocalStorage() {
  const store = new Map<string, string>();
  const storage = {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => {
      store.set(key, value);
    },
    removeItem: (key: string) => {
      store.delete(key);
    },
    clear: () => store.clear(),
  };
  vi.stubGlobal("localStorage", storage);
  return storage;
}

describe("dismissUpdateToast", () => {
  beforeEach(() => {
    installMemoryLocalStorage();
    resetDismissedUpdateToast();
  });

  afterEach(() => {
    resetDismissedUpdateToast();
    vi.unstubAllGlobals();
  });

  it("survives a remount so Later sticks", () => {
    dismissUpdateToast("2.0.3", 1_000, 50);
    resetDismissedUpdateToast();
    const dismissed = readDismissedUpdateToast();
    expect(dismissed?.version).toBe("2.0.3");
    expect(dismissed?.until).toBe(1_050);
    expect(globalThis.localStorage.getItem(DISMISSED_UPDATE_TOAST_KEY)).toContain(
      "2.0.3",
    );
  });
});
