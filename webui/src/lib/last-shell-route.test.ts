import { afterAll, beforeEach, describe, expect, it } from "vitest";

import {
  isEmptyShellHash,
  isEphemeralShellHash,
  isEphemeralShellView,
  normalizeShellHash,
  readLastShellRouteHash,
  rememberLastShellRoute,
  shellHashesEqual,
  LAST_SHELL_ROUTE_KEY,
} from "./last-shell-route";

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

describe("last-shell-route", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("treats empty and /new as empty hashes", () => {
    expect(isEmptyShellHash("")).toBe(true);
    expect(isEmptyShellHash("#/new")).toBe(true);
    expect(isEmptyShellHash("/new")).toBe(true);
    expect(isEmptyShellHash("#/code?chat=websocket:1")).toBe(false);
  });

  it("persists and reads a durable last route", () => {
    rememberLastShellRoute("#/code?chat=websocket:abc");
    expect(window.localStorage.getItem(LAST_SHELL_ROUTE_KEY)).toBe(
      "#/code?chat=websocket:abc",
    );
    expect(readLastShellRouteHash()).toBe("#/code?chat=websocket:abc");
  });

  it("does not persist empty routes", () => {
    rememberLastShellRoute("#/code?chat=websocket:abc");
    rememberLastShellRoute("#/new");
    expect(readLastShellRouteHash()).toBe("#/code?chat=websocket:abc");
  });

  it("does not persist Settings overlays", () => {
    rememberLastShellRoute("#/montage");
    rememberLastShellRoute("#/settings?section=image");
    expect(readLastShellRouteHash()).toBe("#/montage");
    expect(isEphemeralShellHash("#/settings?section=image")).toBe(true);
    expect(isEphemeralShellHash("#/apps")).toBe(true);
    expect(isEphemeralShellHash("#/tools")).toBe(true);
    expect(isEphemeralShellHash("#/chat/websocket%3Ax")).toBe(false);
  });

  it("treats Settings / Apps as ephemeral views for Back navigation", () => {
    expect(isEphemeralShellView("settings")).toBe(true);
    expect(isEphemeralShellView("apps")).toBe(true);
    expect(isEphemeralShellView("tools")).toBe(true);
    expect(isEphemeralShellView("project")).toBe(false);
    expect(isEphemeralShellView("dev")).toBe(false);
  });

  it("ignores a stored Settings route on read", () => {
    window.localStorage.setItem(LAST_SHELL_ROUTE_KEY, "#/settings?section=image");
    expect(readLastShellRouteHash()).toBe(null);
  });

  it("treats encoded and decoded chat keys as the same hash", () => {
    expect(
      shellHashesEqual(
        "#/trading?chat=websocket:97671c95-49ba-4acf-9764-db099d76fe98",
        "#/trading?chat=websocket%3A97671c95-49ba-4acf-9764-db099d76fe98",
      ),
    ).toBe(true);
    expect(shellHashesEqual("#/trading?chat=a", "#/code?chat=a")).toBe(false);
  });

  it("persists the Career desk so a restart stays in the IDE", () => {
    rememberLastShellRoute("#/career?job=abc&chat=websocket:1");
    expect(readLastShellRouteHash()).toBe("#/career?job=abc&chat=websocket:1");
    expect(isEphemeralShellHash("#/career")).toBe(false);
  });

  it("persists the Trading desk so a restart stays in the IDE", () => {
    rememberLastShellRoute("#/trading?chat=websocket:1");
    expect(readLastShellRouteHash()).toBe("#/trading?chat=websocket:1");
    expect(isEphemeralShellHash("#/trading")).toBe(false);
  });

  it("persists the Tenders desk so a restart stays in the IDE", () => {
    rememberLastShellRoute("#/tenders?notice=abc&chat=websocket:1");
    expect(readLastShellRouteHash()).toBe("#/tenders?notice=abc&chat=websocket:1");
    expect(isEphemeralShellHash("#/tenders")).toBe(false);
  });

  it("persists the Leads desk so a restart stays in the IDE", () => {
    rememberLastShellRoute("#/leads?lead=abc&chat=websocket:1");
    expect(readLastShellRouteHash()).toBe("#/leads?lead=abc&chat=websocket:1");
    expect(isEphemeralShellHash("#/leads")).toBe(false);
  });

  it("normalizes hashes without a leading #", () => {
    expect(normalizeShellHash("code?chat=x")).toBe("#code?chat=x");
  });
});
