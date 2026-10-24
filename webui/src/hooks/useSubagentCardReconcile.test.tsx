// @vitest-environment jsdom
import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createSubagentCardStore } from "@/components/thread/ParallelSubagentsPanel";
import { useSubagentCardReconcile } from "./useSubagentCardReconcile";

afterEach(() => vi.useRealTimers());

describe("useSubagentCardReconcile", () => {
  it("marks a running card done once the journal says it finished, and stops polling", async () => {
    vi.useFakeTimers();
    const store = createSubagentCardStore();
    store.upsert({ chatId: "c", taskId: "t1", label: "One", phase: "awaiting_model", statusLine: "Waiting", iteration: 1, done: false });
    const querySubagents = vi.fn(async () => [{ task_id: "t1", label: "One", phase: "error", done: true, error: "cancelled", status_line: "Cancelled" }]);
    renderHook(() => useSubagentCardReconcile({ querySubagents } as never, "c", store, 1000));
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(store.getSnapshot().cards[0]).toMatchObject({ done: true, phase: "error", statusLine: "Cancelled" });
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(querySubagents).toHaveBeenCalledTimes(1);
  });
});
