// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { NavinClient, SubagentHistory, SubagentRecord } from "@/lib/navin-client";
import { createSubagentCardStore } from "./ParallelSubagentsPanel";
import { SubagentsButton, SubagentsSidePanel, subagentState, summarizeArgs } from "./SubagentsSidePanel";

afterEach(() => { cleanup(); vi.useRealTimers(); });
const agent = (index: number): SubagentRecord => ({ task_id: `agent-${index}`, label: `Developer ${index}`, done: false, phase: "awaiting_model", status_line: "Waiting for model response", task_description: `Implement task ${index}`, model: "navin-model" });

describe("Subagent inspection", () => {
  it("keeps the header control once the chat has any agent, active or finished", () => {
    const store = createSubagentCardStore();
    const click = vi.fn();
    const { rerender } = render(<SubagentsButton store={store} open={false} onClick={click} />);
    expect(screen.queryByRole("button")).toBeNull();
    act(() => store.upsert({ chatId: "chat", taskId: "one", label: "One", phase: "queued", statusLine: "Queued", iteration: 0, done: false }));
    fireEvent.click(screen.getByRole("button", { name: /Subagents/ }));
    expect(click).toHaveBeenCalledOnce();
    expect(screen.getByRole("button").textContent).toContain("1");
    act(() => store.upsert({ chatId: "chat", taskId: "one", label: "One", phase: "done", statusLine: "Done", iteration: 1, done: true }));
    expect(screen.getByRole("button").textContent).not.toMatch(/\d/);
    act(() => store.reset());
    expect(screen.queryByRole("button")).toBeNull();
    // Reopening a chat whose agents already finished: history alone shows it.
    rerender(<SubagentsButton store={store} open={false} onClick={click} hasHistory />);
    expect(screen.getByRole("button", { name: /Subagents/ })).toBeTruthy();
  });

  it("lists more than 200 agents, opens the selected transcript and loads all action pages", async () => {
    const history = (after: number): SubagentHistory => ({ agent: agent(219), events: [{ seq: after + 1, kind: after ? "tool_done" : "tool_start", payload: after ? { name: "read_file", result: "Entire file output from agent 219" } : { name: "read_file", arguments: { path: "agent-219.py" } }, created_at: 1000 }], next_cursor: after + 1, has_more: !after });
    const querySubagents = vi.fn(async (_chat: string, operation: string, extra: { offset?: number; after?: number }) => operation === "list" ? Array.from({ length: extra.offset ? 20 : 200 }, (_, index) => agent(index + (extra.offset || 0))) : history(extra.after || 0));
    const close = vi.fn();
    render(<SubagentsSidePanel client={{ querySubagents } as unknown as NavinClient} chatId="chat-1" onClose={close} />);
    await screen.findByRole("button", { name: /Developer 219/ });
    fireEvent.change(screen.getByRole("textbox", { name: "Search agents" }), { target: { value: "Developer 219" } });
    expect(screen.queryByRole("button", { name: /Developer 218/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Developer 219/ }));
    expect((await screen.findAllByText(/agent-219.py/)).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: "Load more actions" }));
    expect((await screen.findAllByText(/Entire file output from agent 219/)).length).toBeGreaterThan(0);
    expect(document.querySelectorAll("[data-event-seq]")).toHaveLength(2);
    expect(querySubagents).toHaveBeenCalledWith("chat-1", "history", { task_id: "agent-219", after: 1, limit: 100 });
    fireEvent.click(screen.getByRole("button", { name: "Back to agents" }));
    await screen.findByRole("button", { name: /Developer 219/ });
    fireEvent.click(screen.getByRole("button", { name: "Return to main chat" }));
    expect(close).toHaveBeenCalledOnce();
  });

  it("keeps recovery unavailable to a readonly viewer and surfaces history errors", async () => {
    const querySubagents = vi.fn(async (_chat: string, operation: string) => {
      if (operation === "list") return [{ ...agent(1), recoverable: true }];
      throw new Error("History temporarily unavailable");
    });
    render(<SubagentsSidePanel client={{ querySubagents } as unknown as NavinClient} chatId="chat-1" canManage={false} onClose={() => undefined} />);
    fireEvent.click(await screen.findByRole("button", { name: /Developer 1/ }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("History temporarily unavailable"));
    expect(screen.queryByRole("button", { name: "Resume agent" })).toBeNull();
  });

  it("opens durable history after restart and reports explicit recovery success or refusal", async () => {
    const interrupted = { ...agent(1), phase: "interrupted", recoverable: true };
    let refuse = false;
    const querySubagents = vi.fn(async (_chat: string, operation: string) => {
      if (operation === "list") return [interrupted];
      if (operation === "history") return { agent: interrupted, events: [], next_cursor: 0, has_more: false };
      if (refuse) throw new Error("Cannot resume while a tool result is uncertain");
      return { detail: "Agent resumed from checkpoint" };
    });
    render(<SubagentsSidePanel client={{ querySubagents } as unknown as NavinClient} chatId="chat-1" includeCompleted onClose={() => undefined} />);
    fireEvent.click(await screen.findByRole("button", { name: /Developer 1/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Resume agent" }));
    await screen.findByText("Agent resumed from checkpoint");
    expect(querySubagents).toHaveBeenCalledWith("chat-1", "recover", { task_id: "agent-1" });
    refuse = true;
    fireEvent.click(screen.getByRole("button", { name: "Resume agent" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("Cannot resume"));
  });

  it("waits for explicit pagination before polling the live tail", async () => {
    const querySubagents = vi.fn(async (_chat: string, operation: string, extra: { after?: number }) => operation === "list" ? [agent(1)] : { agent: agent(1), events: [{ seq: (extra.after || 0) + 1, kind: "message", payload: { content: "Visible reply" }, created_at: 1000 }], next_cursor: (extra.after || 0) + 1, has_more: !extra.after });
    render(<SubagentsSidePanel client={{ querySubagents } as unknown as NavinClient} chatId="chat-1" onClose={() => undefined} />);
    const select = await screen.findByRole("button", { name: /Developer 1/ });
    vi.useFakeTimers();
    fireEvent.click(select);
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
    expect(querySubagents.mock.calls.filter((call) => call[1] === "history")).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Load more actions" }));
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(querySubagents.mock.calls.filter((call) => call[1] === "history")).toHaveLength(3);
  });
  it("renders messages as markdown and tool calls as a compact expandable list", async () => {
    const events = [{ seq: 1, kind: "message", payload: { role: "assistant", content: "**Bold** step", tool_calls: [{ id: "c1", function: { name: "read_file", arguments: JSON.stringify({ path: "src/app.ts", limit: 20, offset: 4 }) } }] }, created_at: 1000 }];
    const querySubagents = vi.fn(async (_chat: string, operation: string) => operation === "list" ? [agent(1)] : { agent: agent(1), events, next_cursor: 1, has_more: false });
    render(<SubagentsSidePanel client={{ querySubagents } as unknown as NavinClient} chatId="chat-1" onClose={() => undefined} />);
    fireEvent.click(await screen.findByRole("button", { name: /Developer 1/ }));
    expect((await screen.findByText("Bold", {}, { timeout: 8000 })).tagName).toBe("STRONG");
    expect(screen.getByText("read_file")).toBeTruthy();
    expect(screen.getByText("path: src/app.ts, limit: 20")).toBeTruthy();
    expect(document.querySelector("pre")?.closest("details")).toBeTruthy();
    expect(document.body.textContent).not.toContain('"tool_calls"');
  });

  it("derives finished states and closes live cards the journal reports as over", async () => {
    expect(subagentState({ done: false, phase: "awaiting_model" })).toBe("running");
    expect(subagentState({ done: true, phase: "done" })).toBe("completed");
    expect(subagentState({ done: true, phase: "error", error: "boom" })).toBe("failed");
    expect(subagentState({ done: true, phase: "error", error: "cancelled" })).toBe("cancelled");
    expect(subagentState({ done: false, phase: "interrupted" })).toBe("interrupted");
    expect(summarizeArgs('{"q":"x"}')).toBe("q: x");
    const store = createSubagentCardStore();
    store.upsert({ chatId: "chat-1", taskId: "agent-1", label: "Developer 1", phase: "awaiting_model", statusLine: "Waiting", iteration: 1, done: false });
    const finished = { ...agent(1), done: true, phase: "done", status_line: "Completed" };
    const querySubagents = vi.fn(async () => [finished]);
    render(<SubagentsSidePanel client={{ querySubagents } as unknown as NavinClient} chatId="chat-1" includeCompleted store={store} onClose={() => undefined} />);
    await screen.findByText("Completed", { selector: "[data-state]" });
    await waitFor(() => expect(store.getSnapshot().cards[0].done).toBe(true));
  });
});
