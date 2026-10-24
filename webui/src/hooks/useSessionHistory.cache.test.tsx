// @vitest-environment jsdom
// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ fetchThread: vi.fn() }));

vi.mock("@/providers/ClientProvider", () => ({ useClient: () => ({ token: "tok" }) }));
vi.mock("@/lib/api", async (orig) => ({
  ...(await orig<typeof import("@/lib/api")>()),
  fetchWebuiThread: mocks.fetchThread,
}));

import { clearSessionHistoryCache, useSessionHistory } from "./useSessions";

function body(text: string) {
  return { messages: [{ id: text, role: "user", content: text, createdAt: 1 }] };
}

function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => { resolve = r; });
  return { promise, resolve };
}

describe("useSessionHistory cache", () => {
  beforeEach(() => {
    clearSessionHistoryCache();
    mocks.fetchThread.mockReset();
  });

  it("renders the cached transcript immediately on switch-back, then refreshes", async () => {
    mocks.fetchThread.mockImplementation(async (_t: string, key: string) => body(`${key}-v1`));
    const { result, rerender } = renderHook(({ k }) => useSessionHistory(k), {
      initialProps: { k: "A" },
    });
    await waitFor(() => expect(result.current.messages[0]?.content).toBe("A-v1"));
    rerender({ k: "B" });
    await waitFor(() => expect(result.current.messages[0]?.content).toBe("B-v1"));

    const fresh = deferred<ReturnType<typeof body>>();
    mocks.fetchThread.mockImplementation(() => fresh.promise);
    rerender({ k: "A" });
    expect(result.current.loading).toBe(false);
    expect(result.current.messages[0]?.content).toBe("A-v1");
    await act(async () => { fresh.resolve(body("A-v2")); });
    await waitFor(() => expect(result.current.messages[0]?.content).toBe("A-v2"));
  });

  it("ignores a stale response for the previous chat", async () => {
    const slowA = deferred<ReturnType<typeof body>>();
    mocks.fetchThread.mockImplementation((_t: string, key: string) =>
      key === "A" ? slowA.promise : Promise.resolve(body("B-v1")));
    const { result, rerender } = renderHook(({ k }) => useSessionHistory(k), {
      initialProps: { k: "A" },
    });
    rerender({ k: "B" });
    await waitFor(() => expect(result.current.messages[0]?.content).toBe("B-v1"));
    await act(async () => { slowA.resolve(body("A-late")); });
    expect(result.current.messages[0]?.content).toBe("B-v1");
  });
});
