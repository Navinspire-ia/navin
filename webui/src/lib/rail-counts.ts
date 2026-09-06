import type { BoardTask, BoardTaskStatus } from "@/lib/api";

/**
 * Counters shown as pills on the workbench rail (Terminal, Browser, File,
 * Rules, Tasks), next to the Git one. A pill answers "how many are open"
 * at a glance, so it stays neutral: color is reserved for a signal that
 * asks for attention (a dirty tree, a blocked task).
 */

/** A task the user still has to care about. */
const CLOSED_TASK_STATUSES: ReadonlySet<BoardTaskStatus> = new Set<BoardTaskStatus>([
  "done",
  "cancelled",
]);

export interface RailTaskCounts {
  /** Every task not done or cancelled. */
  open: number;
  inProgress: number;
  blocked: number;
}

export function countBoardTasks(
  tasks: readonly Pick<BoardTask, "status">[] | null | undefined,
): RailTaskCounts {
  const counts: RailTaskCounts = { open: 0, inProgress: 0, blocked: 0 };
  for (const task of tasks ?? []) {
    if (CLOSED_TASK_STATUSES.has(task.status)) continue;
    counts.open += 1;
    if (task.status === "in_progress") counts.inProgress += 1;
    if (task.status === "blocked") counts.blocked += 1;
  }
  return counts;
}

/** Pill text: two digits fit the pill, anything more reads as "99+". */
export function railCountText(count: number): string {
  if (!Number.isFinite(count) || count <= 0) return "0";
  return count > 99 ? "99+" : String(Math.floor(count));
}

/**
 * Fired on ``window`` by the Rules panel after it creates or saves a rule,
 * so the rail pill can refetch without polling.
 */
export const PROJECT_RULES_CHANGED_EVENT = "navin:project-rules-changed";

export function notifyProjectRulesChanged(): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(PROJECT_RULES_CHANGED_EVENT));
}
