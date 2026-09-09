// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import type { BoardTask } from "@/lib/api";
import {
  hasRunningBoardTask,
  openBoardTasks,
  orderedOpenTasks,
  visibleOpenTasks,
} from "./board-tasks";

let seq = 0;
function task(status: string, title = ""): BoardTask {
  seq += 1;
  return {
    id: `t-${seq}`,
    title: title || `Task ${seq}`,
    status,
    priority: "normal",
    done: status === "done",
    active: status === "in_progress",
    blocked: status === "blocked",
  } as unknown as BoardTask;
}

function board(): BoardTask[] {
  return [
    task("done"),
    task("backlog"),
    task("in_progress", "Audit memory & skills systems"),
    task("cancelled"),
    task("fix"),
    task("planned"),
    task("blocked"),
    task("review"),
    task("backlog"),
    task("backlog"),
  ];
}

describe("openBoardTasks", () => {
  it("drops done and cancelled tasks", () => {
    const open = openBoardTasks(board());
    expect(open).toHaveLength(8);
    expect(open.every((t) => t.status !== "done" && t.status !== "cancelled")).toBe(true);
  });
});

describe("visibleOpenTasks", () => {
  it("collapsed: caps the list and counts the remainder", () => {
    const { shown, rest } = visibleOpenTasks(board(), { expanded: false, limit: 5 });
    expect(shown).toHaveLength(5);
    expect(rest).toBe(3);
  });

  it("collapsed: live work sorts first, stuck last", () => {
    const { shown } = visibleOpenTasks(board(), { expanded: false, limit: 8 });
    expect(shown[0].status).toBe("in_progress");
    expect(shown[shown.length - 1].status).toBe("blocked");
  });

  it("expanded: shows every open task with no remainder", () => {
    const { shown, rest } = visibleOpenTasks(board(), { expanded: true, limit: 5 });
    expect(shown).toHaveLength(8);
    expect(rest).toBe(0);
  });

  it("expanded keeps the same ordering as collapsed", () => {
    const collapsed = visibleOpenTasks(board(), { expanded: false, limit: 8 }).shown;
    seq -= 10; // rebuild the identical board (ids restart)
    const expanded = visibleOpenTasks(board(), { expanded: true }).shown;
    expect(expanded.map((t) => t.status)).toEqual(collapsed.map((t) => t.status));
  });

  it("no remainder when the board fits under the cap", () => {
    const small = [task("backlog"), task("in_progress")];
    const { shown, rest } = visibleOpenTasks(small, { expanded: false });
    expect(shown).toHaveLength(2);
    expect(rest).toBe(0);
  });
});

describe("orderedOpenTasks", () => {
  it("matches the collapsed view (back-compat)", () => {
    const legacy = orderedOpenTasks(board(), 6);
    seq -= 10;
    const collapsed = visibleOpenTasks(board(), { expanded: false, limit: 6 });
    expect(legacy.shown.map((t) => t.status)).toEqual(
      collapsed.shown.map((t) => t.status),
    );
    expect(legacy.rest).toBe(collapsed.rest);
  });
});

describe("hasRunningBoardTask", () => {
  it("is true only when a task is in progress", () => {
    expect(hasRunningBoardTask(board())).toBe(true);
    expect(hasRunningBoardTask([task("backlog"), task("done")])).toBe(false);
    expect(hasRunningBoardTask([])).toBe(false);
  });
});
