// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

// The checkpoint CTA relies on two behaviours: the event reaches a mounted
// workbench immediately, and a workbench that mounts later (App had to switch
// to the Code module first) still finds the pending request exactly once.

import { describe, expect, it } from "vitest";

import {
  OPEN_BOARD_TASK_EVENT,
  OPEN_CHECKPOINTS_EVENT,
  OPEN_SESSION_PLAN_EVENT,
  SHOW_PENDING_REVIEW_EVENT,
  consumePendingBoardTaskFocus,
  consumePendingOpenBoard,
  consumePendingOpenCheckpoints,
  consumePendingOpenSessionPlan,
  matchBoardTask,
  requestOpenBoardTask,
  requestOpenCheckpoints,
  requestOpenSessionPlan,
  requestShowPendingReview,
} from "./workbench-events";

describe("workbench events", () => {
  it("dispatches the open-checkpoints event for mounted listeners", () => {
    // The node test environment has no window; stand in for it with a bare
    // EventTarget wired to the same dispatch entry point the module uses.
    const target = new EventTarget();
    const hadDispatch = "dispatchEvent" in globalThis;
    const originalDispatch = (globalThis as Record<string, unknown>).dispatchEvent;
    (globalThis as Record<string, unknown>).dispatchEvent =
      target.dispatchEvent.bind(target);
    let received = 0;
    target.addEventListener(OPEN_CHECKPOINTS_EVENT, () => {
      received += 1;
    });
    try {
      requestOpenCheckpoints();
    } finally {
      if (hadDispatch) {
        (globalThis as Record<string, unknown>).dispatchEvent = originalDispatch;
      } else {
        delete (globalThis as Record<string, unknown>).dispatchEvent;
      }
    }
    expect(received).toBe(1);
    // Drain the pending flag so other tests start clean.
    consumePendingOpenCheckpoints();
  });

  it("keeps a pending request for a workbench that mounts later, once", () => {
    requestOpenCheckpoints();
    expect(consumePendingOpenCheckpoints()).toBe(true);
    expect(consumePendingOpenCheckpoints()).toBe(false);
  });

  it("reports nothing pending when nobody asked", () => {
    expect(consumePendingOpenCheckpoints()).toBe(false);
  });
});

describe("session plan workbench events", () => {
  it("dispatches the open-session-plan event for mounted listeners", () => {
    const target = new EventTarget();
    const hadDispatch = "dispatchEvent" in globalThis;
    const originalDispatch = (globalThis as Record<string, unknown>).dispatchEvent;
    (globalThis as Record<string, unknown>).dispatchEvent =
      target.dispatchEvent.bind(target);
    let received = 0;
    target.addEventListener(OPEN_SESSION_PLAN_EVENT, () => {
      received += 1;
    });
    try {
      requestOpenSessionPlan();
    } finally {
      if (hadDispatch) {
        (globalThis as Record<string, unknown>).dispatchEvent = originalDispatch;
      } else {
        delete (globalThis as Record<string, unknown>).dispatchEvent;
      }
    }
    expect(received).toBe(1);
    consumePendingOpenSessionPlan();
  });

  it("keeps a pending request for a workbench that mounts later, once", () => {
    requestOpenSessionPlan();
    expect(consumePendingOpenSessionPlan()).toBe(true);
    expect(consumePendingOpenSessionPlan()).toBe(false);
  });
});

describe("board task workbench events", () => {
  it("dispatches open-board-task with the task reference as detail", () => {
    const target = new EventTarget();
    const hadDispatch = "dispatchEvent" in globalThis;
    const originalDispatch = (globalThis as Record<string, unknown>).dispatchEvent;
    (globalThis as Record<string, unknown>).dispatchEvent =
      target.dispatchEvent.bind(target);
    const received: unknown[] = [];
    target.addEventListener(OPEN_BOARD_TASK_EVENT, (event) => {
      received.push((event as CustomEvent).detail);
    });
    try {
      requestOpenBoardTask({ id: " t-1 ", title: "Open Preview" });
    } finally {
      if (hadDispatch) {
        (globalThis as Record<string, unknown>).dispatchEvent = originalDispatch;
      } else {
        delete (globalThis as Record<string, unknown>).dispatchEvent;
      }
    }
    expect(received).toEqual([{ id: "t-1", title: "Open Preview" }]);
    consumePendingOpenBoard();
    consumePendingBoardTaskFocus();
  });

  it("keeps two pending slots: one for the workbench mode, one for the lazy panel", () => {
    requestOpenBoardTask({ title: "Ship it" });
    expect(consumePendingOpenBoard()).toBe(true);
    expect(consumePendingOpenBoard()).toBe(false);
    expect(consumePendingBoardTaskFocus()).toEqual({ title: "Ship it" });
    expect(consumePendingBoardTaskFocus()).toBeNull();
  });

  it("matches the card by id first, then exact title, then the clipped title", () => {
    const tasks = [
      { id: "a", title: "Open Preview in the OS browser" },
      { id: "b", title: "Fix the flaky test" },
    ];
    expect(matchBoardTask(tasks, { id: "b", title: "Open Preview in the OS browser" })?.id).toBe("b");
    expect(matchBoardTask(tasks, { title: "fix the   flaky test" })?.id).toBe("b");
    expect(matchBoardTask(tasks, { title: "Open Preview in the…" })?.id).toBe("a");
    expect(matchBoardTask(tasks, { title: "Nothing like this" })).toBeUndefined();
    expect(matchBoardTask(tasks, null)).toBeUndefined();
  });
});

describe("pending review workbench events", () => {
  it("dispatches show-pending-review so a hidden composer bar can return", () => {
    const target = new EventTarget();
    const hadDispatch = "dispatchEvent" in globalThis;
    const originalDispatch = (globalThis as Record<string, unknown>).dispatchEvent;
    (globalThis as Record<string, unknown>).dispatchEvent =
      target.dispatchEvent.bind(target);
    let received = 0;
    target.addEventListener(SHOW_PENDING_REVIEW_EVENT, () => {
      received += 1;
    });
    try {
      requestShowPendingReview();
    } finally {
      if (hadDispatch) {
        (globalThis as Record<string, unknown>).dispatchEvent = originalDispatch;
      } else {
        delete (globalThis as Record<string, unknown>).dispatchEvent;
      }
    }
    expect(received).toBe(1);
  });
});
