// The checkpoint divider in the chat is a passive note without this: the
// restore point it announces lives in Code > Git > Checkpoints, three
// navigation steps away. The CTA dispatches one window event; App switches
// to the Code module and DevWorkbench opens the Git panel on the
// Checkpoints tab. The pending flag covers the mount race: when the
// workbench is not mounted yet, it consumes the request on mount instead
// of missing the event.

export const OPEN_CHECKPOINTS_EVENT = "navin:open-checkpoints";

let pendingOpenCheckpoints = false;

export function requestOpenCheckpoints(): void {
  pendingOpenCheckpoints = true;
  // globalThis === window in the browser; the guard keeps SSR/tests happy.
  if (typeof globalThis.dispatchEvent === "function" && typeof CustomEvent !== "undefined") {
    globalThis.dispatchEvent(new CustomEvent(OPEN_CHECKPOINTS_EVENT));
  }
}

export function consumePendingOpenCheckpoints(): boolean {
  const pending = pendingOpenCheckpoints;
  pendingOpenCheckpoints = false;
  return pending;
}

// "View plan" used to open a Sheet beside the chat. The CTA now brings the
// Code workbench to the session-plan tab instead, so the markdown lives in
// the editor column. Same pending-flag race as checkpoints.

export const OPEN_SESSION_PLAN_EVENT = "navin:open-session-plan";

let pendingOpenSessionPlan = false;

export function requestOpenSessionPlan(): void {
  pendingOpenSessionPlan = true;
  if (typeof globalThis.dispatchEvent === "function" && typeof CustomEvent !== "undefined") {
    globalThis.dispatchEvent(new CustomEvent(OPEN_SESSION_PLAN_EVENT));
  }
}

export function consumePendingOpenSessionPlan(): boolean {
  const pending = pendingOpenSessionPlan;
  pendingOpenSessionPlan = false;
  return pending;
}

// A task title in the activity journal ("Created task Open Preview in OS
// browser") is a pointer into the board. Clicking it brings the Code
// workbench to the Tasks panel and selects that card. Two pending slots:
// the workbench consumes "open the board", the (lazy) board panel consumes
// "which task", because the panel mounts only once the board mode is on.

export const OPEN_BOARD_TASK_EVENT = "navin:open-board-task";

export interface BoardTaskFocusRequest {
  id?: string;
  title?: string;
}

let pendingOpenBoard = false;
let pendingBoardTaskFocus: BoardTaskFocusRequest | null = null;

export function requestOpenBoardTask(request: BoardTaskFocusRequest = {}): void {
  pendingOpenBoard = true;
  const id = request.id?.trim();
  const title = request.title?.trim();
  pendingBoardTaskFocus = id || title
    ? { ...(id ? { id } : {}), ...(title ? { title } : {}) }
    : null;
  if (typeof globalThis.dispatchEvent === "function" && typeof CustomEvent !== "undefined") {
    globalThis.dispatchEvent(
      new CustomEvent<BoardTaskFocusRequest | null>(OPEN_BOARD_TASK_EVENT, {
        detail: pendingBoardTaskFocus,
      }),
    );
  }
}

export function consumePendingOpenBoard(): boolean {
  const pending = pendingOpenBoard;
  pendingOpenBoard = false;
  return pending;
}

export function consumePendingBoardTaskFocus(): BoardTaskFocusRequest | null {
  const pending = pendingBoardTaskFocus;
  pendingBoardTaskFocus = null;
  return pending;
}

function normalizeTaskTitle(value: string): string {
  return value.replace(/\s+/g, " ").replace(/[…]+$/, "").trim().toLowerCase();
}

/**
 * Find the board card a journal row points at. The id wins; otherwise the
 * title, which the journal may have clipped with an ellipsis, so a prefix
 * match is accepted when no exact title exists.
 */
export function matchBoardTask<T extends { id: string; title: string }>(
  tasks: readonly T[],
  request: BoardTaskFocusRequest | null | undefined,
): T | undefined {
  if (!request) return undefined;
  if (request.id) {
    const byId = tasks.find((task) => task.id === request.id);
    if (byId) return byId;
  }
  const wanted = request.title ? normalizeTaskTitle(request.title) : "";
  if (!wanted) return undefined;
  const exact = tasks.find((task) => normalizeTaskTitle(task.title) === wanted);
  if (exact) return exact;
  return tasks.find((task) => normalizeTaskTitle(task.title).startsWith(wanted));
}

// The composer review bar can be hidden or collapsed; Changes in the Code
// rail brings it back expanded so every pending file is visible.

export const SHOW_PENDING_REVIEW_EVENT = "navin:show-pending-review";

export function requestShowPendingReview(): void {
  if (typeof globalThis.dispatchEvent === "function" && typeof CustomEvent !== "undefined") {
    globalThis.dispatchEvent(new CustomEvent(SHOW_PENDING_REVIEW_EVENT));
  }
}
