// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only
// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

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
});
