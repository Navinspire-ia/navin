// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  ArrowRight,
  BookOpen,
  CircleDot,
  Clock3,
  FolderKanban,
  GitBranch,
  Github,
  History,
  Loader2,
  Milestone,
  Play,
  Radar,
  RefreshCw,
  Sparkles,
  SquareKanban,
  Waypoints,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { DevGitTimeline } from "@/components/dev/DevGitTimeline";
import { DevProjectSelector } from "@/components/dev/DevProjectSelector";
import { ProjectIssuesPanel } from "@/components/project/ProjectIssuesPanel";
import { lazyWithRetry } from "@/lib/lazy-retry";
import { Button } from "@/components/ui/button";
import {
  fetchBoard,
  fetchProjectBrain,
  fetchResumeSeed,
  type BoardPayload,
  type ProjectBrainPayload,
  type ResumeSeedPayload,
} from "@/lib/api";
import type { ChatSummary, RecentProjectEntry } from "@/lib/types";
import { cn } from "@/lib/utils";
import {
  projectNameFromPath,
  sameWorkspacePath,
  shortWorkspacePath,
} from "@/lib/workspace";
import { useClient } from "@/providers/ClientProvider";

type ContinuityTab =
  | "resume"
  | "tasks"
  | "evolutions"
  | "issues"
  | "vision"
  | "graph"
  | "timeline"
  | "git";

// Reuse the Code workbench panels as-is so Project Home shows the exact same
// board / evolutions / 360 vision / graph, kept in sync through the same APIs.
const DevBoardPanel = lazyWithRetry(() => import("@/components/dev/DevBoardPanel"));
const DevEvolutions = lazyWithRetry(() => import("@/components/dev/DevEvolutions"));
const DevProjectAudit = lazyWithRetry(() => import("@/components/dev/DevProjectAudit"));
const DevMetagraph = lazyWithRetry(() => import("@/components/dev/DevMetagraph"));

function PanelFallback() {
  return (
    <div className="flex flex-1 items-center justify-center py-16 text-muted-foreground">
      <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
    </div>
  );
}

function relativeLabel(
  iso: string | null | undefined,
  fallback: string,
  t: (key: string, options?: Record<string, unknown>) => string,
): string {
  if (!iso) return fallback;
  const ts = Date.parse(iso);
  if (!Number.isFinite(ts)) return fallback;
  const deltaSec = Math.max(0, Math.round((Date.now() - ts) / 1000));
  if (deltaSec < 60) return t("projectHome.relative.justNow");
  if (deltaSec < 3600) {
    return t("projectHome.relative.minutesAgo", {
      count: Math.floor(deltaSec / 60),
    });
  }
  if (deltaSec < 86400) {
    return t("projectHome.relative.hoursAgo", {
      count: Math.floor(deltaSec / 3600),
    });
  }
  const days = Math.floor(deltaSec / 86400);
  if (days === 1) return t("projectHome.relative.yesterday");
  if (days < 30) return t("projectHome.relative.daysAgo", { count: days });
  return t("projectHome.relative.monthsAgo", {
    count: Math.floor(days / 30),
  });
}

/**
 * Project Home - continuity surface for month-long work.
 * Preview UI wired to board/sessions when available; demo content otherwise.
 * Served only via Vite `#/project` so production desktop builds stay untouched
 * until an explicit `npm run build`.
 */
export function ProjectHomeView({
  sessions,
  projectPath,
  projectName,
  recentProjects,
  onSelectProject,
  onOpenCode,
  onOpenChat,
  onResumeGoal,
}: {
  sessions: ChatSummary[];
  projectPath: string | null;
  projectName: string | null;
  recentProjects: RecentProjectEntry[];
  onSelectProject: (path: string, name?: string | null) => void;
  onOpenCode: () => void;
  onOpenChat: (key: string) => void;
  onResumeGoal: (seed: string) => void;
}) {
  const { t } = useTranslation();
  const { client, token } = useClient();
  const [tab, setTab] = useState<ContinuityTab>("git");
  const [board, setBoard] = useState<BoardPayload | null>(null);
  const [loadingBoard, setLoadingBoard] = useState(false);
  const [boardError, setBoardError] = useState<string | null>(null);
  const [usingDemo, setUsingDemo] = useState(true);
  const [brain, setBrain] = useState<ProjectBrainPayload | null>(null);
  const [loadingBrain, setLoadingBrain] = useState(false);
  const [resumePayload, setResumePayload] = useState<ResumeSeedPayload | null>(null);

  const displayName =
    projectName?.trim()
    || (projectPath ? projectNameFromPath(projectPath) : "")
    || t("projectHome.untitled");

  const projectSessions = useMemo(() => {
    if (!projectPath) return sessions.slice(0, 8);
    // Strict match only: falling back to unrelated chats reused their
    // workspace (often ~/.navin or NavinProjects with no GitHub remote) and
    // made Issues say "no git remotes found" while the header showed another
    // repo.
    return sessions
      .filter((s) => sameWorkspacePath(s.workspaceScope?.project_path, projectPath))
      .slice(0, 8);
  }, [projectPath, sessions]);

  const boardKey = useMemo(() => {
    const scoped = projectSessions.find((s) => s.key)?.key;
    if (scoped) return scoped;
    // Auth-only fallback: Issues/Git pass projectPath explicitly so a chat
    // bound to another folder cannot poison the GitHub remote lookup.
    return sessions[0]?.key ?? null;
  }, [projectSessions, sessions]);

  const loadBoard = useCallback(async () => {
    if (!token || !boardKey) {
      setBoard(null);
      setUsingDemo(true);
      setBoardError(null);
      return;
    }
    setLoadingBoard(true);
    setBoardError(null);
    try {
      const payload = await fetchBoard(token, boardKey);
      setBoard(payload);
      // Bound projects never fall back to marketing demo content.
      setUsingDemo(!projectPath);
    } catch {
      setBoard(null);
      setUsingDemo(!projectPath);
      setBoardError(
        t(
          projectPath
            ? "projectHome.boardUnavailable"
            : "projectHome.boardUnavailableDemo",
        ),
      );
    } finally {
      setLoadingBoard(false);
    }
  }, [boardKey, projectPath, t, token]);

  useEffect(() => {
    void loadBoard();
  }, [loadBoard]);

  useEffect(() => {
    const unsubscribe = client.onBoardUpdate(() => void loadBoard());
    return unsubscribe;
  }, [client, loadBoard]);

  const loadBrain = useCallback(async () => {
    if (!token || !boardKey || !projectPath) {
      setBrain(null);
      return;
    }
    setLoadingBrain(true);
    try {
      const payload = await fetchProjectBrain(token, boardKey);
      setBrain(payload);
    } catch {
      setBrain(null);
    } finally {
      setLoadingBrain(false);
    }
  }, [boardKey, projectPath, token]);

  useEffect(() => {
    void loadBrain();
  }, [loadBrain]);

  const openTasks = useMemo(() => {
    // Never invent fake tasks for a real bound project - empty board is honest.
    if (!board || usingDemo) {
      if (projectPath) return [];
      return [
        {
          id: "demo-1",
          title: "Unifier les studios sous le contexte projet",
          status: "in_progress",
        },
        {
          id: "demo-2",
          title: "Brief Resume apres 14 jours d'absence",
          status: "planned",
        },
        {
          id: "demo-3",
          title: "Panneau Brain editable (MEMORY / decisions)",
          status: "review",
        },
      ];
    }
    return board.tasks
      .filter((task) => !["done", "cancelled"].includes(task.status))
      .slice(0, 6)
      .map((task) => ({
        id: task.id,
        title: task.title,
        status: task.status,
      }));
  }, [board, projectPath, usingDemo]);

  const milestones = useMemo(() => {
    if (!board || usingDemo) {
      if (projectPath) return [];
      return [
        { id: "m1", title: "Continuité UX v1", progress: 35, status: "active" },
        { id: "m2", title: "Trust layer des runs longs", progress: 10, status: "planned" },
        { id: "m3", title: "Collab handoff equipe", progress: 0, status: "planned" },
      ];
    }
    return board.milestones.slice(0, 5).map((m) => {
      const prog = board.milestone_progress?.[m.id];
      const total = prog?.total ?? 0;
      const done = prog?.done ?? 0;
      return {
        id: m.id,
        title: m.title,
        status: m.status,
        progress: total > 0 ? Math.round((done / total) * 100) : 0,
      };
    });
  }, [board, usingDemo]);

  const activity = useMemo(() => {
    if (!board || usingDemo || board.activity.length === 0) {
      if (projectPath) return [];
      return [
        {
          id: "a1",
          title: "Spec Project Home / Resume / Brain",
          when: "aujourd'hui",
        },
        {
          id: "a2",
          title: "Sidebar: entree Projet ajoutee",
          when: "aujourd'hui",
        },
        {
          id: "a3",
          title: "Derniere session Code sur preview serveur",
          when: "hier",
        },
      ];
    }
    return board.activity.slice(0, 8).map((entry, index) => ({
      id: `${entry.ts}-${entry.kind}-${index}`,
      title: entry.detail?.trim() || entry.kind,
      when: relativeLabel(entry.ts, "", t),
    }));
  }, [board, projectPath, usingDemo]);

  const planItems = board?.session_plan?.items?.slice(0, 5) ?? [];

  const loadResumeSeed = useCallback(async () => {
    if (!token || !boardKey || !projectPath) {
      setResumePayload(null);
      return;
    }
    try {
      setResumePayload(await fetchResumeSeed(token, boardKey));
    } catch {
      setResumePayload(null);
    }
  }, [boardKey, projectPath, token]);

  useEffect(() => {
    void loadResumeSeed();
  }, [loadResumeSeed]);

  const resumeSeed = useMemo(() => {
    if (resumePayload?.seed) return resumePayload.seed;
    // Fallback when the seed API is unavailable - still useful offline.
    const taskLines = openTasks
      .slice(0, 3)
      .map((task) => `- [${task.status}] ${task.title}`)
      .join("\n");
    const constraintLines = (brain?.constraints ?? [])
      .slice(0, 5)
      .map((line) => `- ${line}`)
      .join("\n");
    const resumeBrief = brain?.continuity?.resume?.content?.trim() || "";
    return [
      t("projectHome.resume.seed.intro", { name: displayName }),
      t("projectHome.resume.seed.read"),
      "",
      t("projectHome.resume.seed.brief"),
      resumeBrief || t("projectHome.resume.seed.briefEmpty"),
      "",
      t("projectHome.resume.seed.constraints"),
      constraintLines || t("projectHome.resume.seed.noConstraints"),
      "",
      t("projectHome.resume.seed.tasks"),
      taskLines || t("projectHome.resume.seed.noTasks"),
      "",
      t("projectHome.resume.seed.outro"),
    ].join("\n");
  }, [brain, displayName, openTasks, resumePayload, t]);

  const driftWarnings = useMemo(() => {
    const rows = brain?.drift?.violations ?? [];
    return rows
      .map((row) => {
        if (typeof row === "string") return row.trim();
        if (row && typeof row === "object") {
          return String(row.detail || row.constraint || "").trim();
        }
        return "";
      })
      .filter(Boolean)
      .slice(0, 6);
  }, [brain]);

  const tabs: { id: ContinuityTab; label: string; icon: typeof Play }[] = [
    {
      id: "git",
      label: t("projectHome.tabs.git", { defaultValue: "Git" }),
      icon: GitBranch,
    },
    {
      id: "issues",
      label: t("projectHome.tabs.issues", { defaultValue: "Issues" }),
      icon: Github,
    },
    {
      id: "tasks",
      label: t("projectHome.tabs.tasks", { defaultValue: "Tasks" }),
      icon: SquareKanban,
    },
    {
      id: "vision",
      label: t("projectHome.tabs.vision", { defaultValue: "360° Vision" }),
      icon: Radar,
    },
    {
      id: "evolutions",
      label: t("projectHome.tabs.evolutions", { defaultValue: "Evolutions" }),
      icon: History,
    },
    {
      id: "graph",
      label: t("projectHome.tabs.graph", { defaultValue: "Graph" }),
      icon: Waypoints,
    },
    {
      id: "resume",
      label: t("projectHome.tabs.resume"),
      icon: Play,
    },
    {
      id: "timeline",
      label: t("projectHome.tabs.timeline"),
      icon: Clock3,
    },
  ];

  return (
    <div className="flex h-full min-h-0 flex-col bg-background text-foreground">
      <header className="shrink-0 border-b border-border/70 px-5 py-4 sm:px-7">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
              {t("projectHome.kicker")}
            </p>
            <h1 className="mt-1 truncate text-[1.35rem] font-semibold tracking-tight">
              {displayName}
            </h1>
            <p className="mt-1 max-w-[40rem] text-sm text-muted-foreground">
              {projectPath
                ? shortWorkspacePath(projectPath)
                : t("projectHome.pickProject")}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <DevProjectSelector
              projectPath={projectPath}
              projectName={projectName}
              recentProjects={recentProjects}
              onSelectProject={onSelectProject}
            />
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="gap-1.5"
              onClick={() => {
                void loadBoard();
                void loadBrain();
              }}
              disabled={loadingBoard || loadingBrain}
            >
              {loadingBoard || loadingBrain ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <RefreshCw className="h-3.5 w-3.5" />
              )}
              {t("projectHome.refresh")}
            </Button>
            <Button
              type="button"
              size="sm"
              className="gap-1.5"
              onClick={() => onResumeGoal(resumeSeed)}
            >
              <Sparkles className="h-3.5 w-3.5" />
              {t("projectHome.resumeCta")}
            </Button>
          </div>
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-2">
          {tabs.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              type="button"
              onClick={() => setTab(id)}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-[12.5px] font-medium transition-colors",
                tab === id
                  ? "border-foreground/20 bg-foreground text-background"
                  : "border-border bg-transparent text-muted-foreground hover:bg-muted/60 hover:text-foreground",
              )}
            >
              <Icon className="h-3.5 w-3.5" />
              {label}
            </button>
          ))}
          {usingDemo ? (
            <span className="rounded-full border border-amber-500/30 bg-amber-500/10 px-2.5 py-1 text-[11px] font-medium text-amber-700 dark:text-amber-300">
              {t("projectHome.demoBadge")}
            </span>
          ) : null}
          {boardError ? (
            <span className="text-[11px] text-muted-foreground">{boardError}</span>
          ) : null}
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5 sm:px-7">
        {tab === "resume" ? (
          <div className="grid gap-4 lg:grid-cols-[1.2fr_0.9fr]">
            <section className="rounded-2xl border border-border/70 bg-card/40 p-5">
              <div className="flex items-center gap-2">
                <FolderKanban className="h-4 w-4 text-muted-foreground" />
                <h2 className="text-sm font-semibold">
                  {t("projectHome.resume.title")}
                </h2>
              </div>
              <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
                {t("projectHome.resume.blurb")}
              </p>

              {brain?.drift?.supported && driftWarnings.length > 0 ? (
                <div className="mt-4 rounded-xl border border-amber-500/35 bg-amber-500/10 px-3 py-2.5">
                  <div className="flex items-start gap-2">
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-700 dark:text-amber-300" />
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-semibold text-amber-900 dark:text-amber-100">
                        {t("projectHome.resume.driftTitle", {
                          defaultValue: "Possible constraint drift",
                        })}
                      </p>
                      <p className="mt-0.5 text-[12px] text-amber-800/90 dark:text-amber-200/90">
                        {brain.drift.note ||
                          t("projectHome.resume.driftNote", {
                            defaultValue:
                              "Soft warnings only - review before shipping.",
                          })}
                      </p>
                      <ul className="mt-2 space-y-1">
                        {driftWarnings.map((line) => (
                          <li
                            key={line}
                            className="truncate text-[12.5px] text-amber-950 dark:text-amber-50"
                            title={line}
                          >
                            {line}
                          </li>
                        ))}
                      </ul>
                    </div>
                  </div>
                </div>
              ) : brain?.drift?.supported && (brain.constraints?.length ?? 0) > 0 ? (
                <p className="mt-4 text-[12px] text-muted-foreground">
                  {brain.drift.note ||
                    t("projectHome.resume.driftClear", {
                      defaultValue:
                        "Constraint drift check active - no likely violations.",
                    })}
                </p>
              ) : null}

              <div className="mt-4 space-y-2">
                {openTasks.length === 0 ? (
                  <p className="rounded-xl border border-dashed border-border/70 px-3 py-2.5 text-sm text-muted-foreground">
                    {t("projectHome.resume.noTasks")}
                  </p>
                ) : (
                  openTasks.map((task) => (
                  <div
                    key={task.id}
                    className="flex items-start gap-3 rounded-xl border border-border/60 bg-background/70 px-3 py-2.5"
                  >
                    <CircleDot className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium">{task.title}</p>
                      <p className="mt-0.5 text-[11px] uppercase tracking-wide text-muted-foreground">
                        {task.status}
                      </p>
                    </div>
                  </div>
                  ))
                )}
              </div>

              {planItems.length > 0 ? (
                <div className="mt-5 rounded-xl border border-border/60 bg-background/50 p-3">
                  <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                    {t("projectHome.resume.plan")}
                  </p>
                  <ul className="mt-2 space-y-1.5">
                    {planItems.map((item) => (
                      <li
                        key={item.id}
                        className="flex items-center gap-2 text-sm text-muted-foreground"
                      >
                        <span
                          className={cn(
                            "h-1.5 w-1.5 rounded-full",
                            item.done
                              ? "bg-emerald-500"
                              : item.active
                                ? "bg-sky-500"
                                : "bg-muted-foreground/40",
                          )}
                        />
                        <span className="truncate">{item.title}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}

              <div className="mt-5 flex flex-wrap gap-2">
                <Button
                  type="button"
                  className="gap-1.5"
                  onClick={() => onResumeGoal(resumeSeed)}
                >
                  <Play className="h-3.5 w-3.5" />
                  {t("projectHome.resume.start")}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  className="gap-1.5"
                  onClick={onOpenCode}
                >
                  <GitBranch className="h-3.5 w-3.5" />
                  {t("projectHome.resume.openCode")}
                </Button>
              </div>
            </section>

            <section className="space-y-4">
              <div className="rounded-2xl border border-border/70 bg-card/40 p-5">
                <div className="flex items-center gap-2">
                  <Milestone className="h-4 w-4 text-muted-foreground" />
                  <h2 className="text-sm font-semibold">
                    {t("projectHome.resume.milestones")}
                  </h2>
                </div>
                <div className="mt-3 space-y-3">
                  {milestones.map((m) => (
                    <div key={m.id}>
                      <div className="flex items-center justify-between gap-2 text-sm">
                        <span className="truncate font-medium">{m.title}</span>
                        <span className="text-[11px] text-muted-foreground">
                          {m.progress}%
                        </span>
                      </div>
                      <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-muted">
                        <div
                          className="h-full rounded-full bg-foreground/80"
                          style={{ width: `${Math.min(100, m.progress)}%` }}
                        />
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              <div className="rounded-2xl border border-border/70 bg-card/40 p-5">
                <div className="flex items-center gap-2">
                  <BookOpen className="h-4 w-4 text-muted-foreground" />
                  <h2 className="text-sm font-semibold">
                    {t("projectHome.resume.sessions")}
                  </h2>
                </div>
                <div className="mt-3 space-y-1.5">
                  {projectSessions.length === 0 ? (
                    <p className="text-sm text-muted-foreground">
                      {t("projectHome.resume.noSessions")}
                    </p>
                  ) : (
                    projectSessions.map((session) => (
                      <button
                        key={session.key}
                        type="button"
                        onClick={() => onOpenChat(session.key)}
                        className="flex w-full items-center gap-2 rounded-xl px-2.5 py-2 text-left transition-colors hover:bg-muted/70"
                      >
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-sm font-medium">
                            {session.title || session.preview || session.chatId}
                          </p>
                          <p className="truncate text-[11px] text-muted-foreground">
                            {relativeLabel(session.updatedAt, "", t)}
                          </p>
                        </div>
                        <ArrowRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                      </button>
                    ))
                  )}
                </div>
              </div>
            </section>
          </div>
        ) : null}

        {tab === "tasks" ? (
          <section className="flex h-[72vh] min-h-[28rem] flex-col overflow-hidden rounded-2xl border border-border/70 bg-card/40">
            {token && boardKey && projectPath ? (
              <Suspense fallback={<PanelFallback />}>
                <DevBoardPanel
                  sessionKey={boardKey}
                  projectPath={projectPath}
                  onRunAction={(text) => onResumeGoal(text)}
                />
              </Suspense>
            ) : (
              <p className="px-4 py-6 text-sm text-muted-foreground">
                {t("projectHome.brain.pickFirst")}
              </p>
            )}
          </section>
        ) : null}

        {tab === "evolutions" ? (
          <section className="flex h-[72vh] min-h-[28rem] flex-col overflow-hidden rounded-2xl border border-border/70 bg-card/40">
            {token && boardKey && projectPath ? (
              <Suspense fallback={<PanelFallback />}>
                <DevEvolutions
                  sessionKey={boardKey}
                  projectPath={projectPath}
                />
              </Suspense>
            ) : (
              <p className="px-4 py-6 text-sm text-muted-foreground">
                {t("projectHome.brain.pickFirst")}
              </p>
            )}
          </section>
        ) : null}

        {tab === "issues" ? (
          <section className="flex h-[72vh] min-h-[28rem] flex-col overflow-hidden rounded-2xl border border-border/70 bg-card/40">
            {token && boardKey && projectPath ? (
              <ProjectIssuesPanel
                sessionKey={boardKey}
                projectPath={projectPath}
                onRunAction={(text) => onResumeGoal(text)}
              />
            ) : (
              <p className="px-4 py-6 text-sm text-muted-foreground">
                {t("projectHome.brain.pickFirst")}
              </p>
            )}
          </section>
        ) : null}

        {tab === "vision" ? (
          <section className="flex h-[72vh] min-h-[28rem] flex-col overflow-hidden rounded-2xl border border-border/70 bg-card/40">
            {projectPath ? (
              <Suspense fallback={<PanelFallback />}>
                <DevProjectAudit projectPath={projectPath} />
              </Suspense>
            ) : (
              <p className="px-4 py-6 text-sm text-muted-foreground">
                {t("projectHome.brain.pickFirst")}
              </p>
            )}
          </section>
        ) : null}

        {tab === "graph" ? (
          <section className="flex h-[72vh] min-h-[28rem] flex-col overflow-hidden rounded-2xl border border-border/70 bg-card/40">
            {token && boardKey && projectPath ? (
              <Suspense fallback={<PanelFallback />}>
                <DevMetagraph
                  sessionKey={boardKey}
                  onRunAction={(text) => onResumeGoal(text)}
                />
              </Suspense>
            ) : (
              <p className="px-4 py-6 text-sm text-muted-foreground">
                {t("projectHome.brain.pickFirst")}
              </p>
            )}
          </section>
        ) : null}

        {tab === "timeline" ? (
          <section className="mx-auto max-w-3xl rounded-2xl border border-border/70 bg-card/40 p-5">
            <div className="flex items-center gap-2">
              <Clock3 className="h-4 w-4 text-muted-foreground" />
              <h2 className="text-sm font-semibold">
                {t("projectHome.timeline.title")}
              </h2>
            </div>
            <p className="mt-2 text-sm text-muted-foreground">
              {t("projectHome.timeline.blurb")}
            </p>
            <ol className="relative mt-5 space-y-0 border-l border-border/70 pl-5">
              {activity.map((item) => (
                <li key={item.id} className="relative pb-5 last:pb-0">
                  <span className="absolute -left-[1.41rem] top-1.5 h-2.5 w-2.5 rounded-full border-2 border-background bg-foreground/70" />
                  <p className="text-sm font-medium">{item.title}</p>
                  <p className="mt-0.5 text-[11px] text-muted-foreground">
                    {item.when}
                  </p>
                </li>
              ))}
            </ol>
          </section>
        ) : null}

        {tab === "git" ? (
          <section className="mx-auto flex h-full min-h-[28rem] max-w-4xl flex-col rounded-2xl border border-border/70 bg-card/40 p-4">
            <div className="flex shrink-0 items-center gap-2 px-1 pb-2">
              <GitBranch className="h-4 w-4 text-muted-foreground" />
              <h2 className="text-sm font-semibold">
                {t("projectHome.git.title", { defaultValue: "Timeline Git" })}
              </h2>
              <p className="ml-2 hidden text-[12px] text-muted-foreground sm:block">
                {t("projectHome.git.blurb", {
                  defaultValue:
                    "Branches, commits, merges et auteurs du projet, avec le graphe des relations.",
                })}
              </p>
            </div>
            {token && boardKey ? (
              <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-border/60 bg-background/70">
                <DevGitTimeline token={token} sessionKey={boardKey} />
              </div>
            ) : (
              <p className="px-1 py-4 text-sm text-muted-foreground">
                {t("projectHome.brain.pickFirst")}
              </p>
            )}
          </section>
        ) : null}
      </div>
    </div>
  );
}
