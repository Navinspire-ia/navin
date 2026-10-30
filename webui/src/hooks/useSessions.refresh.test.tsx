// @vitest-environment jsdom
// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  listSessions: vi.fn(),
  listeners: new Set<() => void>(),
}));

vi.mock("@/providers/ClientProvider", () => ({
  useClient: () => ({
    token: "tok",
    client: {
      onSessionUpdate: (cb: () => void) => {
        mocks.listeners.add(cb);
        return () => mocks.listeners.delete(cb);
      },
    },
  }),
}));
vi.mock("@/lib/api", async (orig) => ({
  ...(await orig<typeof import("@/lib/api")>()),
  listSessions: mocks.listSessions,
}));

import {
  SESSION_UPDATE_DEBOUNCE_MS,
  SESSION_UPDATE_MAX_WAIT_MS,
  useSessions,
} from "./useSessions";

const row = (key: string) => ({
  key,
  channel: "websocket",
  chatId: key,
  createdAt: "2026-01-01T00:00:00Z",
  updatedAt: "2026-01-01T00:00:00Z",
  title: key,
  preview: "",
  runStartedAt: null,
  workspaceScope: null,
});

const emit = () => mocks.listeners.forEach((cb) => cb());

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("useSessions refresh under many parallel sessions", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    mocks.listeners.clear();
    mocks.listSessions.mockReset();
    mocks.listSessions.mockResolvedValue([row("websocket:a")]);
  });
  afterEach(() => vi.useRealTimers());

  it("still refreshes within the max wait while updates never pause", async () => {
    renderHook(() => useSessions());
    await flush();
    const initial = mocks.listSessions.mock.calls.length;

    // An update every 500 ms: a pure 1.2 s trailing debounce would never fire.
    for (let t = 0; t < SESSION_UPDATE_MAX_WAIT_MS + 500; t += 500) {
      act(() => emit());
      await act(async () => {
        vi.advanceTimersByTime(500);
      });
    }
    await flush();
    expect(mocks.listSessions.mock.calls.length).toBeGreaterThan(initial);
  });

  it("does not stack parallel list requests and keeps rows stable", async () => {
    const { result } = renderHook(() => useSessions());
    await flush();
    const before = result.current.sessions;
    expect(before.map((s) => s.key)).toEqual(["websocket:a"]);

    let release!: () => void;
    let inFlight = 0;
    let maxInFlight = 0;
    mocks.listSessions.mockImplementation(async () => {
      inFlight += 1;
      maxInFlight = Math.max(maxInFlight, inFlight);
      await new Promise<void>((r) => {
        release = r;
      });
      inFlight -= 1;
      return [row("websocket:a")];
    });

    act(() => {
      void result.current.refresh();
      void result.current.refresh();
      void result.current.refresh();
    });
    await flush();
    expect(maxInFlight).toBe(1);
    // Release the first request; the queued follow-up runs once, alone.
    await act(async () => release());
    await flush();
    await act(async () => release());
    await flush();
    expect(maxInFlight).toBe(1);
    expect(mocks.listSessions.mock.calls.length).toBe(1 + 2);
    // Same content: the previous array is kept, so the sidebar does not re-render.
    expect(result.current.sessions).toBe(before);
  });

  it("background refresh after an update does not flip loading", async () => {
    const { result } = renderHook(() => useSessions());
    await flush();
    expect(result.current.loading).toBe(false);
    const seen: boolean[] = [];
    act(() => emit());
    await act(async () => {
      vi.advanceTimersByTime(SESSION_UPDATE_DEBOUNCE_MS);
    });
    seen.push(result.current.loading);
    await flush();
    seen.push(result.current.loading);
    expect(seen).toEqual([false, false]);
    expect(mocks.listSessions).toHaveBeenCalledTimes(2);
  });
});
