// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { motion } from "framer-motion";
import {
  AlertTriangle,
  AlignLeft,
  Bot,
  CircleDot,
  ExternalLink,
  Flag,
  GitBranch,
  GitPullRequest,
  Github,
  Loader2,
  Lock,
  MessageSquare,
  Plus,
  RefreshCw,
  Repeat,
  Send,
  Trash2,
  User,
  Users,
  Wrench,
  X,
  Zap,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  fetchBoard,
  fetchGithubPrSync,
  updateBoard,
  updateBoardAutonomy,
  type BoardActorType,
  type BoardAutonomy,
  type BoardPayload,
  type BoardTask,
  type BoardTaskStatus,
  type BoardUpdateOp,
} from "@/lib/api";
import type { GithubPrSyncSuggestion } from "@/lib/types";
import { cn } from "@/lib/utils";
import {
  OPEN_BOARD_TASK_EVENT,
  consumePendingBoardTaskFocus,
  matchBoardTask,
  type BoardTaskFocusRequest,
} from "@/lib/workbench-events";
import { useClient } from "@/providers/ClientProvider";

const STATUS_ORDER: BoardTaskStatus[] = [
  "backlog",
  "planned",
  "in_progress",
  "review",
  "audit",
  "fix",
  "blocked",
  "cancelled",
  "done",
];

const STATUS_COLORS: Record<BoardTaskStatus, string> = {
  backlog: "#9ca3af",
  planned: "#8b5cf6",
  in_progress: "#3b82f6",
  review: "#f59e0b",
  audit: "#06b6d4",
  fix: "#ef4444",
  blocked: "#f43f5e",
  cancelled: "#6b7280",
  done: "#10b981",
};

const PRIORITY_COLORS: Record<string, string> = {
  low: "#9ca3af",
  medium: "#3b82f6",
  high: "#f59e0b",
  critical: "#ef4444",
};

function ActorIcon({ type, className }: { type: BoardActorType; className?: string }) {
  if (type === "human") return <User className={className} aria-hidden />;
  if (type === "subagent") return <Users className={className} aria-hidden />;
  return <Bot className={className} aria-hidden />;
}

/** Short display name for a task branch: navin/task-t-abc123-fix-login -> fix-login. */
function shortBranch(branch: string): string {
  const match = /^navin\/task-[^-]+-[^-]+-(.+)$/.exec(branch);
  return match ? match[1] : branch.replace(/^navin\//, "");
}

function ConsentOption({
  checked,
  onChange,
  title,
  detail,
  icon: Icon,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  title: string;
  detail: string;
  icon: typeof Zap;
}) {
  return (
    <label
      className={cn(
        "flex cursor-pointer items-start gap-2.5 rounded-lg border p-2.5 transition-colors",
        checked
          ? "border-foreground/30 bg-muted/40"
          : "border-border/60 hover:bg-muted/20",
      )}
    >
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="mt-0.5 h-3.5 w-3.5 accent-foreground"
      />
      <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
      <span className="min-w-0">
        <span className="block text-[12.5px] font-medium leading-snug">{title}</span>
        <span className="mt-0.5 block text-[11.5px] leading-snug text-muted-foreground">
          {detail}
        </span>
      </span>
    </label>
  );
}

export function DevBoardPanel({
  sessionKey,
  projectPath,
  onRunAction,
}: {
  sessionKey: string | null;
  projectPath?: string | null;
  onRunAction?: (text: string) => void;
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
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [creatingIn, setCreatingIn] = useState<BoardTaskStatus | null>(null);
  const [newTitle, setNewTitle] = useState("");
  const [commentText, setCommentText] = useState("");
  const [titleDraft, setTitleDraft] = useState("");
  const [descriptionDraft, setDescriptionDraft] = useState("");
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [consentOpen, setConsentOpen] = useState(false);
  const [consentBranch, setConsentBranch] = useState(true);
  const [consentPr, setConsentPr] = useState(true);
  const [consentIssues, setConsentIssues] = useState(false);
  const [consentFixIssues, setConsentFixIssues] = useState(false);
  const [consentAutopilot, setConsentAutopilot] = useState(false);
  const [savingAutonomy, setSavingAutonomy] = useState(false);
  const [syncingGithub, setSyncingGithub] = useState(false);
  const [prSuggestions, setPrSuggestions] = useState<GithubPrSyncSuggestion[]>(
    [],
  );
  const [checkingPrSync, setCheckingPrSync] = useState(false);
  const mutatingRef = useRef(false);
  const draftTaskRef = useRef<string | null>(null);
  const draggingRef = useRef<string | null>(null);
  const [draggingId, setDraggingId] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState<BoardTaskStatus | null>(null);

  const loadPrSync = useCallback(
    async (silent = false) => {
      if (!token) return;
      if (!silent) setCheckingPrSync(true);
      try {
        const sync = await fetchGithubPrSync(token, boardKey);
        setPrSuggestions(
          Array.isArray(sync.suggestions) ? sync.suggestions : [],
        );
      } catch {
        if (!silent) setPrSuggestions([]);
      } finally {
        if (!silent) setCheckingPrSync(false);
      }
    },
    [token, boardKey],
  );

  const load = useCallback(
    async (silent = false) => {
      if (!token) return;
      if (!silent) setLoading(true);
      try {
        const data = await fetchBoard(token, boardKey);
        setPayload(data);
        setError(null);
        void loadPrSync(true);
      } catch (err) {
        if (!silent) setError(err instanceof Error ? err.message : String(err));
      } finally {
        if (!silent) setLoading(false);
      }
    },
    [token, boardKey, loadPrSync],
  );

  useEffect(() => {
    void load();
  }, [load]);

  // Real-time: agents mutate the board through their tool; the gateway
  // broadcasts board_updated. Polling stays as a safety net.
  useEffect(() => {
    const currentProject = projectPath ?? payload?.project_path ?? null;
    const unsubscribe = client.onBoardUpdate((changedProject) => {
      if (mutatingRef.current) return;
      if (changedProject && currentProject && changedProject !== currentProject) return;
      void load(true);
    });
    const interval = window.setInterval(() => void load(true), 15_000);
    return () => {
      unsubscribe();
      window.clearInterval(interval);
    };
  }, [client, load, payload, projectPath]);

  // A journal row asked for one task: the request may predate this mount
  // (panel is lazy), so drain the pending slot first, then keep listening.
  const [focusRequest, setFocusRequest] = useState<BoardTaskFocusRequest | null>(
    () => consumePendingBoardTaskFocus(),
  );
  useEffect(() => {
    const onOpenTask = (event: Event) => {
      consumePendingBoardTaskFocus();
      const detail = (event as CustomEvent<BoardTaskFocusRequest | null>).detail;
      setFocusRequest(detail ? { ...detail } : null);
    };
    window.addEventListener(OPEN_BOARD_TASK_EVENT, onOpenTask);
    return () => window.removeEventListener(OPEN_BOARD_TASK_EVENT, onOpenTask);
  }, []);
  useEffect(() => {
    if (!focusRequest || !payload) return;
    const task = matchBoardTask(payload.tasks, focusRequest);
    setFocusRequest(null);
    if (!task) return;
    setSelectedId(task.id);
    window.requestAnimationFrame(() => {
      document
        .querySelector(`[data-board-task-id="${task.id}"]`)
        ?.scrollIntoView({ block: "center", behavior: "smooth" });
    });
  }, [focusRequest, payload]);

  const mutate = useCallback(
    async (op: BoardUpdateOp) => {
      if (!token) return false;
      mutatingRef.current = true;
      try {
        const data = await updateBoard(token, boardKey, op);
        setPayload(data);
        setError(null);
        return true;
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
        return false;
      } finally {
        mutatingRef.current = false;
      }
    },
    [token, boardKey],
  );

  const moveTask = useCallback(
    (taskId: string, status: BoardTaskStatus) => {
      const current = payload?.tasks.find((task) => task.id === taskId);
      if (!current || current.status === status) return;
      setPayload((prev) =>
        prev
          ? {
              ...prev,
              tasks: prev.tasks.map((task) =>
                task.id === taskId ? { ...task, status } : task,
              ),
            }
          : prev,
      );
      void mutate({
        action: "update_task",
        task_id: taskId,
        fields: { status },
      }).then((ok) => {
        if (!ok) void load(true);
      });
    },
    [payload, mutate, load],
  );

  const endDrag = useCallback(() => {
    draggingRef.current = null;
    setDraggingId(null);
    setDragOver(null);
  }, []);

  const autonomy: BoardAutonomy | null = payload?.autonomy ?? null;

  const saveAutonomy = useCallback(
    async (fields: Parameters<typeof updateBoardAutonomy>[2]) => {
      if (!token) return false;
      setSavingAutonomy(true);
      mutatingRef.current = true;
      try {
        await updateBoardAutonomy(token, boardKey, fields);
        setError(null);
        return true;
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
        return false;
      } finally {
        mutatingRef.current = false;
        setSavingAutonomy(false);
        void load(true);
      }
    },
    [token, boardKey, load],
  );

  const openConsent = useCallback(() => {
    // Seed the dialog from the stored settings so re-enabling keeps choices.
    setConsentBranch(autonomy?.auto_branch ?? false);
    setConsentPr(autonomy?.open_pr_on_done ?? true);
    setConsentIssues(autonomy?.sync_github_issues ?? false);
    setConsentFixIssues(autonomy?.fix_issues ?? false);
    setConsentAutopilot(autonomy?.autopilot_loop ?? false);
    setConsentOpen(true);
  }, [autonomy]);

  const confirmConsent = useCallback(() => {
    setConsentOpen(false);
    const wasLooping = autonomy?.autopilot_loop ?? false;
    void saveAutonomy({
      enabled: true,
      auto_branch: consentBranch,
      open_pr_on_done: consentPr,
      sync_github_issues: consentIssues,
      fix_issues: consentFixIssues,
      autopilot_loop: consentAutopilot,
    }).then((ok) => {
      if (!ok || !onRunAction) return;
      // The loop itself is a session-bound cron the agent manages: ask it to
      // create or remove the job so the setting has an immediate effect.
      if (consentAutopilot && !wasLooping) {
        onRunAction("/board loop");
      } else if (!consentAutopilot && wasLooping) {
        onRunAction(
          tx(
            "dev.board.autonomy.stopLoopPrompt",
            "Stop the board autopilot: remove the session-bound board loop cron job you created for this project, then confirm.",
          ),
        );
      }
    });
  }, [
    saveAutonomy,
    consentBranch,
    consentPr,
    consentIssues,
    consentFixIssues,
    consentAutopilot,
    autonomy,
    onRunAction,
    tx,
  ]);

  const toggleAutonomy = useCallback(() => {
    if (autonomy?.enabled) {
      const wasLooping = autonomy.autopilot_loop;
      void saveAutonomy({ enabled: false, autopilot_loop: false }).then((ok) => {
        if (!ok || !onRunAction || !wasLooping) return;
        onRunAction(
          tx(
            "dev.board.autonomy.stopLoopPrompt",
            "Stop the board autopilot: remove the session-bound board loop cron job you created for this project, then confirm.",
          ),
        );
      });
    } else {
      openConsent();
    }
  }, [autonomy, saveAutonomy, openConsent, onRunAction, tx]);

  const syncGithub = useCallback(async () => {
    if (!token || syncingGithub) return;
    setSyncingGithub(true);
    mutatingRef.current = true;
    try {
      const data = await updateBoard(token, boardKey, { action: "sync_github" });
      setPayload(data);
      setError(null);
      void loadPrSync(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      mutatingRef.current = false;
      setSyncingGithub(false);
    }
  }, [token, boardKey, syncingGithub, loadPrSync]);

  const markTaskDoneFromPr = useCallback(
    (taskId: string) => {
      void mutate({
        action: "update_task",
        task_id: taskId,
        fields: { status: "done" },
      }).then(() => {
        setPrSuggestions((prev) => prev.filter((row) => row.task_id !== taskId));
        void loadPrSync(true);
      });
    },
    [mutate, loadPrSync],
  );

  const tasksByStatus = useMemo(() => {
    const map = new Map<BoardTaskStatus, BoardTask[]>();
    for (const status of STATUS_ORDER) map.set(status, []);
    for (const task of payload?.tasks ?? []) {
      map.get(task.status)?.push(task);
    }
    return map;
  }, [payload]);

  const milestoneById = useMemo(
    () => new Map((payload?.milestones ?? []).map((m) => [m.id, m])),
    [payload],
  );

  const taskById = useMemo(
    () => new Map((payload?.tasks ?? []).map((task) => [task.id, task])),
    [payload],
  );

  const plan = payload?.plan;
  const readyRank = useMemo(() => {
    const map = new Map<string, number>();
    (plan?.ready_queue ?? []).forEach((id, index) => map.set(id, index));
    return map;
  }, [plan]);

  const taskLabel = useCallback(
    (taskId: string) => taskById.get(taskId)?.title ?? taskId,
    [taskById],
  );

  const selected = useMemo(
    () => (payload?.tasks ?? []).find((task) => task.id === selectedId) ?? null,
    [payload, selectedId],
  );

  const statusLabel = useCallback(
    (status: BoardTaskStatus) => tx(`dev.board.statuses.${status}`, status.replace("_", " ")),
    [tx],
  );

  const submitCreate = useCallback(() => {
    const title = newTitle.trim();
    if (!title || !creatingIn) return;
    void mutate({ action: "create_task", task: { title, status: creatingIn } });
    setNewTitle("");
    setCreatingIn(null);
  }, [newTitle, creatingIn, mutate]);

  const submitComment = useCallback(() => {
    const text = commentText.trim();
    if (!text || !selected) return;
    void mutate({ action: "comment_task", task_id: selected.id, text });
    setCommentText("");
  }, [commentText, selected, mutate]);

  // Drafts are seeded once per task so background refreshes never wipe typing.
  useEffect(() => {
    if (!selected) {
      draftTaskRef.current = null;
      return;
    }
    if (draftTaskRef.current === selected.id) return;
    draftTaskRef.current = selected.id;
    setTitleDraft(selected.title);
    setDescriptionDraft(selected.description ?? "");
  }, [selected]);

  const commitTitle = useCallback(() => {
    if (!selected) return;
    const title = titleDraft.trim();
    if (!title || title === selected.title) {
      setTitleDraft(selected.title);
      return;
    }
    void mutate({ action: "update_task", task_id: selected.id, fields: { title } });
  }, [selected, titleDraft, mutate]);

  const commitDescription = useCallback(() => {
    if (!selected) return;
    const description = descriptionDraft.trim();
    if (description === (selected.description ?? "").trim()) return;
    void mutate({ action: "update_task", task_id: selected.id, fields: { description } });
  }, [selected, descriptionDraft, mutate]);

  const deleteTask = useCallback(
    (taskId: string) => {
      void mutate({ action: "delete_task", task_id: taskId });
      setConfirmDeleteId(null);
      setSelectedId((current) => (current === taskId ? null : current));
    },
    [mutate],
  );

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
        <Flag className="h-4 w-4 text-muted-foreground" aria-hidden />
        <span className="text-[13px] font-semibold">
          {tx("dev.board.title", "Task board")}
        </span>
        <span className="text-[11.5px] text-muted-foreground">
          {payload
            ? tx("dev.board.count", "{{count}} tasks").replace(
                "{{count}}",
                String(payload.tasks.length),
              )
            : null}
        </span>
        <div className="flex-1" />
        <button
          type="button"
          onClick={toggleAutonomy}
          disabled={savingAutonomy}
          title={
            autonomy?.enabled
              ? tx(
                  "dev.board.autonomy.disableHint",
                  "Autonomy is on: the agent chains ready tasks, isolates each one on a navin/task branch and opens PRs. Click to turn off.",
                )
              : tx(
                  "dev.board.autonomy.enableHint",
                  "Let the agent chain ready tasks, auto-branch per task and open PRs. Click to review and enable.",
                )
          }
          className={cn(
            "flex items-center gap-1.5 rounded-md border px-2 py-1 text-[11.5px] font-medium transition-colors",
            autonomy?.enabled
              ? "border-emerald-500/40 bg-emerald-500/12 text-emerald-600 dark:text-emerald-400"
              : "border-border/60 text-muted-foreground hover:bg-muted/60 hover:text-foreground",
          )}
        >
          {savingAutonomy ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
          ) : autonomy?.enabled ? (
            <motion.span
              initial={{ scale: 0.6, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              transition={{ type: "spring", stiffness: 400, damping: 18 }}
              className="flex"
            >
              <Zap className="h-3.5 w-3.5" aria-hidden />
            </motion.span>
          ) : (
            <Zap className="h-3.5 w-3.5" aria-hidden />
          )}
          {autonomy?.enabled
            ? tx("dev.board.autonomy.on", "Autonomy ON")
            : tx("dev.board.autonomy.off", "Autonomy")}
        </button>
        <button
          type="button"
          onClick={() => void syncGithub()}
          disabled={syncingGithub}
          title={tx(
            "dev.board.syncGithubHint",
            "Import the repository's open GitHub issues as board tasks (deduplicated by issue URL).",
          )}
          className="flex items-center gap-1.5 rounded-md border border-border/60 px-2 py-1 text-[11.5px] font-medium text-muted-foreground transition-colors hover:bg-foreground hover:text-background disabled:pointer-events-none disabled:opacity-50"
        >
          {syncingGithub ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
          ) : (
            <Github className="h-3.5 w-3.5" aria-hidden />
          )}
          {tx("dev.board.syncGithub", "Sync GitHub")}
        </button>
        <button
          type="button"
          onClick={() => void loadPrSync()}
          disabled={checkingPrSync}
          title={tx(
            "dev.board.prSyncHint",
            "Check whether task PRs merged on GitHub. Suggestions only - tasks are not auto-closed.",
          )}
          className="flex items-center gap-1.5 rounded-md border border-border/60 px-2 py-1 text-[11.5px] font-medium text-muted-foreground transition-colors hover:bg-foreground hover:text-background disabled:pointer-events-none disabled:opacity-50"
        >
          {checkingPrSync ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
          ) : (
            <GitPullRequest className="h-3.5 w-3.5" aria-hidden />
          )}
          {tx("dev.board.prSync", "Check merged PRs")}
          {prSuggestions.length > 0 ? (
            <span className="rounded-full bg-emerald-500/20 px-1.5 text-[10px] font-semibold text-emerald-700 dark:text-emerald-300">
              {prSuggestions.length}
            </span>
          ) : null}
        </button>
        {onRunAction ? (
          <button
            type="button"
            onClick={() => onRunAction("/board")}
            className="flex items-center gap-1.5 rounded-md border border-border/60 px-2 py-1 text-[11.5px] font-medium text-muted-foreground transition-colors hover:bg-foreground hover:text-background"
          >
            <Bot className="h-3.5 w-3.5" aria-hidden />
            {tx("dev.board.runAgent", "Run agent on board")}
          </button>
        ) : null}
        <button
          type="button"
          onClick={() => void load()}
          title={tx("dev.board.refresh", "Refresh")}
          className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
        >
          <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} aria-hidden />
        </button>
      </div>

      <AlertDialog open={consentOpen} onOpenChange={setConsentOpen}>
        <AlertDialogContent className="max-w-md">
          <AlertDialogHeader>
            <AlertDialogTitle className="flex items-center gap-2">
              <Zap className="h-4 w-4 text-amber-500" aria-hidden />
              {tx("dev.board.autonomy.dialogTitle", "Enable board autonomy?")}
            </AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="space-y-2 text-left">
                <p>
                  {tx(
                    "dev.board.autonomy.dialogIntro",
                    "During a run you start (Run agent, /forge, /cruise, /mission or a board loop), the agent will chain through ready tasks without asking again for each card. Your consent is recorded in .navin/board/settings.json.",
                  )}
                </p>
                <ul className="list-disc space-y-0.5 pl-4 text-[12px]">
                  <li>
                    {tx(
                      "dev.board.autonomy.canTasks",
                      "Create, update and move tasks itself (to do, doing, blocked, done) with evidence.",
                    )}
                  </li>
                  <li>
                    {tx(
                      "dev.board.autonomy.canChain",
                      "Pick the next ready task and keep working until the queue is empty or a task blocks.",
                    )}
                  </li>
                  <li>
                    {tx(
                      "dev.board.autonomy.canNotify",
                      "Notify you on claim, blocked, opened PR and imported issues.",
                    )}
                  </li>
                </ul>
                <p className="text-[12px]">
                  {tx(
                    "dev.board.autonomy.dialogSafety",
                    "It never merges pull requests and still asks before destructive git operations (force-push, hard reset, deletions). You can turn this off at any time.",
                  )}
                </p>
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="flex flex-col gap-2">
            <ConsentOption
              checked={consentBranch}
              onChange={setConsentBranch}
              icon={GitBranch}
              title={tx("dev.board.autonomy.optBranch", "Isolated branch per task")}
              detail={tx(
                "dev.board.autonomy.optBranchDetail",
                "Claiming a task creates and switches to navin/task-<id>, so your current branch is never touched. Off by default: the agent commits where you already are.",
              )}
            />
            <ConsentOption
              checked={consentPr}
              onChange={setConsentPr}
              icon={GitPullRequest}
              title={tx("dev.board.autonomy.optPr", "Pull request on done")}
              detail={tx(
                "dev.board.autonomy.optPrDetail",
                "When a task reaches done, its branch is pushed and a PR is opened for your review (requires gh).",
              )}
            />
            <ConsentOption
              checked={consentIssues}
              onChange={setConsentIssues}
              icon={Github}
              title={tx("dev.board.autonomy.optIssues", "GitHub issues sync")}
              detail={tx(
                "dev.board.autonomy.optIssuesDetail",
                "The agent may create issues mirroring tasks and close them when the task is done.",
              )}
            />
            <ConsentOption
              checked={consentFixIssues}
              onChange={setConsentFixIssues}
              icon={Wrench}
              title={tx("dev.board.autonomy.optFix", "Fix issues autonomously")}
              detail={tx(
                "dev.board.autonomy.optFixDetail",
                "The agent may fix a GitHub issue end to end: reproduce, fix on an isolated branch, run the tests until green, commit, then close the issue and re-sync the board.",
              )}
            />
            <ConsentOption
              checked={consentAutopilot}
              onChange={setConsentAutopilot}
              icon={Repeat}
              title={tx("dev.board.autonomy.optLoop", "Autopilot loop")}
              detail={tx(
                "dev.board.autonomy.optLoopDetail",
                "A session-bound cron job processes the board continuously, one ready task per cycle, even without a chat open. Off: the agent only works during runs you start.",
              )}
            />
          </div>
          {autonomy?.global &&
          (!autonomy.global.auto_branch_enabled || !autonomy.global.open_pr_enabled) ? (
            <p className="rounded-md border border-amber-500/30 bg-amber-500/10 px-2.5 py-1.5 text-[11.5px] text-amber-700 dark:text-amber-300">
              {tx(
                "dev.board.autonomy.globalOff",
                "A global kill-switch (config tools.boardGit) currently disables part of this on this machine.",
              )}
            </p>
          ) : null}
          <AlertDialogFooter>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setConsentOpen(false)}
            >
              {tx("dev.board.cancel", "Cancel")}
            </Button>
            <Button type="button" size="sm" className="gap-1.5" onClick={confirmConsent}>
              <Zap className="h-3.5 w-3.5" aria-hidden />
              {tx("dev.board.autonomy.confirm", "Enable autonomy")}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {error ? (
        <div className="flex items-center gap-2 border-b border-border/60 bg-destructive/10 px-3 py-1.5 text-[11.5px] text-destructive">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden />
          <span className="truncate">{error}</span>
        </div>
      ) : null}

      {prSuggestions.length > 0 ? (
        <div className="space-y-1.5 border-b border-emerald-500/25 bg-emerald-500/10 px-3 py-2">
          <p className="text-[11px] font-semibold text-emerald-800 dark:text-emerald-200">
            {tx(
              "dev.board.prSyncTitle",
              "Merged PRs - mark matching board tasks done?",
            )}
          </p>
          {prSuggestions.map((row) => {
            const task = taskById.get(row.task_id);
            return (
              <div
                key={`${row.task_id}:${row.pr_url}`}
                className="flex flex-wrap items-center gap-2 text-[11.5px]"
              >
                <span className="min-w-0 flex-1 truncate font-medium text-foreground">
                  {task?.title || row.task_id}
                </span>
                <a
                  href={row.pr_url}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex max-w-[12rem] items-center gap-1 truncate text-emerald-700 underline-offset-2 hover:underline dark:text-emerald-300"
                  title={row.pr_url}
                >
                  <ExternalLink className="h-3 w-3 shrink-0" aria-hidden />
                  PR
                </a>
                <button
                  type="button"
                  onClick={() => markTaskDoneFromPr(row.task_id)}
                  className="rounded-md border border-emerald-600/40 bg-background/70 px-2 py-0.5 font-medium text-emerald-800 transition-colors hover:bg-emerald-500/20 dark:text-emerald-200"
                >
                  {tx("dev.board.prSyncMarkDone", "Mark done")}
                </button>
              </div>
            );
          })}
        </div>
      ) : null}

      {plan && plan.open_count > 0 ? (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-border/60 px-3 py-1.5 text-[11px]">
          <span className="flex items-center gap-1 text-emerald-600 dark:text-emerald-400">
            <span className="font-semibold">{plan.ready_queue.length}</span>
            {tx("dev.board.planReady", "ready")}
          </span>
          <span className="flex items-center gap-1 text-amber-600 dark:text-amber-400">
            <Lock className="h-3 w-3" aria-hidden />
            <span className="font-semibold">{plan.blocked.length}</span>
            {tx("dev.board.planBlocked", "blocked")}
          </span>
          {plan.critical_path.length > 1 ? (
            <span
              title={plan.critical_path.map(taskLabel).join(" → ")}
              className="flex items-center gap-1 text-rose-600 dark:text-rose-400"
            >
              <GitBranch className="h-3 w-3" aria-hidden />
              {tx("dev.board.planCriticalPath", "critical path")}
              <span className="font-semibold">{plan.critical_path.length}</span>
            </span>
          ) : null}
          <span className="text-muted-foreground">
            {plan.done_count}/{plan.done_count + plan.open_count}{" "}
            {tx("dev.board.planDone", "done")}
          </span>
          {plan.cycles.length > 0 || Object.keys(plan.dangling).length > 0 ? (
            <span
              title={[
                ...plan.cycles.map((ring) => ring.map(taskLabel).join(" → ")),
                ...Object.entries(plan.dangling).map(
                  ([id, missing]) => `${taskLabel(id)} → ${missing.join(", ")}`,
                ),
              ].join(" | ")}
              className="flex items-center gap-1 font-medium text-destructive"
            >
              <AlertTriangle className="h-3 w-3" aria-hidden />
              {tx("dev.board.planBrokenGraph", "broken dependencies")}
            </span>
          ) : null}
        </div>
      ) : null}

      <div className="flex min-h-0 flex-1">
        <div className="flex min-h-0 flex-1 gap-2 overflow-x-auto p-2">
          {STATUS_ORDER.map((status) => {
            const tasks = tasksByStatus.get(status) ?? [];
            const isDropTarget = dragOver === status && draggingId != null;
            return (
              <div
                key={status}
                onDragOver={(event) => {
                  if (!draggingRef.current) return;
                  event.preventDefault();
                  event.dataTransfer.dropEffect = "move";
                  if (dragOver !== status) setDragOver(status);
                }}
                onDrop={(event) => {
                  event.preventDefault();
                  const taskId =
                    event.dataTransfer.getData("text/plain") || draggingRef.current;
                  endDrag();
                  if (taskId) moveTask(taskId, status);
                }}
                className={cn(
                  "flex min-h-0 w-[220px] shrink-0 flex-col rounded-lg border bg-muted/20 transition-colors",
                  isDropTarget
                    ? "border-foreground/50 bg-foreground/[0.04]"
                    : "border-border/50",
                )}
              >
                <div className="flex items-center gap-1.5 px-2 py-1.5">
                  <span
                    className="h-2 w-2 shrink-0 rounded-full"
                    style={{ backgroundColor: STATUS_COLORS[status] }}
                  />
                  <span className="truncate text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                    {statusLabel(status)}
                  </span>
                  <span className="text-[10.5px] text-muted-foreground/70">{tasks.length}</span>
                  <div className="flex-1" />
                  <button
                    type="button"
                    onClick={() => {
                      setCreatingIn(creatingIn === status ? null : status);
                      setNewTitle("");
                    }}
                    title={tx("dev.board.addTask", "Add task")}
                    className="rounded p-0.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                  >
                    <Plus className="h-3.5 w-3.5" aria-hidden />
                  </button>
                </div>
                {creatingIn === status ? (
                  <div className="px-2 pb-1.5">
                    <input
                      autoFocus
                      value={newTitle}
                      onChange={(event) => setNewTitle(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") submitCreate();
                        if (event.key === "Escape") setCreatingIn(null);
                      }}
                      placeholder={tx("dev.board.newTaskPlaceholder", "Task title…")}
                      className="w-full rounded-md border border-border bg-background px-2 py-1 text-[12px] outline-none focus:border-foreground/40"
                    />
                  </div>
                ) : null}
                <div className="flex min-h-0 flex-1 flex-col gap-1.5 overflow-y-auto px-2 pb-2">
                  {tasks.map((task) => (
                    <div
                      key={task.id}
                      data-board-task-id={task.id}
                      role="button"
                      tabIndex={0}
                      draggable
                      title={tx("dev.board.dragHint", "Drag to another column")}
                      onDragStart={(event) => {
                        const target = event.target as HTMLElement;
                        if (target.closest("button, a, input, textarea, select")) {
                          event.preventDefault();
                          return;
                        }
                        event.dataTransfer.setData("text/plain", task.id);
                        event.dataTransfer.effectAllowed = "move";
                        draggingRef.current = task.id;
                        setDraggingId(task.id);
                      }}
                      onDragEnd={endDrag}
                      onClick={() => setSelectedId(task.id)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" || event.key === " ") {
                          event.preventDefault();
                          setSelectedId(task.id);
                        }
                      }}
                      className={cn(
                        "group cursor-grab select-none rounded-md border bg-background p-2 text-left shadow-sm transition-colors hover:border-foreground/40 active:cursor-grabbing",
                        selectedId === task.id
                          ? "border-foreground/60"
                          : "border-border/60",
                        draggingId === task.id && "cursor-grabbing opacity-45",
                      )}
                    >
                      <div className="flex items-start gap-1.5">
                        <span
                          className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full"
                          style={{ backgroundColor: PRIORITY_COLORS[task.priority] }}
                          title={task.priority}
                        />
                        <span className="min-w-0 flex-1 text-[12px] font-medium leading-snug text-foreground">
                          {task.title}
                        </span>
                        <button
                          type="button"
                          onClick={(event) => {
                            event.stopPropagation();
                            setConfirmDeleteId(task.id);
                          }}
                          title={tx("dev.board.deleteTask", "Delete task")}
                          className={cn(
                            "-mr-0.5 shrink-0 rounded p-0.5 text-muted-foreground transition-opacity hover:text-destructive focus-visible:opacity-100",
                            confirmDeleteId === task.id
                              ? "opacity-0"
                              : "opacity-0 group-hover:opacity-100",
                          )}
                        >
                          <Trash2 className="h-3 w-3" aria-hidden />
                        </button>
                      </div>
                      {confirmDeleteId === task.id ? (
                        <div className="mt-1.5 flex items-center gap-1.5">
                          <span className="text-[10.5px] text-muted-foreground">
                            {tx("dev.board.confirmDelete", "Delete this task?")}
                          </span>
                          <div className="flex-1" />
                          <button
                            type="button"
                            onClick={(event) => {
                              event.stopPropagation();
                              deleteTask(task.id);
                            }}
                            className="rounded border border-destructive/40 px-1.5 py-0.5 text-[10.5px] font-medium text-destructive transition-colors hover:bg-destructive/10"
                          >
                            {tx("dev.board.confirmDeleteYes", "Delete")}
                          </button>
                          <button
                            type="button"
                            onClick={(event) => {
                              event.stopPropagation();
                              setConfirmDeleteId(null);
                            }}
                            className="rounded border border-border px-1.5 py-0.5 text-[10.5px] text-muted-foreground transition-colors hover:bg-muted"
                          >
                            {tx("dev.board.cancel", "Cancel")}
                          </button>
                        </div>
                      ) : null}
                      <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                        {task.labels.slice(0, 3).map((label) => (
                          <span
                            key={label}
                            className="rounded bg-muted px-1 py-0.5 text-[10px] text-muted-foreground"
                          >
                            {label}
                          </span>
                        ))}
                        {task.milestone_id && milestoneById.has(task.milestone_id) ? (
                          <span className="flex items-center gap-0.5 rounded bg-muted px-1 py-0.5 text-[10px] text-muted-foreground">
                            <Flag className="h-2.5 w-2.5" aria-hidden />
                            {milestoneById.get(task.milestone_id)!.title}
                          </span>
                        ) : null}
                        {plan?.tasks[task.id]?.blocked_by.length ? (
                          <span
                            title={`${tx("dev.board.blockedBy", "Waiting on")}: ${plan.tasks[
                              task.id
                            ].blocked_by
                              .map(taskLabel)
                              .join(", ")}`}
                            className="flex items-center gap-0.5 rounded bg-amber-500/12 px-1 py-0.5 text-[10px] font-medium text-amber-600 dark:text-amber-400"
                          >
                            <Lock className="h-2.5 w-2.5" aria-hidden />
                            {plan.tasks[task.id].blocked_by.length}
                          </span>
                        ) : readyRank.get(task.id) === 0 ? (
                          <span
                            title={tx(
                              "dev.board.nextUpHelp",
                              "Top of the ready queue: nothing blocks it and it unblocks the most work.",
                            )}
                            className="rounded bg-emerald-500/12 px-1 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-emerald-600 dark:text-emerald-400"
                          >
                            {tx("dev.board.nextUp", "Next")}
                          </span>
                        ) : null}
                        {plan?.tasks[task.id]?.on_critical_path ? (
                          <span
                            title={tx(
                              "dev.board.criticalPathHelp",
                              "On the critical path: the longest chain of remaining work runs through this task.",
                            )}
                            className="flex items-center gap-0.5 text-[10px] text-rose-600 dark:text-rose-400"
                          >
                            <GitBranch className="h-2.5 w-2.5" aria-hidden />
                          </span>
                        ) : null}
                        {task.branch ? (
                          <span
                            title={task.branch}
                            className="flex min-w-0 items-center gap-0.5 rounded bg-violet-500/12 px-1 py-0.5 text-[10px] font-medium text-violet-600 dark:text-violet-400"
                          >
                            <GitBranch className="h-2.5 w-2.5 shrink-0" aria-hidden />
                            <span className="max-w-[80px] truncate">
                              {shortBranch(task.branch)}
                            </span>
                          </span>
                        ) : null}
                        {task.pr_url ? (
                          <a
                            href={task.pr_url}
                            target="_blank"
                            rel="noreferrer"
                            onClick={(event) => event.stopPropagation()}
                            title={task.pr_url}
                            className="flex items-center gap-0.5 rounded bg-sky-500/12 px-1 py-0.5 text-[10px] font-medium text-sky-600 hover:underline dark:text-sky-400"
                          >
                            <GitPullRequest className="h-2.5 w-2.5" aria-hidden />
                            PR
                          </a>
                        ) : null}
                        {task.issue_url ? (
                          <a
                            href={task.issue_url}
                            target="_blank"
                            rel="noreferrer"
                            onClick={(event) => event.stopPropagation()}
                            title={task.issue_url}
                            className="flex items-center gap-0.5 rounded bg-muted px-1 py-0.5 text-[10px] font-medium text-muted-foreground hover:underline"
                          >
                            <CircleDot className="h-2.5 w-2.5" aria-hidden />
                            {tx("dev.board.issueChip", "Issue")}
                          </a>
                        ) : null}
                        <div className="flex-1" />
                        {task.description ? (
                          <span
                            title={task.description.slice(0, 160)}
                            className="text-muted-foreground"
                          >
                            <AlignLeft className="h-2.5 w-2.5" aria-hidden />
                          </span>
                        ) : null}
                        {task.comments.length > 0 ? (
                          <span className="flex items-center gap-0.5 text-[10px] text-muted-foreground">
                            <MessageSquare className="h-2.5 w-2.5" aria-hidden />
                            {task.comments.length}
                          </span>
                        ) : null}
                        {task.assignee ? (
                          <span
                            title={`${task.assignee.name} (${task.assignee.type})`}
                            className="flex items-center gap-0.5 rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground"
                          >
                            <ActorIcon type={task.assignee.type} className="h-2.5 w-2.5" />
                            <span className="max-w-[70px] truncate">{task.assignee.name}</span>
                          </span>
                        ) : null}
                      </div>
                    </div>
                  ))}
                  {tasks.length === 0 && isDropTarget ? (
                    <div className="flex flex-1 items-center justify-center rounded-md border border-dashed border-foreground/25 px-2 py-6 text-[11px] text-muted-foreground">
                      {tx("dev.board.dropHint", "Drop here")}
                    </div>
                  ) : null}
                </div>
              </div>
            );
          })}
        </div>

        {selected ? (
          <div className="flex min-h-0 w-[320px] shrink-0 flex-col border-l border-border/60 bg-background">
            <div className="flex items-start gap-2 border-b border-border/60 px-3 py-2">
              <div className="min-w-0 flex-1">
                <input
                  value={titleDraft}
                  onChange={(event) => setTitleDraft(event.target.value)}
                  onBlur={commitTitle}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") event.currentTarget.blur();
                    if (event.key === "Escape") setTitleDraft(selected.title);
                  }}
                  aria-label={tx("dev.board.taskTitle", "Task title")}
                  className="w-full rounded-md border border-transparent bg-transparent px-1 py-0.5 text-[13px] font-semibold leading-snug outline-none hover:border-border/60 focus:border-foreground/40 focus:bg-background"
                />
                <div className="mt-0.5 px-1 text-[10.5px] text-muted-foreground">
                  {selected.id} · {tx("dev.board.createdBy", "created by")}{" "}
                  {selected.created_by.name} ({selected.created_by.type})
                </div>
              </div>
              {confirmDeleteId === selected.id ? (
                <div className="flex shrink-0 items-center gap-1">
                  <button
                    type="button"
                    onClick={() => deleteTask(selected.id)}
                    className="rounded border border-destructive/40 px-1.5 py-0.5 text-[10.5px] font-medium text-destructive transition-colors hover:bg-destructive/10"
                  >
                    {tx("dev.board.confirmDeleteYes", "Delete")}
                  </button>
                  <button
                    type="button"
                    onClick={() => setConfirmDeleteId(null)}
                    className="rounded border border-border px-1.5 py-0.5 text-[10.5px] text-muted-foreground transition-colors hover:bg-muted"
                  >
                    {tx("dev.board.cancel", "Cancel")}
                  </button>
                </div>
              ) : (
                <button
                  type="button"
                  onClick={() => setConfirmDeleteId(selected.id)}
                  title={tx("dev.board.deleteTask", "Delete task")}
                  className="shrink-0 rounded p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                >
                  <Trash2 className="h-3.5 w-3.5" aria-hidden />
                </button>
              )}
              <button
                type="button"
                onClick={() => setSelectedId(null)}
                title={tx("dev.board.close", "Close")}
                className="shrink-0 rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
              >
                <X className="h-3.5 w-3.5" aria-hidden />
              </button>
            </div>
            <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-3">
              <div className="flex items-center gap-2">
                <select
                  value={selected.status}
                  onChange={(event) =>
                    void mutate({
                      action: "update_task",
                      task_id: selected.id,
                      fields: { status: event.target.value as BoardTaskStatus },
                    })
                  }
                  className="flex-1 rounded-md border border-border bg-background px-2 py-1 text-[12px]"
                >
                  {STATUS_ORDER.map((status) => (
                    <option key={status} value={status}>
                      {statusLabel(status)}
                    </option>
                  ))}
                </select>
                <select
                  value={selected.priority}
                  onChange={(event) =>
                    void mutate({
                      action: "update_task",
                      task_id: selected.id,
                      fields: { priority: event.target.value as BoardTask["priority"] },
                    })
                  }
                  className="rounded-md border border-border bg-background px-2 py-1 text-[12px]"
                >
                  {(payload?.priorities ?? ["low", "medium", "high", "critical"]).map(
                    (priority) => (
                      <option key={priority} value={priority}>
                        {tx(`dev.board.priorities.${priority}`, priority)}
                      </option>
                    ),
                  )}
                </select>
              </div>

              {payload && payload.milestones.length > 0 ? (
                <select
                  value={selected.milestone_id ?? ""}
                  onChange={(event) =>
                    void mutate({
                      action: "update_task",
                      task_id: selected.id,
                      fields: {
                        milestone_id: (event.target.value || null) as BoardTask["milestone_id"],
                      },
                    })
                  }
                  className="rounded-md border border-border bg-background px-2 py-1 text-[12px]"
                >
                  <option value="">{tx("dev.board.noMilestone", "No milestone")}</option>
                  {payload.milestones.map((milestone) => (
                    <option key={milestone.id} value={milestone.id}>
                      {milestone.title}
                    </option>
                  ))}
                </select>
              ) : null}

              <div className="flex flex-col gap-1">
                <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                  {tx("dev.board.description", "Description")}
                </span>
                <textarea
                  value={descriptionDraft}
                  onChange={(event) => setDescriptionDraft(event.target.value)}
                  onBlur={commitDescription}
                  onKeyDown={(event) => {
                    if (event.key === "Escape") {
                      setDescriptionDraft(selected.description ?? "");
                      event.currentTarget.blur();
                    }
                  }}
                  rows={4}
                  placeholder={tx(
                    "dev.board.descriptionPlaceholder",
                    "Context, acceptance criteria, links… Saved when you click away.",
                  )}
                  className="w-full resize-y rounded-md border border-border bg-background px-2 py-1.5 text-[12px] leading-relaxed outline-none focus:border-foreground/40"
                />
              </div>

              <div className="flex flex-col gap-1.5">
                <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                  {tx("dev.board.dependencies", "Waits for")}
                </span>
                {selected.depends_on.length === 0 ? (
                  <span className="text-[11.5px] text-muted-foreground">
                    {tx("dev.board.noDependencies", "Nothing - this task can start now.")}
                  </span>
                ) : (
                  selected.depends_on.map((depId) => {
                    const dep = taskById.get(depId);
                    const satisfied = dep?.status === "done";
                    return (
                      <div
                        key={depId}
                        className="flex items-center gap-1.5 rounded-md border border-border/50 bg-muted/20 px-2 py-1"
                      >
                        <span
                          className="h-1.5 w-1.5 shrink-0 rounded-full"
                          style={{
                            backgroundColor: dep
                              ? STATUS_COLORS[dep.status]
                              : PRIORITY_COLORS.critical,
                          }}
                        />
                        <button
                          type="button"
                          onClick={() => dep && setSelectedId(dep.id)}
                          disabled={!dep}
                          className={cn(
                            "min-w-0 flex-1 truncate text-left text-[11.5px]",
                            satisfied && "text-muted-foreground line-through",
                            !dep && "text-destructive",
                          )}
                        >
                          {dep
                            ? dep.title
                            : `${depId} · ${tx("dev.board.unknownTask", "unknown task")}`}
                        </button>
                        <button
                          type="button"
                          onClick={() =>
                            void mutate({
                              action: "update_task",
                              task_id: selected.id,
                              fields: {
                                depends_on: selected.depends_on.filter((id) => id !== depId),
                              },
                            })
                          }
                          title={tx("dev.board.removeDependency", "Remove dependency")}
                          className="shrink-0 rounded p-0.5 text-muted-foreground hover:text-destructive"
                        >
                          <X className="h-3 w-3" aria-hidden />
                        </button>
                      </div>
                    );
                  })
                )}
                <select
                  value=""
                  onChange={(event) => {
                    const depId = event.target.value;
                    if (!depId) return;
                    void mutate({
                      action: "update_task",
                      task_id: selected.id,
                      fields: { depends_on: [...selected.depends_on, depId] },
                    });
                  }}
                  className="rounded-md border border-border bg-background px-2 py-1 text-[11.5px] text-muted-foreground"
                >
                  <option value="">{tx("dev.board.addDependency", "Add a dependency…")}</option>
                  {(payload?.tasks ?? [])
                    .filter(
                      (candidate) =>
                        candidate.id !== selected.id &&
                        !selected.depends_on.includes(candidate.id),
                    )
                    .map((candidate) => (
                      <option key={candidate.id} value={candidate.id}>
                        {candidate.title}
                      </option>
                    ))}
                </select>
              </div>

              {selected.assignee ? (
                <div className="flex items-center gap-1.5 text-[11.5px] text-muted-foreground">
                  <ActorIcon type={selected.assignee.type} className="h-3.5 w-3.5" />
                  {tx("dev.board.assignedTo", "Assigned to")} {selected.assignee.name} (
                  {selected.assignee.type})
                </div>
              ) : null}

              {selected.branch || selected.pr_url || selected.issue_url ? (
                <div className="flex flex-col gap-1.5">
                  <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                    {tx("dev.board.gitTrail", "Git / GitHub")}
                  </span>
                  {selected.branch ? (
                    <div className="flex items-center gap-1.5 rounded-md border border-border/50 bg-muted/20 px-2 py-1 text-[11.5px]">
                      <GitBranch className="h-3 w-3 shrink-0 text-violet-500" aria-hidden />
                      <span className="min-w-0 flex-1 truncate font-mono" title={selected.branch}>
                        {selected.branch}
                      </span>
                    </div>
                  ) : null}
                  {selected.pr_url ? (
                    <a
                      href={selected.pr_url}
                      target="_blank"
                      rel="noreferrer"
                      className="flex items-center gap-1.5 rounded-md border border-border/50 bg-muted/20 px-2 py-1 text-[11.5px] text-sky-600 hover:underline dark:text-sky-400"
                    >
                      <GitPullRequest className="h-3 w-3 shrink-0" aria-hidden />
                      <span className="min-w-0 flex-1 truncate">{selected.pr_url}</span>
                      <ExternalLink className="h-3 w-3 shrink-0" aria-hidden />
                    </a>
                  ) : null}
                  {selected.issue_url ? (
                    <a
                      href={selected.issue_url}
                      target="_blank"
                      rel="noreferrer"
                      className="flex items-center gap-1.5 rounded-md border border-border/50 bg-muted/20 px-2 py-1 text-[11.5px] text-muted-foreground hover:underline"
                    >
                      <CircleDot className="h-3 w-3 shrink-0" aria-hidden />
                      <span className="min-w-0 flex-1 truncate">{selected.issue_url}</span>
                      <ExternalLink className="h-3 w-3 shrink-0" aria-hidden />
                    </a>
                  ) : null}
                </div>
              ) : null}

              {onRunAction ? (
                <button
                  type="button"
                  onClick={() => onRunAction(`/board task ${selected.id}`)}
                  className="flex items-center justify-center gap-1.5 rounded-md border border-border px-2 py-1.5 text-[12px] font-medium transition-colors hover:bg-foreground hover:text-background"
                >
                  <Bot className="h-3.5 w-3.5" aria-hidden />
                  {tx("dev.board.delegateToAgent", "Hand to the agent")}
                </button>
              ) : null}

              <div className="flex flex-col gap-2">
                <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                  {tx("dev.board.comments", "Comments")}
                </span>
                {selected.comments.length === 0 ? (
                  <span className="text-[11.5px] text-muted-foreground">
                    {tx("dev.board.noComments", "No comments yet.")}
                  </span>
                ) : (
                  selected.comments.map((comment, index) => (
                    <div
                      key={`${comment.ts}-${index}`}
                      className="rounded-md border border-border/50 bg-muted/20 p-2"
                    >
                      <div className="flex items-center gap-1 text-[10.5px] text-muted-foreground">
                        <ActorIcon type={comment.author_type} className="h-3 w-3" />
                        <span className="font-medium">{comment.author}</span>
                        <span>· {comment.ts}</span>
                      </div>
                      <p className="mt-1 whitespace-pre-wrap text-[11.5px] leading-relaxed">
                        {comment.text}
                      </p>
                    </div>
                  ))
                )}
                <div className="flex items-center gap-1.5">
                  <input
                    value={commentText}
                    onChange={(event) => setCommentText(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") submitComment();
                    }}
                    placeholder={tx("dev.board.commentPlaceholder", "Report or comment…")}
                    className="min-w-0 flex-1 rounded-md border border-border bg-background px-2 py-1 text-[12px] outline-none focus:border-foreground/40"
                  />
                  <button
                    type="button"
                    onClick={submitComment}
                    disabled={!commentText.trim()}
                    className="rounded-md border border-border p-1.5 text-muted-foreground transition-colors hover:bg-foreground hover:text-background disabled:pointer-events-none disabled:opacity-40"
                  >
                    <Send className="h-3.5 w-3.5" aria-hidden />
                  </button>
                </div>
              </div>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}

export default DevBoardPanel;
