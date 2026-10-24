// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createSubagentCardStore } from "@/components/thread/ParallelSubagentsPanel";
import { mergeTeamFeed, parseTeamEntry, type TeamIdentity } from "@/lib/team";
import { TeamRoom } from "./TeamRoom";

afterEach(() => cleanup());

const mark: TeamIdentity = { id: "mark", name: "Mark", title: "Senior Developer", avatar: "visor", color: "#2f7de1" };
const salma: TeamIdentity = { id: "salma", name: "Salma", title: "QA Engineer", avatar: "round", color: "#1f9d74" };

function frame(id: string, kind: string, sender: TeamIdentity, target: TeamIdentity | null, content: string, extra: Record<string, unknown> = {}) {
  return { event: "team_message", chat_id: "c1", entry_id: id, kind, sender, target, content, ts: 1_800_000_000 + Number(id.slice(1)), ...extra };
}

describe("team feed", () => {
  it("parses frames and merges them once, in time order", () => {
    const a = parseTeamEntry(frame("e2", "message", salma, mark, "2 tests fail", { status: "challenge" }))!;
    const b = parseTeamEntry(frame("e1", "assign", { id: "lead", name: "Navin", avatar: "navin" }, mark, "Build auth"))!;
    expect(a.status).toBe("challenge");
    expect(parseTeamEntry({ entry_id: "x" })).toBeNull();
    const merged = mergeTeamFeed(mergeTeamFeed([], [a]), [b, a]);
    expect(merged.map((entry) => entry.entryId)).toEqual(["e1", "e2"]);
  });
});

describe("TeamRoom", () => {
  it("shows lanes with the real model, the journal, and lets the user interrupt or assign", async () => {
    const store = createSubagentCardStore();
    const onSend = vi.fn(async () => undefined);
    const onAssign = vi.fn(async () => undefined);
    const feed = [
      parseTeamEntry(frame("e1", "assign", { id: "lead", name: "Navin", avatar: "navin" }, mark, "Build the auth module"))!,
      parseTeamEntry(frame("e2", "message", salma, mark, "Refresh token still valid after logout", { status: "challenge" }))!,
      parseTeamEntry(frame("e3", "email", mark, { id: "email", name: "Aymen" }, "Auth shipped", { status: "sent" }))!,
    ];
    render(
      <TeamRoom
        team={{ id: "dev", name: "Dev", members: [mark, salma] }}
        feed={feed}
        store={store}
        canManage
        onSend={onSend}
        onAssign={onAssign}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByTestId("team-room").textContent).toMatch(/Team/);
    expect(screen.getByText("Refresh token still valid after logout")).toBeTruthy();
    expect(screen.getByText("challenge")).toBeTruthy();
    expect(screen.getByText("email sent")).toBeTruthy();

    act(() => store.upsert({
      chatId: "c1", taskId: "t1", label: "Mark (Senior Developer)", phase: "awaiting_tools",
      statusLine: "Editing session.ts", iteration: 2, done: false, model: "anthropic/claude-sonnet-5.5", member: mark,
    }));
    expect(screen.getByTestId("team-lane").textContent).toContain("claude-sonnet-5.5");

    // Mark is running: the composer interrupts him.
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "Use argon2, not bcrypt" } });
    fireEvent.click(screen.getByRole("button", { name: /Send/ }));
    await waitFor(() => expect(onSend).toHaveBeenCalledWith("Mark", "Use argon2, not bcrypt"));

    // Salma is idle: the same composer gives her a task.
    fireEvent.click(screen.getByRole("button", { name: "Salma", pressed: false }));
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "Write the revocation test" } });
    fireEvent.click(screen.getByRole("button", { name: /Assign/ }));
    await waitFor(() => expect(onAssign).toHaveBeenCalledWith("Salma", "Write the revocation test"));
  });

  it("shows a failed request instead of swallowing it", async () => {
    const store = createSubagentCardStore();
    render(
      <TeamRoom
        team={{ id: "dev", name: "Dev", members: [mark] }}
        feed={[]}
        store={store}
        canManage
        onSend={vi.fn()}
        onAssign={vi.fn(async () => { throw new Error("Collaboration Mode is off"); })}
        onClose={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "Do it" } });
    fireEvent.click(screen.getByRole("button", { name: /Assign/ }));
    expect((await screen.findByRole("alert")).textContent).toContain("Collaboration Mode is off");
  });
});
