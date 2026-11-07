// @vitest-environment jsdom
// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ChatHistoryDialog, ChatPlansDialog } from "./ChatSessionDialogs";

const mock = vi.hoisted(() => ({ prompts: vi.fn(), board: vi.fn() }));
vi.mock("@/lib/api", () => ({ fetchWebuiPrompts: mock.prompts }));
vi.mock("@/lib/board-fetch", () => ({ fetchBoardShared: mock.board }));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (key: string, values?: { done?: number; total?: number; title?: string; id?: string }) => {
  const labels: Record<string, string> = {
    "chat.history.title": "History", "chat.history.empty": "No prompts sent in this chat yet",
    "chat.history.loading": "Loading history...", "chat.history.error": "Could not load chat history.",
    "chat.history.showMore": "Show full prompt", "chat.history.showLess": "Show less", "chat.history.copy": "Copy",
    "chat.history.copied": "Copied", "chat.history.copyFailed": "Could not copy",
    "chat.history.status.done": "Done", "chat.history.status.unfinished": "Not finished", "chat.history.status.error": "Error",
    "chat.plans.title": "My plans", "chat.plans.empty": "No plan for this project yet", "chat.plans.loading": "Loading plan...",
    "chat.plans.error": "Could not load the plan.", "chat.plans.relaunch": "Relaunch",
    "chat.plans.relaunchAll": "Relaunch all unfinished", "chat.plans.status.done": "Done",
    "chat.plans.status.active": "In progress", "chat.plans.status.blocked": "Blocked", "chat.plans.status.pending": "Planned",
    "chat.plans.status.cancelled": "Stopped",
  };
  if (key === "chat.plans.progress") return `${values?.done}/${values?.total} tasks complete`;
  if (key === "chat.history.copyAria") return `Copy prompt: ${values?.title}`;
  if (key === "chat.history.showMoreAria") return `Show full prompt: ${values?.title}`;
  if (key === "chat.history.showLessAria") return `Show less of prompt: ${values?.title}`;
  if (key === "chat.plans.relaunchAria") return `Relaunch ${values?.title} (${values?.id})`;
  return labels[key] ?? key;
}, i18n: { language: "en" } }) }));

const props = { open: true, onOpenChange: vi.fn(), sessionKey: "websocket:sample", token: "tok" };

beforeEach(() => { vi.clearAllMocks(); });
afterEach(cleanup);

describe("chat session dialogs", () => {
  it("renders empty history and empty plan", async () => {
    mock.prompts.mockResolvedValue({ prompts: [] });
    mock.board.mockResolvedValue({ session_plan: null });
    const history = render(<ChatHistoryDialog {...props} />);
    expect(await screen.findByText("No prompts sent in this chat yet")).toBeTruthy();
    history.unmount();
    render(<ChatPlansDialog {...props} onRelaunch={vi.fn()} />);
    expect(await screen.findByText("No plan for this project yet")).toBeTruthy();
  });

  it("shows prompt statuses, full text and copy action", async () => {
    const text = "Long prompt ".repeat(60);
    mock.prompts.mockResolvedValue({ prompts: [
      { index: 0, text, timestamp: 1700000000000, status: "done" },
      { index: 1, text: "Stopped", timestamp: 1700000001000, status: "unfinished" },
      { index: 2, text: "Failed", timestamp: 1700000002000, status: "error" },
    ] });
    render(<ChatHistoryDialog {...props} />);
    expect(await screen.findByText("Done")).toBeTruthy();
    expect(screen.getByText("Not finished")).toBeTruthy();
    expect(screen.getByText("Error")).toBeTruthy();
    fireEvent.click(screen.getByText("Show full prompt"));
    expect(screen.getByText((_, element) => element?.tagName === "P" && element.textContent === text)).toBeTruthy();
  });

  it("names each copy button after its prompt and confirms or reports the copy", async () => {
    const writeText = vi.fn().mockResolvedValueOnce(undefined).mockRejectedValueOnce(Error("denied"));
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    mock.prompts.mockResolvedValue({ prompts: [
      { index: 0, text: "First prompt", timestamp: 1700000000000, status: "done" },
      { index: 1, text: "Second prompt", timestamp: 1700000001000, status: "done" },
    ] });
    render(<ChatHistoryDialog {...props} />);
    fireEvent.click(await screen.findByRole("button", { name: "Copy prompt: First prompt" }));
    expect(writeText).toHaveBeenCalledWith("First prompt");
    expect(await screen.findByText("Copied")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Copy prompt: Second prompt" }));
    expect(writeText).toHaveBeenLastCalledWith("Second prompt");
    expect(await screen.findByText("Could not copy")).toBeTruthy();
    expect(screen.queryByText("Copied")).toBeNull();
  });

  it("shows progress and relaunches the selected task and all unfinished tasks", async () => {
    const onRelaunch = vi.fn();
    mock.board.mockResolvedValue({ session_plan: { items: [
      { id: "t-done", title: "Done task", status: "done", done: true },
      { id: "t-active", title: "Active task", status: "in_progress", active: true, description: "Do the work" },
      { id: "t-blocked", title: "Blocked task", status: "blocked", blocked: true },
      { id: "t-planned", title: "Planned task", status: "planned" },
    ] } });
    const { rerender } = render(<ChatPlansDialog {...props} onRelaunch={onRelaunch} />);
    expect(await screen.findByText("1/4 tasks complete")).toBeTruthy();
    expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("1");
    expect(screen.getByText("Do the work")).toBeTruthy();
    expect(screen.getByText("Blocked")).toBeTruthy();
    expect(screen.getByText("In progress")).toBeTruthy();
    // A task already in progress is not offered a relaunch: the first one goes to the blocked task.
    expect(screen.queryByRole("button", { name: "Relaunch Active task (t-active)" })).toBeNull();
    fireEvent.click(screen.getAllByText("Relaunch")[0]);
    expect(onRelaunch).toHaveBeenCalledWith("Continue board task t-blocked: Blocked task");
    rerender(<ChatPlansDialog {...props} onRelaunch={onRelaunch} />);
    fireEvent.click(screen.getByText("Relaunch all unfinished"));
    expect(onRelaunch).toHaveBeenLastCalledWith(expect.stringContaining("Continue board task t-blocked: Blocked task"));
    expect(onRelaunch).toHaveBeenLastCalledWith(expect.not.stringContaining("t-active"));
  });

  it("shows tasks stopped with /stop as Stopped and offers to relaunch them under distinct names", async () => {
    const onRelaunch = vi.fn();
    mock.board.mockResolvedValue({ session_plan: { items: [
      { id: "t-stopped", title: "Stopped task", status: "cancelled", done: false, active: false, blocked: false, cancelled: true },
      { id: "t-planned", title: "Planned task", status: "planned", done: false, active: false, blocked: false },
    ] } });
    render(<ChatPlansDialog {...props} onRelaunch={onRelaunch} />);
    expect(await screen.findByText("Stopped")).toBeTruthy();
    expect(screen.getByText("Planned")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Relaunch Planned task (t-planned)" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Relaunch Stopped task (t-stopped)" }));
    expect(onRelaunch).toHaveBeenCalledWith("Continue board task t-stopped: Stopped task");
    expect(screen.getByText("Relaunch all unfinished")).toBeTruthy();
  });

  it("shows fetch failures", async () => {
    mock.prompts.mockRejectedValue(Error("offline"));
    mock.board.mockRejectedValue(Error("offline"));
    const history = render(<ChatHistoryDialog {...props} />);
    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe("Could not load chat history."));
    history.unmount();
    render(<ChatPlansDialog {...props} onRelaunch={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe("Could not load the plan."));
  });
});
