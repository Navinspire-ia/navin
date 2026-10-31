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

describe("useSessionHistory loadOlderUntil", () => {
  beforeEach(() => {
    clearSessionHistoryCache();
    mocks.fetchThread.mockReset();
  });

  /** Page N holds prompt N of a 4-prompt chat; the latest page is prompt 3. */
  function paged(failBefore?: string) {
    return async (_t: string, _key: string, options?: { before?: string | null }) => {
      if (failBefore && options?.before === failBefore) throw new Error("offline");
      const at = options?.before ? Number(options.before) - 1 : 3;
      return {
        messages: [{ id: `u${at}`, role: "user", content: `prompt ${at}`, createdAt: at }],
        page: {
          before_cursor: at > 0 ? String(at) : null,
          has_more_before: at > 0,
          user_message_offset: at,
        },
      };
    };
  }

  it("loads older pages until the prompt is in, in order", async () => {
    mocks.fetchThread.mockImplementation(paged());
    const { result } = renderHook(() => useSessionHistory("A"));
    await waitFor(() => expect(result.current.messages).toHaveLength(1));
    expect(result.current.userMessageOffset).toBe(3);
    let reached = false;
    await act(async () => { reached = await result.current.loadOlderUntil(1); });
    expect(reached).toBe(true);
    expect(result.current.messages.map((m) => m.content)).toEqual(["prompt 1", "prompt 2", "prompt 3"]);
    expect(result.current.userMessageOffset).toBe(1);
    expect(result.current.hasMoreBefore).toBe(true);
    expect(result.current.loadingOlder).toBe(false);
    // Already loaded: no request.
    const calls = mocks.fetchThread.mock.calls.length;
    await act(async () => { reached = await result.current.loadOlderUntil(2); });
    expect(reached).toBe(true);
    expect(mocks.fetchThread.mock.calls.length).toBe(calls);
  });

  it("reports a failed load and keeps what was on screen", async () => {
    mocks.fetchThread.mockImplementation(paged("2"));
    const { result } = renderHook(() => useSessionHistory("A"));
    await waitFor(() => expect(result.current.messages).toHaveLength(1));
    let reached = true;
    await act(async () => { reached = await result.current.loadOlderUntil(0); });
    expect(reached).toBe(false);
    expect(result.current.messages.map((m) => m.content)).toEqual(["prompt 3"]);
    expect(result.current.error).toBe("offline");
    expect(result.current.loadingOlder).toBe(false);
  });
});
