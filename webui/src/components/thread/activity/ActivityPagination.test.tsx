// @vitest-environment jsdom
// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ActivityJournal } from "./ActivityJournal";
import { buildJournalTimeline } from "./activityJournalModel";
import { FileEditGroup, type FileEditSummary } from "./FileEditRow";

afterEach(cleanup);

const edits: FileEditSummary[] = Array.from({ length: 790 }, (_, index) => ({
  key: `file-${index}`, path: `src/file-${index}.py`, added: 18, deleted: 0,
  approximate: false, binary: false, status: "done", pending: false,
}));

describe("large desktop activity", () => {
  it("pages every file without mounting the whole operation and preserves preview actions", () => {
    const open = vi.fn();
    const { container, rerender } = render(<FileEditGroup edits={edits} displayMode="summary" onOpenFilePreview={open} />);
    expect(container.querySelectorAll("li")).toHaveLength(20);
    expect(screen.getByText("1-20 / 790")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Previous" }) as HTMLButtonElement).disabled).toBe(true);
    for (let page = 1; page < 40; page += 1) {
      fireEvent.click(screen.getByRole("button", { name: "Next" }));
      expect(container.querySelectorAll("li").length).toBeLessThanOrEqual(20);
    }
    expect(screen.getByText("781-790 / 790")).toBeTruthy();
    expect(screen.getByRole("button", { name: "src/file-789.py", exact: true })).toBeTruthy();
    expect((screen.getByRole("button", { name: "Next" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "src/file-789.py", exact: true }));
    expect(open).toHaveBeenCalledWith("src/file-789.py");
    fireEvent.click(screen.getByRole("button", { name: "Previous" }));
    expect(screen.getByText("761-780 / 790")).toBeTruthy();
    rerender(<FileEditGroup edits={edits.slice(0, 2)} displayMode="summary" onOpenFilePreview={open} />);
    expect(container.querySelectorAll("li")).toHaveLength(2);
    expect(screen.queryByTestId("activity-pagination")).toBeNull();
  }, 15_000); // Exercise all 40 pages with the real file controls in jsdom.

  it("bounds expanded command history and follows the live tail", () => {
    const entries = buildJournalTimeline(Array.from({ length: 123 }, (_, index) => ({
      kind: "shell" as const, key: `run-${index}`, command: `check-${index}`, status: "done" as const,
    })), { streaming: true });
    render(<ActivityJournal entries={entries} streaming />);
    expect(screen.getAllByTestId("activity-journal-row")).toHaveLength(8);
    fireEvent.click(screen.getByTestId("activity-journal-open"));
    expect(screen.getByText("121-123 / 123")).toBeTruthy();
    expect(screen.getAllByTestId("activity-journal-row")).toHaveLength(3);
    fireEvent.click(screen.getByRole("button", { name: "Previous" }));
    expect(screen.getByText("101-120 / 123")).toBeTruthy();
    expect(screen.getAllByTestId("activity-journal-row")).toHaveLength(20);
    fireEvent.click(screen.getByTestId("activity-journal-open"));
    expect(screen.getAllByTestId("activity-journal-row")).toHaveLength(8);
  });
});
