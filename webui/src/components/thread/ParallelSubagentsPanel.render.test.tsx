// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only
// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createSubagentCardStore,
  LiveSubagentsPanel,
  ParallelSubagentsPanel,
  type ParallelSubagentCard,
} from "./ParallelSubagentsPanel";

const failedCard: ParallelSubagentCard = {
  chatId: "c",
  taskId: "t1",
  label: "Translate ar",
  phase: "error",
  statusLine: "Model timed out",
  errorKind: "timeout",
  error: "Error calling LLM: timed out after 300s",
  iteration: 3,
  done: true,
  model: "glm-5.3-flash",
  taskDescription: "Translate common.json to Arabic",
  tokens: 12_400,
  steps: [
    { kind: "read", verb: "Read", target: "common.json", status: "ok" },
    { kind: "write", verb: "Could not write", target: "ar.json", status: "error" },
  ],
  updatedAt: 60_000,
  startedAt: 0,
};

function gAction(action: string, scope?: string): HTMLElement {
  const el = qAction(action, scope);
  if (!el) throw new Error(`no ${action} button`);
  return el;
}
function qAction(action: string, scope?: string): HTMLElement | null {
  const buttons = Array.from(document.querySelectorAll<HTMLElement>(`button[data-action="${action}"]`));
  return buttons.find((b) => !scope || b.closest(`[data-testid$="-${scope}"]`)) ?? null;
}

describe("ParallelSubagentsPanel", () => {
  afterEach(cleanup);

  it("caps the journal at 6 subagents with show more / show less", () => {
    const cards = Array.from({ length: 8 }, (_, i) => ({
      ...failedCard,
      taskId: `r${i}`,
      label: `Worker ${i}`,
      done: false,
      phase: "awaiting_model",
      error: undefined,
      errorKind: undefined,
    })) as ParallelSubagentCard[];
    render(<ParallelSubagentsPanel cards={cards} />);
    expect(screen.queryAllByText(/^Worker \d$/)).toHaveLength(6);
    const more = screen.getByTestId("parallel-subagents-show-more");
    expect(more.textContent).toMatch(/^Show more \(/);
    expect(more.getAttribute("data-hidden-count")).toBe("2");
    fireEvent.click(more);
    expect(screen.queryAllByText(/^Worker \d$/)).toHaveLength(8);
    expect(more.textContent).toBe("Show less");
    fireEvent.click(more);
    expect(screen.queryAllByText(/^Worker \d$/)).toHaveLength(6);
  });

  it("opens a card on click to show its task, model and steps", () => {
    render(<ParallelSubagentsPanel cards={[failedCard]} />);
    const toggle = screen.getByRole("button", { expanded: false });
    expect(screen.queryByTestId("subagent-details-t1")).toBeNull();
    // The row shows a short reason, never the raw provider error.
    expect(screen.getByText("Model timed out")).toBeTruthy();
    expect(screen.queryByText(/after 300s/)).toBeNull();

    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    const details = screen.getByTestId("subagent-details-t1");
    expect(details.textContent).toContain("Translate common.json to Arabic");
    expect(details.textContent).toContain("glm-5.3-flash");
    expect(details.textContent).toContain("12k");
    expect(details.textContent).toContain("Read common.json");
    expect(details.textContent).toContain("Could not write ar.json");
    const steps = details.querySelectorAll("li[data-status]");
    expect(Array.from(steps).map((li) => li.getAttribute("data-status"))).toEqual([
      "ok",
      "error",
    ]);
  });

  it("keeps the toggle to phrasing content and points at details only when open", () => {
    render(<ParallelSubagentsPanel cards={[failedCard]} />);
    const toggle = screen.getByRole("button");
    expect(toggle.querySelector("div, p")).toBeNull();
    expect(toggle.hasAttribute("aria-controls")).toBe(false);
    fireEvent.click(toggle);
    const id = toggle.getAttribute("aria-controls");
    expect(id).toBeTruthy();
    expect(document.getElementById(id as string)).toBeTruthy();
  });

  it("draws a cancelled card neutral, not as a failure", () => {
    render(
      <ParallelSubagentsPanel
        cards={[
          {
            ...failedCard,
            taskId: "c1",
            errorKind: "cancelled",
            statusLine: "Cancelled",
            error: "Cancelled",
          },
        ]}
      />,
    );
    const row = screen.getByTestId("subagent-card-c1");
    expect(row.querySelector(".text-red-500")).toBeNull();
    expect(row.querySelector("[class*='text-red-600']")).toBeNull();
    expect(screen.getByText("Cancelled")).toBeTruthy();
  });

  it("reports the open set to a parent that owns it", () => {
    const seen: string[][] = [];
    render(
      <ParallelSubagentsPanel
        cards={[failedCard]}
        expandedIds={new Set()}
        onExpandedChange={(next) => seen.push([...next])}
      />,
    );
    fireEvent.click(screen.getByRole("button"));
    expect(seen).toEqual([["t1"]]);
  });

  it("shows live progress from the thread store in the journal", () => {
    // The thread mounts this panel as the transcript tail: live updates must
    // reach it, or the agent looks idle while subagents work in the background.
    const store = createSubagentCardStore();
    render(<LiveSubagentsPanel store={store} />);
    expect(screen.queryByTestId("subagent-card-live")).toBeNull();
    act(() => store.upsert({ chatId: "c", taskId: "live", label: "Audit", phase: "running", statusLine: "Reading files", iteration: 1, done: false }));
    expect(screen.getByTestId("subagent-card-live")).toBeTruthy();
    act(() => store.upsert({ chatId: "c", taskId: "live", label: "Audit", phase: "running", statusLine: "Writing report", iteration: 2, done: false }));
    expect(screen.getByTestId("subagent-card-live").textContent).toContain("Writing report");
  });

  it("leaves a running subagent to the status bar until it finishes or is opened", () => {
    const store = createSubagentCardStore();
    render(<LiveSubagentsPanel store={store} attentionInStatusBar />);
    act(() => store.upsert({ chatId: "c", taskId: "run", label: "Audit", phase: "running", statusLine: "Reading files", iteration: 1, done: false }));
    act(() => store.upsert({ chatId: "c", taskId: "wait", label: "Queue", phase: "queued", statusLine: "", iteration: 0, done: false }));
    expect(screen.queryByTestId("parallel-subagents-panel")).toBeNull();

    // Opened from the bar: the card and its details come into the journal.
    act(() => store.setExpanded(new Set(["run"])));
    expect(screen.getByTestId("subagent-card-run")).toBeTruthy();
    expect(screen.queryByTestId("subagent-card-wait")).toBeNull();
    expect(screen.queryByText(/Waiting for|queued, waiting/)).toBeNull();

    act(() => store.setExpanded(new Set()));
    act(() => store.upsert({ chatId: "c", taskId: "run", label: "Audit", phase: "done", statusLine: "Report written", iteration: 3, done: true }));
    expect(screen.getByTestId("subagent-card-run").textContent).toContain("Audit");
    expect(screen.queryByTestId("subagent-card-wait")).toBeNull();
    expect(screen.queryByText("Subagents finished")).toBeNull();
  });

  it("leaves a failed or stopped subagent to the status bar until it is opened", () => {
    const store = createSubagentCardStore();
    render(<LiveSubagentsPanel store={store} attentionInStatusBar />);
    act(() => store.upsert({ ...failedCard, taskId: "bad" }));
    // A stop on request arrives as an error frame of kind "cancelled".
    act(() => store.upsert({ ...failedCard, taskId: "stop", phase: "error", errorKind: "cancelled", error: "Cancelled" }));
    expect(screen.queryByTestId("subagent-card-bad")).toBeNull();
    expect(screen.queryByTestId("subagent-card-stop")).toBeNull();

    // Opened from the bar: the failure and its details read in the journal.
    act(() => store.setExpanded(new Set(["bad"])));
    const card = screen.getByTestId("subagent-card-bad");
    expect(card.textContent).toContain("Translate ar");
    expect(card.textContent).toContain("Model timed out");
    expect(screen.getByTestId("subagent-details-bad").textContent).toContain("Translate common.json to Arabic");
    expect(screen.queryByTestId("subagent-card-stop")).toBeNull();
  });

  it("offers Relaunch and Dismiss on a failed card, as siblings of the toggle", async () => {
    const onAction = vi.fn(async () => ({ ok: true }));
    render(<ParallelSubagentsPanel cards={[failedCard]} onAction={onAction} />);
    const retry = gAction("retry", "t1");
    const dismiss = gAction("dismiss", "t1");
    expect(retry.getAttribute("aria-label")).toMatch(/^Relaunch/);
    expect(retry.closest("button")?.parentElement?.closest("button")).toBeNull();
    expect(document.querySelector("button button")).toBeNull();
    await act(async () => fireEvent.click(retry));
    expect(onAction).toHaveBeenCalledWith(failedCard, "retry");
    await act(async () => fireEvent.click(dismiss));
    expect(onAction).toHaveBeenLastCalledWith(failedCard, "dismiss");
  });

  it("offers Cancel while running and Dismiss once done ok", () => {
    const running = { ...failedCard, taskId: "r", label: "Run", done: false, phase: "awaiting_model", error: undefined, errorKind: undefined };
    const ok = { ...failedCard, taskId: "o", label: "Ok", phase: "done", error: undefined, errorKind: undefined };
    render(<ParallelSubagentsPanel cards={[running, ok]} onAction={() => undefined} />);
    expect(gAction("cancel", "r")).toBeTruthy();
    expect(gAction("dismiss", "o")).toBeTruthy();
    expect(qAction("retry", "o")).toBeNull();
  });

  it("hides Relaunch and Cancel when the user cannot manage", () => {
    render(<ParallelSubagentsPanel cards={[failedCard]} onAction={() => undefined} canManage={false} />);
    expect(qAction("retry", "t1")).toBeNull();
    expect(gAction("dismiss", "t1")).toBeTruthy();
  });

  it("shows the returned error under the row", async () => {
    render(<ParallelSubagentsPanel cards={[failedCard]} onAction={async () => ({ ok: false, error: "Cannot relaunch: agent is still active." })} />);
    await act(async () => fireEvent.click(gAction("retry", "t1")));
    expect(screen.getByRole("alert").textContent).toBe("Cannot relaunch: agent is still active.");
  });

  it("names a validation stop instead of a generic error", () => {
    const unvalidated = {
      ...failedCard,
      taskId: "v",
      statusLine: "Changes not validated",
      errorKind: "unvalidated",
      error: "## Validation pending\n\nChanges are saved. The task is not validated yet.",
    };
    render(<ParallelSubagentsPanel cards={[unvalidated]} />);
    const row = screen.getByTestId("subagent-card-v");
    expect(row.textContent).toContain("Changes not validated");
    expect(row.textContent).not.toContain("Stopped on an error");
    expect(row.textContent).not.toContain("Validation pending");
  });

  it("draws an agent waiting for approval in amber", () => {
    const waiting = { ...failedCard, taskId: "w", done: false, phase: "awaiting_approval", statusLine: "Waiting for your approval", error: undefined, errorKind: undefined };
    render(<ParallelSubagentsPanel cards={[waiting]} onAction={() => undefined} />);
    const row = screen.getByTestId("subagent-card-w");
    expect(row.querySelector(".text-amber-500")).toBeTruthy();
    expect(row.textContent).toContain("Waiting for your approval");
    expect(gAction("cancel", "w")).toBeTruthy();
  });
});
