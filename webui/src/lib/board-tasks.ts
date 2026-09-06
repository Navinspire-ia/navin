import type { BoardTask } from "@/lib/api";

/** Live work first, startable next, stuck last. */
const TASK_STATUS_ORDER: Record<string, number> = {
  in_progress: 0,
  review: 1,
  audit: 2,
  fix: 3,
  planned: 4,
  backlog: 5,
  blocked: 6,
};

/** Board tasks that still represent work to do. */
export function openBoardTasks(tasks: BoardTask[]): BoardTask[] {
  return tasks.filter(
    (task) => task.status !== "done" && task.status !== "cancelled",
  );
}

function sortedOpenTasks(tasks: BoardTask[]): BoardTask[] {
  return [...openBoardTasks(tasks)].sort(
    (a, b) =>
      (TASK_STATUS_ORDER[a.status] ?? 9) - (TASK_STATUS_ORDER[b.status] ?? 9),
  );
}

/**
 * The short list a chat card can show: open tasks ordered by how alive they
 * are, capped at `limit`, plus how many were left out.
 */
export function orderedOpenTasks(
  tasks: BoardTask[],
  limit = 6,
): { shown: BoardTask[]; rest: number } {
  return visibleOpenTasks(tasks, { expanded: false, limit });
}

/**
 * What the chat Tasks panel renders: collapsed keeps the short capped list
 * with a "+N more" remainder; expanded shows every open task (rest = 0) so
 * the user can actually read the whole backlog from the thread.
 */
export function visibleOpenTasks(
  tasks: BoardTask[],
  { expanded, limit = 6 }: { expanded: boolean; limit?: number },
): { shown: BoardTask[]; rest: number } {
  const open = sortedOpenTasks(tasks);
  if (expanded) return { shown: open, rest: 0 };
  const shown = open.slice(0, limit);
  return { shown, rest: open.length - shown.length };
}

/** An in-flight board task means a mission is (or believes it is) running. */
export function hasRunningBoardTask(tasks: BoardTask[]): boolean {
  return tasks.some((task) => task.status === "in_progress");
}
