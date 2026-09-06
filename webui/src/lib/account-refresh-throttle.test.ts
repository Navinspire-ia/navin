import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  claimForcedRefresh,
  FORCED_REFRESH_MIN_MS,
  lastForcedRefreshAt,
  markForcedRefresh,
  resetForcedRefreshThrottle,
} from "./account-refresh-throttle";

function installMemorySessionStorage() {
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
  vi.stubGlobal("sessionStorage", storage);
  return store;
}

describe("forced account refresh throttle", () => {
  beforeEach(() => {
    installMemorySessionStorage();
    resetForcedRefreshThrottle();
  });

  it("allows the first forced validation and blocks the burst behind it", () => {
    const now = 1_000_000;
    expect(claimForcedRefresh(now)).toBe(true);
    expect(claimForcedRefresh(now + 1)).toBe(false);
    expect(claimForcedRefresh(now + FORCED_REFRESH_MIN_MS - 1)).toBe(false);
    expect(claimForcedRefresh(now + FORCED_REFRESH_MIN_MS)).toBe(true);
  });

  it("survives a page reload, which wipes module state but not the session", async () => {
    const now = 2_000_000;
    expect(claimForcedRefresh(now)).toBe(true);
    // A full reload drops every module-level variable, so re-import the module
    // with an empty registry while sessionStorage keeps its value. The
    // persisted stamp is what stops each reload from paying another blocking
    // navin.live round-trip.
    vi.resetModules();
    const reloaded = await import("./account-refresh-throttle");
    expect(reloaded.lastForcedRefreshAt()).toBe(now);
    expect(reloaded.claimForcedRefresh(now + 500)).toBe(false);
    expect(reloaded.claimForcedRefresh(now + FORCED_REFRESH_MIN_MS + 1)).toBe(true);
  });

  it("keeps working when sessionStorage throws, e.g. private mode", () => {
    vi.stubGlobal("sessionStorage", {
      getItem: () => {
        throw new Error("denied");
      },
      setItem: () => {
        throw new Error("denied");
      },
      removeItem: () => {
        throw new Error("denied");
      },
    });
    const now = 3_000_000;
    expect(claimForcedRefresh(now)).toBe(true);
    expect(claimForcedRefresh(now + 10)).toBe(false);
  });

  it("reports the newest stamp between memory and the session", () => {
    markForcedRefresh(4_000_000);
    expect(lastForcedRefreshAt()).toBe(4_000_000);
    // An older mark must not rewind the throttle and reopen the floodgate.
    markForcedRefresh(3_000_000);
    expect(lastForcedRefreshAt()).toBe(4_000_000);
  });
});
