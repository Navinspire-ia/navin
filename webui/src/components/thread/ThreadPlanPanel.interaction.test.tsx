// @vitest-environment jsdom
// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SessionPlan } from "@/lib/api";
import { ThreadPlanPanel } from "./ThreadPlanPanel";

const mock = vi.hoisted(() => ({
  fetch: vi.fn(), update: vi.fn(), openPlan: vi.fn(), onUpdate: vi.fn(),
  t: (key: string, options?: Record<string, unknown> & { defaultValue?: string }) =>
    (options?.defaultValue ?? key).replace(/\{\{(\w+)\}\}/g, (_match, name: string) => String(options?.[name] ?? "")),
}));
vi.mock("@/lib/api", () => ({ fetchBoard: mock.fetch, updateBoard: mock.update }));
vi.mock("@/lib/workbench-events", () => ({ requestOpenSessionPlan: mock.openPlan }));
vi.mock("@/components/MarkdownText", () => ({ preloadMarkdownText: vi.fn() }));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: mock.t }) }));
const client = { onBoardUpdate: mock.onUpdate };
vi.mock("@/providers/ClientProvider", () => ({ useClient: () => ({ client, token: "test" }) }));

const plan: SessionPlan = {
  current_id: "step-1", title: "Desktop cleanup", goal: "Keep the eight useful modules",
  done_count: 1, total_count: 8, complete: false, version: 66, ledger_status: "running",
  last_change: { version: 66, reason: "manual edit" },
  items: Array.from({ length: 8 }, (_, i) => ({
    id: `step-${i}`, title: `Task ${i}`, priority: "high", status: i === 0 ? "done" : "in_progress",
    done: i === 0, active: i === 1, blocked: false,
  })),
};

beforeEach(() => {
  vi.clearAllMocks();
  mock.fetch.mockResolvedValue({ session_plan: plan });
  mock.onUpdate.mockReturnValue(() => {});
});
afterEach(cleanup);

describe("on-demand session plan", () => {
  it("stays compact across streaming updates and only opens at the user's request", async () => {
    const { rerender } = render(<ThreadPlanPanel sessionKey="chat" isStreaming />);
    await screen.findByTestId("thread-plan-toggle");
    // Nothing blocked nor handed over: the done count alone.
    expect(screen.getByTestId("thread-plan-counts").textContent).toBe("1/8 done");
    expect(screen.queryByTestId("thread-plan-details")).toBeNull();
    fireEvent.click(screen.getByTestId("thread-plan-toggle"));
    expect(await screen.findByRole("dialog")).toBeTruthy();
    expect(screen.getAllByTestId("thread-plan-row")).toHaveLength(8);
    expect(screen.queryByText(/manual edit|v66/)).toBeNull();
    fireEvent.click(screen.getByTestId("thread-plan-hide"));
    rerender(<ThreadPlanPanel sessionKey="chat" isStreaming={false} />);
    await act(async () => { mock.onUpdate.mock.calls.at(-1)?.[0](); });
    expect(screen.queryByTestId("thread-plan-details")).toBeNull();
    fireEvent.click(screen.getByTestId("thread-plan-toggle"));
    fireEvent.keyDown(window, { key: "Escape", keyCode: 27, which: 27 });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("keeps Stop and the full plan action wired", async () => {
    const stop = vi.fn();
    render(<ThreadPlanPanel sessionKey="chat" isStreaming onStop={stop} />);
    fireEvent.click(await screen.findByTestId("thread-plan-toggle"));
    fireEvent.click(screen.getByTestId("thread-plan-stop"));
    expect(stop).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByTestId("thread-plan-view"));
    expect(mock.openPlan).toHaveBeenCalledOnce();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("holds a plan with gaps and starts it only after explicit acknowledgement", async () => {
    const build = vi.fn();
    const incomplete = { ...plan, quality: { status: "gaps", gaps: [{ kind: "acceptance", message: "Add acceptance criteria" }] } };
    mock.fetch.mockResolvedValue({ session_plan: incomplete });
    render(<ThreadPlanPanel sessionKey="chat" onBuild={build} />);
    fireEvent.click(await screen.findByTestId("thread-plan-toggle"));
    expect((screen.getByTestId("thread-plan-build") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(await screen.findByTestId("thread-plan-build-anyway"));
    expect(build).toHaveBeenCalledWith(incomplete);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("reports a failed pause and allows a successful retry", async () => {
    mock.update.mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce({});
    render(<ThreadPlanPanel sessionKey="chat" />);
    fireEvent.click(await screen.findByTestId("thread-plan-toggle"));
    fireEvent.click(screen.getByTestId("thread-plan-pause-mission"));
    expect(await screen.findByText("The plan could not be updated. Please try again.")).toBeTruthy();
    mock.fetch.mockResolvedValue({ session_plan: { ...plan, ledger_status: "paused" } });
    fireEvent.click(screen.getByTestId("thread-plan-pause-mission"));
    expect(await screen.findByTestId("thread-plan-resume-mission")).toBeTruthy();
    expect(mock.update).toHaveBeenLastCalledWith("test", "chat", { action: "pause_mission", reason: "human" });
    expect(screen.queryByText("The plan could not be updated. Please try again.")).toBeNull();
  });

  it("names the done, blocked and to-validate steps next to Plan", async () => {
    mock.fetch.mockResolvedValue({ session_plan: { ...plan, blocked_count: 2, review_count: 3 } });
    render(<ThreadPlanPanel sessionKey="chat" />);
    expect((await screen.findByTestId("thread-plan-counts")).textContent).toBe("1/8 done · 2 blocked · 3 to validate");
    expect(screen.getByTestId("thread-plan-blocked-count").className).toContain("text-amber");
  });

  it("hides the plan from the chat without touching its tasks", async () => {
    mock.update.mockResolvedValue({ session_plan: { ...plan, hidden: true } });
    render(<ThreadPlanPanel sessionKey="chat" isStreaming />);
    // One click on the widget, without opening the plan.
    fireEvent.click(await screen.findByTestId("thread-plan-dismiss-quick"));
    await waitFor(() => expect(screen.queryByTestId("thread-plan-panel")).toBeNull());
    // Only the view changes: no task is moved or cancelled, the run goes on.
    expect(mock.update).toHaveBeenCalledOnce();
    expect(mock.update).toHaveBeenCalledWith("test", "chat", { action: "hide_plan", plan_id: "step-1" });
    // A later refresh of the hidden plan keeps it out of the chat.
    mock.fetch.mockResolvedValue({ session_plan: { ...plan, hidden: true } });
    await act(async () => { mock.onUpdate.mock.calls.at(-1)?.[0](); });
    expect(screen.queryByTestId("thread-plan-panel")).toBeNull();
    // Shown again from the chat menu: the opened plan hides it the same way.
    mock.fetch.mockResolvedValue({ session_plan: plan });
    await act(async () => { mock.onUpdate.mock.calls.at(-1)?.[0](); });
    fireEvent.click(await screen.findByTestId("thread-plan-toggle"));
    fireEvent.click(screen.getByTestId("thread-plan-dismiss"));
    await waitFor(() => expect(screen.queryByTestId("thread-plan-panel")).toBeNull());
    expect(mock.update).toHaveBeenCalledTimes(2);
  });
});
