// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createSubagentCardStore } from "@/components/thread/ParallelSubagentsPanel";
import { SubagentStatusBar } from "@/components/thread/SubagentStatusBar";
import { parseTeamEntry, type TeamFeedEntry, type TeamIdentity } from "@/lib/team";
import { meetingTurnPrompt, meetingTurnReply, nextMeetingTurn, type MeetingState } from "./TeamMeeting";
import { TeamRoom } from "./TeamRoom";

afterEach(() => cleanup());

const mark: TeamIdentity = { id: "mark", name: "Mark", title: "Senior Developer", avatar: "person", color: "#2f7de1" };
const salma: TeamIdentity = { id: "salma", name: "Salma", title: "QA Engineer", avatar: "person", color: "#1f9d74" };

describe("meeting turns", () => {
  const base: MeetingState = {
    topic: "Auth design", order: ["Mark", "Salma"], index: 0, round: 1, rounds: 1,
    turnSince: 0, transcript: [], pendingUser: [],
  };

  it("briefs each speaker with the topic and what the others said", () => {
    const prompt = meetingTurnPrompt({ ...base, transcript: [{ speaker: "Mark", text: "Use JWT" }] }, "Salma");
    expect(prompt).toContain("Topic: Auth design");
    expect(prompt).toContain("Mark: Use JWT");
    expect(prompt).toContain("You are Salma");
  });

  it("waits for the report a finished turn has not delivered yet", () => {
    const card = { chatId: "c", taskId: "t", label: "Mark", phase: "done", statusLine: "", iteration: 1, done: true, member: mark, startedAt: 10, updatedAt: 20, finishedAt: 20 };
    expect(meetingTurnReply("Mark", 10, [card], [], 1000).done).toBe(false);
    expect(meetingTurnReply("Mark", 10, [card], [], 10_000)).toEqual({ done: true, text: "(no answer)" });
  });

  it("passes the floor in order and ends after the last round", () => {
    const second = nextMeetingTurn(base, { speaker: "Mark", text: "a" }, 5)!;
    expect(second.index).toBe(1);
    expect(nextMeetingTurn(second, { speaker: "Salma", text: "b" }, 6)).toBeNull();
    const twoRounds = nextMeetingTurn({ ...second, rounds: 2 }, { speaker: "Salma", text: "b" }, 6)!;
    expect([twoRounds.index, twoRounds.round]).toEqual([0, 2]);
  });
});

describe("teammate cards", () => {
  it("show the teammate's name and title, never 'Subagent'", () => {
    const store = createSubagentCardStore();
    act(() => store.upsert({
      chatId: "c1", taskId: "t9", label: "Mark (Senior Developer)", phase: "awaiting_tools",
      statusLine: "Running uv sync", iteration: 1, done: false, model: "anthropic/claude-opus-5.5", member: mark,
    }));
    render(<SubagentStatusBar store={store} />);
    const row = screen.getByTestId("subagent-status-row-t9");
    expect(row.textContent).toContain("Mark");
    expect(row.textContent).toContain("Senior Developer");
    expect(row.textContent).toContain("claude-opus-5.5");
    expect(screen.getByTestId("subagent-status-bar").textContent).not.toMatch(/Subagent/);
  });
});

describe("TeamRoom meeting", () => {
  it("gives every teammate the floor in turn, then shows the transcript", async () => {
    const store = createSubagentCardStore();
    const onAssign = vi.fn(async () => undefined);
    const props = {
      team: { id: "dev", name: "Dev", members: [mark, salma] },
      store, canManage: true, onSend: vi.fn(), onAssign, onClose: vi.fn(),
    };
    const { rerender } = render(<TeamRoom {...props} feed={[]} />);
    fireEvent.click(screen.getByTestId("team-meeting-toggle"));
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "How do we ship auth?" } });
    fireEvent.click(screen.getByRole("button", { name: /Start meeting/ }));
    await waitFor(() => expect(onAssign).toHaveBeenCalledTimes(1));
    const calls = onAssign.mock.calls as unknown as Array<[string, string]>;
    expect(calls[0][0]).toBe("Mark");

    const answer = (taskId: string, member: TeamIdentity, text: string, feed: TeamFeedEntry[]) => {
      act(() => store.upsert({ chatId: "c1", taskId, label: member.name, phase: "done", statusLine: "", iteration: 1, done: true, member }));
      const next = [...feed, parseTeamEntry({ entry_id: `r-${taskId}`, kind: "result", sender: member, target: null, content: text, task_id: taskId, status: "ok", ts: 1 })!];
      rerender(<TeamRoom {...props} feed={next} />);
      return next;
    };
    const feed = answer("t1", mark, "Ship JWT with refresh rotation.", []);
    await waitFor(() => expect(onAssign).toHaveBeenCalledTimes(2));
    expect(calls[1][0]).toBe("Salma");
    expect(calls[1][1]).toContain("Mark: Ship JWT with refresh rotation.");

    answer("t2", salma, "I will test revocation.", feed);
    await screen.findByText("Meeting over.");
    expect(screen.getAllByTestId("meeting-turn")).toHaveLength(2);
    expect(onAssign).toHaveBeenCalledTimes(2);
  });
});
