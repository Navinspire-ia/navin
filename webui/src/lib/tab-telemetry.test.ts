import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  isTabTelemetryEnabled,
  readTabLatencySamples,
  recordTabLatency,
  setTabTelemetryEnabled,
  tabLatencyPercentiles,
} from "@/lib/tab-telemetry";

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

describe("tab-telemetry (Phase 0 local)", () => {
  beforeEach(() => {
    installMemoryStorage();
  });

  it("records samples by default and computes percentiles", () => {
    expect(isTabTelemetryEnabled()).toBe(true);
    for (const ms of [100, 200, 300, 400, 500]) {
      recordTabLatency({ latencyMs: ms, route: "code" });
    }
    const samples = readTabLatencySamples();
    expect(samples).toHaveLength(5);
    const pct = tabLatencyPercentiles(samples);
    expect(pct?.count).toBe(5);
    expect(pct?.p50).toBe(300);
    expect(pct?.p90).toBe(500);
  });

  it("respects kill-switch", () => {
    setTabTelemetryEnabled(false);
    expect(isTabTelemetryEnabled()).toBe(false);
    recordTabLatency({ latencyMs: 120 });
    expect(readTabLatencySamples()).toHaveLength(0);
  });
});
