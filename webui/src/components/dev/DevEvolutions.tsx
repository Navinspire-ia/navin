import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  Bot,
  CalendarDays,
  Flag,
  GitBranch,
  History,
  Loader2,
  Lock,
  Plus,
  RefreshCw,
  User,
  Users,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  fetchBoard,
  updateBoard,
  type BoardActorType,
  type BoardPayload,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import { useClient } from "@/providers/ClientProvider";

const MILESTONE_STATUS_COLORS: Record<string, string> = {
  planned: "#8b5cf6",
  active: "#3b82f6",
  done: "#10b981",
};

const ACTIVITY_KIND_LABELS: Record<string, string> = {
  task_created: "created",
  task_updated: "updated",
  task_moved: "moved",
  task_claimed: "claimed",
  task_commented: "commented",
  task_deleted: "deleted",
  milestone_created: "milestone created",
  milestone_updated: "milestone updated",
  milestone_deleted: "milestone deleted",
  note: "note",
};

function ActorIcon({ type, className }: { type: BoardActorType; className?: string }) {
  if (type === "human") return <User className={className} aria-hidden />;
  if (type === "subagent") return <Users className={className} aria-hidden />;
  return <Bot className={className} aria-hidden />;
}

export function DevEvolutions({
  sessionKey,
  projectPath,
}: {
  sessionKey: string | null;
  projectPath?: string | null;
}) {
  const { client, token } = useClient();
  const { t } = useTranslation();
  const tx = useCallback(
    (key: string, fallback: string) => t(key, { defaultValue: fallback }),
    [t],
  );

  const boardKey = sessionKey ?? "websocket:webui-dev";
  const [payload, setPayload] = useState<BoardPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [actorFilter, setActorFilter] = useState<BoardActorType | "all">("all");
  const [creating, setCreating] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const [newDate, setNewDate] = useState("");
  const mutatingRef = useRef(false);

  const load = useCallback(
    async (silent = false) => {
      if (!token) return;
      if (!silent) setLoading(true);
      try {
        const data = await fetchBoard(token, boardKey);
        setPayload(data);
        setError(null);
      } catch (err) {
        if (!silent) setError(err instanceof Error ? err.message : String(err));
      } finally {
        if (!silent) setLoading(false);
      }
    },
    [token, boardKey],
  );

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const currentProject = projectPath ?? payload?.project_path ?? null;
    const unsubscribe = client.onBoardUpdate((changedProject) => {
      if (mutatingRef.current) return;
      if (changedProject && currentProject && changedProject !== currentProject) return;
      void load(true);
    });
    const interval = window.setInterval(() => void load(true), 20_000);
    return () => {
      unsubscribe();
      window.clearInterval(interval);
    };
  }, [client, load, payload, projectPath]);

  // The server computes progress including blocked counts; the local tally is
  // only a fallback for boards written by an older gateway.
  const milestoneProgress = useMemo(() => {
    const progress = new Map<string, { done: number; total: number; blocked: number }>();
    for (const milestone of payload?.milestones ?? []) {
      const server = payload?.milestone_progress?.[milestone.id];
      progress.set(
        milestone.id,
        server
          ? { done: server.done, total: server.total, blocked: server.blocked }
          : { done: 0, total: 0, blocked: 0 },
      );
    }
    if (payload?.milestone_progress) return progress;
    for (const task of payload?.tasks ?? []) {
      if (!task.milestone_id) continue;
      const entry = progress.get(task.milestone_id);
      if (!entry) continue;
      entry.total += 1;
      if (task.status === "done") entry.done += 1;
    }
    return progress;
  }, [payload]);

  const taskById = useMemo(
    () => new Map((payload?.tasks ?? []).map((task) => [task.id, task])),
    [payload],
  );

  const taskTitleById = useMemo(
    () => new Map((payload?.tasks ?? []).map((task) => [task.id, task.title])),
    [payload],
  );

  const plan = payload?.plan;
  const graphWarnings = useMemo(() => {
    if (!plan) return [];
    const warnings: string[] = [];
    for (const ring of plan.cycles) {
      warnings.push(
        `${tx("dev.evolutions.cycle", "Dependency cycle")}: ${ring
          .map((id) => taskTitleById.get(id) ?? id)
          .join(" → ")}`,
      );
    }
    for (const [id, missing] of Object.entries(plan.dangling)) {
      warnings.push(
        `${taskTitleById.get(id) ?? id} → ${tx(
          "dev.evolutions.danglingRef",
          "depends on a task that no longer exists",
        )} (${missing.join(", ")})`,
      );
    }
    return warnings;
  }, [plan, taskTitleById, tx]);

  const activity = useMemo(() => {
    const entries = payload?.activity ?? [];
    if (actorFilter === "all") return entries;
    return entries.filter((entry) => entry.actor_type === actorFilter);
  }, [payload, actorFilter]);

  const submitMilestone = useCallback(() => {
    const title = newTitle.trim();
    if (!title || !token) return;
    mutatingRef.current = true;
    void updateBoard(token, boardKey, {
      action: "create_milestone",
      milestone: { title, target_date: newDate.trim() || null },
    })
      .then((data) => {
        setPayload(data);
        setError(null);
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => {
        mutatingRef.current = false;
      });
    setNewTitle("");
    setNewDate("");
    setCreating(false);
  }, [newTitle, newDate, token, boardKey]);

  if (loading && !payload) {
    return (
      <div className="flex flex-1 items-center justify-center text-muted-foreground">
        <Loader2 className="h-5 w-5 animate-spin" aria-hidden />
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-2 border-b border-border/60 px-3 py-2">
        <History className="h-4 w-4 text-muted-foreground" aria-hidden />
        <span className="text-[13px] font-semibold">
          {tx("dev.evolutions.title", "Evolutions")}
        </span>
        <div className="flex-1" />
        <button
          type="button"
          onClick={() => void load()}
          title={tx("dev.evolutions.refresh", "Refresh")}
          className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
        >
          <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} aria-hidden />
        </button>
      </div>

      {error ? (
        <div className="flex items-center gap-2 border-b border-border/60 bg-destructive/10 px-3 py-1.5 text-[11.5px] text-destructive">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden />
          <span className="truncate">{error}</span>
        </div>
      ) : null}

      <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-4 lg:flex-row lg:items-start">
        <section className="w-full lg:w-[380px] lg:shrink-0">
          <div className="mb-2 flex items-center gap-2">
            <Flag className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />
            <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
              {tx("dev.evolutions.roadmap", "Roadmap")}
            </span>
            <div className="flex-1" />
            <button
              type="button"
              onClick={() => setCreating((value) => !value)}
              className="flex items-center gap-1 rounded-md border border-border/60 px-1.5 py-0.5 text-[11px] text-muted-foreground transition-colors hover:bg-foreground hover:text-background"
            >
              <Plus className="h-3 w-3" aria-hidden />
              {tx("dev.evolutions.addMilestone", "Milestone")}
            </button>
          </div>

          {creating ? (
            <div className="mb-2 flex flex-col gap-1.5 rounded-md border border-border/60 p-2">
              <input
                autoFocus
                value={newTitle}
                onChange={(event) => setNewTitle(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") submitMilestone();
                  if (event.key === "Escape") setCreating(false);
                }}
                placeholder={tx("dev.evolutions.milestoneTitle", "Milestone title…")}
                className="rounded-md border border-border bg-background px-2 py-1 text-[12px] outline-none focus:border-foreground/40"
              />
              <div className="flex items-center gap-1.5">
                <input
                  type="date"
                  value={newDate}
                  onChange={(event) => setNewDate(event.target.value)}
                  className="flex-1 rounded-md border border-border bg-background px-2 py-1 text-[12px] outline-none focus:border-foreground/40"
                />
                <button
                  type="button"
                  onClick={submitMilestone}
                  disabled={!newTitle.trim()}
                  className="rounded-md border border-border px-2 py-1 text-[11.5px] font-medium transition-colors hover:bg-foreground hover:text-background disabled:pointer-events-none disabled:opacity-40"
                >
                  {tx("dev.evolutions.create", "Create")}
                </button>
              </div>
            </div>
          ) : null}

          {(payload?.milestones ?? []).length === 0 ? (
            <p className="text-[11.5px] text-muted-foreground">
              {tx(
                "dev.evolutions.noMilestones",
                "No milestones yet. Create one to structure the roadmap.",
              )}
            </p>
          ) : (
            <div className="flex flex-col gap-2">
              {(payload?.milestones ?? []).map((milestone) => {
                const progress =
                  milestoneProgress.get(milestone.id) ?? { done: 0, total: 0, blocked: 0 };
                const pct = progress.total > 0
                  ? Math.round((progress.done / progress.total) * 100)
                  : 0;
                return (
                  <div
                    key={milestone.id}
                    className="rounded-lg border border-border/60 bg-background p-2.5"
                  >
                    <div className="flex items-center gap-2">
                      <span
                        className="h-2 w-2 shrink-0 rounded-full"
                        style={{
                          backgroundColor: MILESTONE_STATUS_COLORS[milestone.status],
                        }}
                      />
                      <span className="min-w-0 flex-1 truncate text-[12.5px] font-medium">
                        {milestone.title}
                      </span>
                      {milestone.target_date ? (
                        <span className="flex items-center gap-1 text-[10.5px] text-muted-foreground">
                          <CalendarDays className="h-3 w-3" aria-hidden />
                          {milestone.target_date}
                        </span>
                      ) : null}
                    </div>
                    {milestone.description ? (
                      <p className="mt-1 line-clamp-2 text-[11px] text-muted-foreground">
                        {milestone.description}
                      </p>
                    ) : null}
                    <div className="mt-2 flex items-center gap-2">
                      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
                        <div
                          className="h-full rounded-full bg-foreground/70 transition-[width]"
                          style={{ width: `${pct}%` }}
                        />
                      </div>
                      <span className="text-[10.5px] tabular-nums text-muted-foreground">
                        {progress.done}/{progress.total} · {pct}%
                      </span>
                      {progress.blocked > 0 ? (
                        <span
                          title={tx("dev.evolutions.blockedTasks", "blocked tasks")}
                          className="flex items-center gap-0.5 text-[10.5px] text-amber-600 dark:text-amber-400"
                        >
                          <Lock className="h-2.5 w-2.5" aria-hidden />
                          {progress.blocked}
                        </span>
                      ) : null}
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {plan && plan.open_count > 0 ? (
            <div className="mt-5">
              <div className="mb-2 flex items-center gap-2">
                <GitBranch className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />
                <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                  {tx("dev.evolutions.executionPlan", "Execution plan")}
                </span>
              </div>

              {graphWarnings.length > 0 ? (
                <div className="mb-2 flex flex-col gap-1 rounded-md border border-destructive/40 bg-destructive/5 p-2">
                  {graphWarnings.map((warning) => (
                    <span key={warning} className="text-[11px] text-destructive">
                      {warning}
                    </span>
                  ))}
                </div>
              ) : null}

              <p className="mb-2 text-[11px] leading-relaxed text-muted-foreground">
                {tx(
                  "dev.evolutions.planHelp",
                  "The critical path is the longest chain of remaining work: it sets the earliest possible finish, even with everything else running in parallel.",
                )}
              </p>

              {plan.critical_path.length > 1 ? (
                <ol className="mb-3 flex flex-col gap-1">
                  {plan.critical_path.map((taskId, index) => {
                    const task = taskById.get(taskId);
                    return (
                      <li
                        key={taskId}
                        className="flex items-center gap-1.5 rounded-md border border-rose-500/30 bg-rose-500/5 px-2 py-1"
                      >
                        <span className="w-4 shrink-0 text-[10px] tabular-nums text-muted-foreground">
                          {index + 1}
                        </span>
                        <span className="min-w-0 flex-1 truncate text-[11.5px]">
                          {task?.title ?? taskId}
                        </span>
                        <span className="shrink-0 text-[10px] uppercase tracking-wide text-muted-foreground">
                          {task ? tx(`dev.board.statuses.${task.status}`, task.status) : ""}
                        </span>
                      </li>
                    );
                  })}
                </ol>
              ) : null}

              <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                {tx("dev.evolutions.readyQueue", "Ready now")}
              </div>
              {plan.ready_queue.length === 0 ? (
                <p className="text-[11.5px] text-muted-foreground">
                  {tx(
                    "dev.evolutions.nothingReady",
                    "Nothing can start: every open task waits on something else or on a human.",
                  )}
                </p>
              ) : (
                <ol className="flex flex-col gap-1">
                  {plan.ready_queue.slice(0, 10).map((taskId, index) => {
                    const task = taskById.get(taskId);
                    const dependents = plan.tasks[taskId]?.dependents ?? 0;
                    return (
                      <li
                        key={taskId}
                        className={cn(
                          "flex items-center gap-1.5 rounded-md border px-2 py-1",
                          index === 0
                            ? "border-emerald-500/40 bg-emerald-500/5"
                            : "border-border/60",
                        )}
                      >
                        <span className="w-4 shrink-0 text-[10px] tabular-nums text-muted-foreground">
                          {index + 1}
                        </span>
                        <span className="min-w-0 flex-1 truncate text-[11.5px]">
                          {task?.title ?? taskId}
                        </span>
                        {dependents > 0 ? (
                          <span
                            title={tx("dev.evolutions.unblocksHelp", "tasks this one unblocks")}
                            className="shrink-0 text-[10px] text-muted-foreground"
                          >
                            +{dependents}
                          </span>
                        ) : null}
                      </li>
                    );
                  })}
                </ol>
              )}
            </div>
          ) : null}
        </section>

        <section className="min-w-0 flex-1">
          <div className="mb-2 flex items-center gap-2">
            <History className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />
            <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
              {tx("dev.evolutions.timeline", "Timeline")}
            </span>
            <div className="flex-1" />
            {(["all", "human", "agent", "subagent"] as const).map((filter) => (
              <button
                key={filter}
                type="button"
                onClick={() => setActorFilter(filter)}
                className={cn(
                  "rounded-md border px-1.5 py-0.5 text-[10.5px] transition-colors",
                  actorFilter === filter
                    ? "border-foreground/60 font-semibold text-foreground"
                    : "border-border text-muted-foreground hover:text-foreground",
                )}
              >
                {tx(`dev.evolutions.filters.${filter}`, filter)}
              </button>
            ))}
          </div>

          {activity.length === 0 ? (
            <p className="text-[11.5px] text-muted-foreground">
              {tx("dev.evolutions.noActivity", "No activity yet.")}
            </p>
          ) : (
            <ol className="relative flex flex-col gap-0 border-l border-border/60 pl-4">
              {activity.map((entry, index) => (
                <li key={`${entry.ts}-${index}`} className="relative pb-3">
                  <span className="absolute -left-[21.5px] top-1 flex h-3 w-3 items-center justify-center rounded-full border border-border bg-background">
                    <ActorIcon
                      type={entry.actor_type}
                      className="h-2 w-2 text-muted-foreground"
                    />
                  </span>
                  <div className="flex flex-wrap items-baseline gap-x-1.5 text-[11.5px]">
                    <span className="font-medium text-foreground">{entry.actor}</span>
                    <span className="text-muted-foreground">
                      {tx(
                        `dev.evolutions.kinds.${entry.kind}`,
                        ACTIVITY_KIND_LABELS[entry.kind] ?? entry.kind,
                      )}
                    </span>
                    {entry.task_id ? (
                      <span className="rounded bg-muted px-1 py-0.5 text-[10px] text-muted-foreground">
                        {taskTitleById.get(entry.task_id) ?? entry.task_id}
                      </span>
                    ) : null}
                    <span className="text-[10px] text-muted-foreground/70">{entry.ts}</span>
                  </div>
                  {entry.detail ? (
                    <p className="mt-0.5 text-[11px] text-muted-foreground">{entry.detail}</p>
                  ) : null}
                </li>
              ))}
            </ol>
          )}
        </section>
      </div>
    </div>
  );
}

export default DevEvolutions;
