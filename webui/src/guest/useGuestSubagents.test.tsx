// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { NavinClient, SubagentProgressUpdate, SubagentRecord } from "@/lib/navin-client";
import { SUBAGENTS_SIDEBAR_QUERY, useGuestSubagents } from "./useGuestSubagents";

const record = (id: string, done = true): SubagentRecord => ({ task_id: id, label: `Agent ${id}`, done, phase: done ? "done" : "awaiting_model" });
const progress = (taskId: string, done = false): SubagentProgressUpdate => ({ chatId: "shared", taskId, label: `Agent ${taskId}`, phase: done ? "done" : "awaiting_model", statusLine: "Working", iteration: 1, done });

function screenWidth(px: number) {
  vi.stubGlobal("matchMedia", (query: string) => {
    const min = Number(/min-width:\s*(\d+)px/.exec(query)?.[1] ?? 0);
    return { matches: px >= min, media: query, addEventListener: () => undefined, removeEventListener: () => undefined };
  });
}

function fakeClient(list: (chatId: string) => Promise<SubagentRecord[]>) {
  const handlers = new Set<(update: SubagentProgressUpdate) => void>();
  const client = {
    querySubagents: vi.fn((chatId: string) => list(chatId)),
    onSubagentProgress: (handler: (update: SubagentProgressUpdate) => void) => {
      handlers.add(handler);
      return () => handlers.delete(handler);
    },
  };
  const emit = (update: SubagentProgressUpdate) => act(() => { for (const handler of handlers) handler(update); });
  return { client: client as unknown as Pick<NavinClient, "querySubagents" | "onSubagentProgress">, query: client.querySubagents, emit };
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("guest subagents", () => {
  it("opens the right sidebar on a wide screen when the shared chat already has agents", async () => {
    screenWidth(1280);
    const { client } = fakeClient(async () => [record("done-1")]);
    const { result } = renderHook(() => useGuestSubagents(client, "shared"));
    await waitFor(() => expect(result.current.open).toBe(true));
    expect(result.current.exists).toBe(true);
    expect(result.current.hasHistory).toBe(true);
    // Only finished agents: the list shows them instead of an empty filter.
    expect(result.current.includeCompleted).toBe(true);
    // Closed by the guest, it stays closed when more agents show up.
    act(() => result.current.close());
    expect(result.current.open).toBe(false);
  });

  it("opens on the first live agent with running ones first, and stays closed once the guest closed it", async () => {
    screenWidth(1280);
    const { client, emit } = fakeClient(async () => []);
    const { result } = renderHook(() => useGuestSubagents(client, "shared"));
    await waitFor(() => expect(result.current.hasHistory).toBe(false));
    expect(result.current.open).toBe(false);
    emit(progress("live-1"));
    expect(result.current.open).toBe(true);
    expect(result.current.includeCompleted).toBe(false);
    act(() => result.current.close());
    emit(progress("live-2"));
    expect(result.current.open).toBe(false);
    // Frames of another chat are not this share's agents.
    emit({ ...progress("other"), chatId: "elsewhere" });
    expect(result.current.store.getSnapshot().cards.map((card) => card.taskId)).toEqual(["live-1", "live-2"]);
  });

  it("keeps the header button only on a phone and closes the full-screen panel when an agent is opened", async () => {
    screenWidth(390);
    expect(window.matchMedia(SUBAGENTS_SIDEBAR_QUERY).matches).toBe(false);
    const { client } = fakeClient(async () => [record("a")]);
    const { result } = renderHook(() => useGuestSubagents(client, "shared"));
    await waitFor(() => expect(result.current.exists).toBe(true));
    expect(result.current.open).toBe(false);
    act(() => result.current.toggle());
    expect(result.current.open).toBe(true);
    act(() => result.current.select(record("a")));
    expect(result.current.viewing?.task_id).toBe("a");
    expect(result.current.open).toBe(false);
    act(() => result.current.back());
    expect(result.current.viewing).toBeNull();
  });

  it("keeps the panel beside the reading on a wide screen and resets on another chat", async () => {
    screenWidth(1280);
    const { client } = fakeClient(async (chatId) => (chatId === "shared" ? [record("a")] : []));
    const { result, rerender } = renderHook(({ chat }) => useGuestSubagents(client, chat), { initialProps: { chat: "shared" } });
    await waitFor(() => expect(result.current.open).toBe(true));
    act(() => result.current.select(record("a")));
    expect(result.current.open).toBe(true);
    rerender({ chat: "next" });
    await waitFor(() => expect(result.current.hasHistory).toBe(false));
    expect(result.current.viewing).toBeNull();
    expect(result.current.open).toBe(false);
  });

  it("retries the journal while the relay is still connecting", async () => {
    screenWidth(1280);
    vi.useFakeTimers();
    let calls = 0;
    const { client, query } = fakeClient(async () => {
      calls += 1;
      if (calls < 3) throw new Error("The agent connection is offline.");
      return [record("late")];
    });
    const { result } = renderHook(() => useGuestSubagents(client, "shared"));
    await act(async () => { await vi.advanceTimersByTimeAsync(4100); });
    expect(query).toHaveBeenCalledTimes(3);
    expect(result.current.hasHistory).toBe(true);
    expect(result.current.open).toBe(true);
  });
});
