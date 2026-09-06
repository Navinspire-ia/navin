import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  DEV_RAIL_ICON_WIDTH,
  DEV_RAIL_LABEL_WIDTH,
  persistRailDensity,
  RAIL_DENSITY_STORAGE_KEY,
  railWidthFor,
  readRailDensity,
} from "./dev-rail-layout";

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

describe("railWidthFor", () => {
  it("keeps the labeled rail at 216px and the icon rail at 48px", () => {
    expect(railWidthFor("labels")).toBe(DEV_RAIL_LABEL_WIDTH);
    expect(railWidthFor("icons")).toBe(DEV_RAIL_ICON_WIDTH);
    expect(DEV_RAIL_ICON_WIDTH).toBeLessThan(DEV_RAIL_LABEL_WIDTH);
  });
});

describe("rail density persistence", () => {
  beforeEach(() => {
    installMemoryStorage();
  });

  it("defaults to labeled names", () => {
    expect(readRailDensity()).toBe("labels");
  });

  it("remembers the icon-only choice", () => {
    persistRailDensity("icons");
    expect(localStorage.getItem(RAIL_DENSITY_STORAGE_KEY)).toBe("icons");
    expect(readRailDensity()).toBe("icons");
    persistRailDensity("labels");
    expect(readRailDensity()).toBe("labels");
  });
});
