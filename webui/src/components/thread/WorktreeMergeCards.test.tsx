// @vitest-environment jsdom
// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import "@/i18n";
import type { InboundEvent } from "@/lib/types";
import { applyMergeEvent, worktreeMergeFromEvent } from "@/lib/worktree-merge";

const handlers = new Map<string, (ev: InboundEvent) => void>();
const fakeClient = {
  status: "open",
  onStatus: () => () => {},
  onChat: vi.fn((chatId: string, handler: (ev: InboundEvent) => void) => {
    handlers.set(chatId, handler);
    return () => handlers.delete(chatId);
  }),
  sendWorktreeMergeAction: vi.fn(),
};

vi.mock("@/providers/ClientProvider", () => ({
  useClient: () => ({ client: fakeClient, token: "tok" }),
}));

import { WorktreeMergeCards } from "./WorktreeMergeCards";

function emit(ev: Record<string, unknown>) {
  act(() => handlers.get("c1")?.({ event: "worktree_merge", chat_id: "c1", ...ev } as unknown as InboundEvent));
}

const conflict = {
  task_id: "t1",
  label: "Fix login",
  status: "conflict",
  files: ["a.py", "b.py"],
  conflicts: ["a.py"],
  reason: "conflict",
  branch: "navin/multitask/t1",
  resolve_prompt: "Please merge the changes from Fix login",
};

beforeEach(() => {
  handlers.clear();
  fakeClient.sendWorktreeMergeAction.mockClear();
});

afterEach(() => cleanup());

describe("worktree merge helpers", () => {
  it("parses a frame and keeps one card per task", () => {
    const card = worktreeMergeFromEvent({ event: "worktree_merge", chat_id: "c1", ...conflict } as InboundEvent);
    expect(card?.conflicts).toEqual(["a.py"]);
    const cards = applyMergeEvent(applyMergeEvent([], card!), card!);
    expect(cards).toHaveLength(1);
    expect(applyMergeEvent(cards, { ...card!, status: "kept" })).toEqual([]);
  });

  it("ignores other events", () => {
    expect(worktreeMergeFromEvent({ event: "turn_end" } as unknown as InboundEvent)).toBeNull();
  });
});

describe("WorktreeMergeCards", () => {
  it("shows a conflict card with the files and nothing-changed reassurance", () => {
    render(<WorktreeMergeCards chatId="c1" />);
    emit(conflict);
    const card = screen.getByTestId("worktree-merge-card");
    expect(card.textContent).toContain("Fix login");
    expect(card.textContent).toContain("1 file conflicts");
    expect(card.textContent).toContain("Nothing in your files was changed.");
    expect(screen.getByTestId("worktree-merge-files").textContent).toContain("a.py");
  });

  it("Resolve sends the action, then the agent prompt once the engine confirms", () => {
    const onResolve = vi.fn();
    render(<WorktreeMergeCards chatId="c1" onResolve={onResolve} />);
    emit(conflict);
    fireEvent.click(screen.getByTestId("worktree-merge-resolve"));
    expect(fakeClient.sendWorktreeMergeAction).toHaveBeenCalledWith("c1", "t1", "resolve");
    expect(onResolve).not.toHaveBeenCalled();
    emit({ task_id: "t1", label: "Fix login", status: "resolving", resolve_prompt: conflict.resolve_prompt });
    expect(onResolve).toHaveBeenCalledWith(conflict.resolve_prompt);
    expect(screen.queryByTestId("worktree-merge-card")).toBeNull();
  });

  it("a resolving echo from another window does not send the prompt here", () => {
    const onResolve = vi.fn();
    render(<WorktreeMergeCards chatId="c1" onResolve={onResolve} />);
    emit(conflict);
    emit({ task_id: "t1", status: "resolving", resolve_prompt: "x" });
    expect(onResolve).not.toHaveBeenCalled();
  });

  it("Keep separate and Discard send their action", () => {
    render(<WorktreeMergeCards chatId="c1" />);
    emit(conflict);
    fireEvent.click(screen.getByTestId("worktree-merge-keep"));
    expect(fakeClient.sendWorktreeMergeAction).toHaveBeenLastCalledWith("c1", "t1", "keep");
    emit({ ...conflict, task_id: "t2" });
    const discard = screen.getAllByTestId("worktree-merge-discard").at(-1)!;
    fireEvent.click(discard);
    expect(fakeClient.sendWorktreeMergeAction).toHaveBeenLastCalledWith("c1", "t2", "discard");
  });

  it("a merged outcome shows a short note with the file count", () => {
    render(<WorktreeMergeCards chatId="c1" />);
    emit({ task_id: "t3", label: "Add tests", status: "merged", files: ["x.py", "y.py", "z.py"] });
    expect(screen.getByTestId("worktree-merge-merged").textContent).toContain("3 files");
  });

  it("viewers see the card without actions", () => {
    render(<WorktreeMergeCards chatId="c1" canManage={false} />);
    emit(conflict);
    expect(screen.queryByTestId("worktree-merge-resolve")).toBeNull();
  });
});
