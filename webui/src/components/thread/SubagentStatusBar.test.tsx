// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only
// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SubagentProgressUpdate } from "@/lib/navin-client";
import { createSubagentCardStore } from "./ParallelSubagentsPanel";
import { SubagentStatusBar } from "./SubagentStatusBar";

function frame(partial: Partial<SubagentProgressUpdate> & { taskId: string }): SubagentProgressUpdate {
  return { chatId: "c", label: partial.taskId, phase: "awaiting_model", statusLine: "Working…", iteration: 1, done: false, ...partial };
}

function gAction(action: string, scope?: string): HTMLElement {
  const el = qAction(action, scope);
  if (!el) throw new Error(`no ${action} button`);
  return el;
}
function qAction(action: string, scope?: string): HTMLElement | null {
  const buttons = Array.from(document.querySelectorAll<HTMLElement>(`button[data-action="${action}"]`));
  return buttons.find((b) => !scope || b.closest(`[data-testid$="-${scope}"]`)) ?? null;
}

describe("SubagentStatusBar", () => {
  afterEach(cleanup);

  it("is hidden while there are no cards", () => {
    const store = createSubagentCardStore();
    render(<SubagentStatusBar store={store} />);
    expect(screen.queryByTestId("subagent-status-bar")).toBeNull();
    act(() => store.upsert(frame({ taskId: "a" })));
    expect(screen.getByTestId("subagent-status-bar")).toBeTruthy();
  });

  it("counts states and lists what needs attention first", () => {
    const store = createSubagentCardStore();
    store.upsert(frame({ taskId: "run" }));
    store.upsert(frame({ taskId: "ok", done: true, phase: "done" }));
    store.upsert(frame({ taskId: "wait", phase: "awaiting_approval" }));
    store.upsert(frame({ taskId: "bad", done: true, phase: "error", error: "boom" }));
    const { container } = render(<SubagentStatusBar store={store} onAction={() => undefined} />);
    const chips = Array.from(container.querySelectorAll("[data-chip]")).map((el) => el.getAttribute("data-chip"));
    expect(chips).toEqual(["running", "approval", "failed", "done"]);
    const rows = Array.from(container.querySelectorAll("[data-testid^='subagent-status-row-']")).map((el) => el.getAttribute("data-testid"));
    expect(rows).toEqual(["subagent-status-row-bad", "subagent-status-row-wait", "subagent-status-row-run"]);
    expect(screen.getByText(/Answer the request in the chat/)).toBeTruthy();
    fireEvent.click(screen.getByTestId("subagent-status-dismiss-finished"));
    expect(store.getSnapshot().cards.map((c) => c.taskId)).toEqual(["run", "wait"]);
  });

  it("opens the side panel for the rows past three", () => {
    const store = createSubagentCardStore();
    for (const id of ["a", "b", "c", "d", "e"]) store.upsert(frame({ taskId: id }));
    const onOpenPanel = vi.fn();
    render(<SubagentStatusBar store={store} onOpenPanel={onOpenPanel} />);
    const more = screen.getByTestId("subagent-status-more");
    expect(more.getAttribute("data-hidden-count")).toBe("2");
    fireEvent.click(more);
    expect(onOpenPanel).toHaveBeenCalledTimes(1);
  });

  it("runs the row actions through the shared handler", async () => {
    const store = createSubagentCardStore();
    store.upsert(frame({ taskId: "bad", label: "Audit", done: true, phase: "error", error: "boom" }));
    const onAction = vi.fn(async () => ({ ok: true }));
    render(<SubagentStatusBar store={store} onAction={onAction} />);
    await act(async () => fireEvent.click(gAction("retry", "bad")));
    expect(onAction).toHaveBeenCalledWith(expect.objectContaining({ taskId: "bad" }), "retry");
  });

  it("expands the journal card when a row label is clicked", () => {
    const store = createSubagentCardStore();
    store.upsert(frame({ taskId: "a", label: "Audit" }));
    render(<SubagentStatusBar store={store} />);
    fireEvent.click(screen.getByRole("button", { name: "Audit" }));
    expect(store.getSnapshot().expanded.has("a")).toBe(true);
  });
});
