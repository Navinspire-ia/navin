// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { intlLocale } from "@/i18n/intl";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import {
  Activity,
  ChevronRight,
  Clock,
  FileCode2,
  Link2,
  FolderGit2,
  GitBranch,
  GitCommitHorizontal,
  History,
  Terminal,
  Users,
} from "lucide-react";

import { cn } from "@/lib/utils";

import type { ShareContext } from "./relay-socket";

function when(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "?";
  const date = typeof value === "number" ? new Date(value * 1000) : new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString(intlLocale(), { dateStyle: "medium", timeStyle: "short" });
}

const SECTIONS_KEY = "navin.guest.sections";

function readOpenSections(): Record<string, boolean> {
  try {
    const raw = JSON.parse(window.localStorage.getItem(SECTIONS_KEY) || "{}") as unknown;
    return raw && typeof raw === "object" ? (raw as Record<string, boolean>) : {};
  } catch {
    return {};
  }
}

function rememberSection(id: string, open: boolean): void {
  try {
    window.localStorage.setItem(SECTIONS_KEY, JSON.stringify({ ...readOpenSections(), [id]: open }));
  } catch {
    // Kept for this visit only.
  }
}

/** A collapsible block: closed by default (except History), the visitor opens what they need. */
function Section({
  id,
  icon,
  title,
  count,
  defaultOpen = false,
  children,
}: {
  id: string;
  icon: ReactNode;
  title: string;
  count?: number;
  defaultOpen?: boolean;
  children: ReactNode;
}) {
  const [open, setOpenState] = useState(() => readOpenSections()[id] ?? defaultOpen);
  const setOpen = (update: (value: boolean) => boolean) => {
    setOpenState((value) => {
      const next = update(value);
      rememberSection(id, next);
      return next;
    });
  };
  return (
    <section className="border-b border-border/60 px-3 py-2.5">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 rounded-md px-1 py-0.5 text-[12px] font-semibold uppercase tracking-wide text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
      >
        <ChevronRight className={cn("rtl:[&:not(.rotate-90)]:rotate-180 h-3.5 w-3.5 transition-transform", open && "rotate-90")} aria-hidden />
        {icon}
        <span className="flex-1 text-start">{title}</span>
        {count !== undefined ? (
          <span className="rounded-full bg-muted px-1.5 py-px text-[11px] font-medium normal-case tabular-nums">{count}</span>
        ) : null}
      </button>
      {open ? <div className="mt-2 space-y-1.5 text-[12.5px]">{children}</div> : null}
    </section>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex gap-2">
      <span className="w-24 shrink-0 text-muted-foreground">{label}</span>
      <span className="min-w-0 flex-1 break-words text-foreground/90">{children}</span>
    </div>
  );
}

const STATUS_TONE: Record<string, string> = {
  M: "text-amber-600 dark:text-amber-300",
  A: "text-emerald-600 dark:text-emerald-300",
  D: "text-rose-600 dark:text-rose-300",
  "??": "text-sky-600 dark:text-sky-300",
};

/**
 * Everything a guest needs to understand the session and pick it up: dates,
 * project and how to run it, git state and history, files edited, and every
 * prompt (click one to jump to it in the conversation).
 */
export function GuestSidebar({
  context,
  onOpenSubagentHistory,
  expiresAt,
  onJump,
  className,
}: {
  context: ShareContext | null;
  onOpenSubagentHistory?: () => void;
  expiresAt: number;
  onJump: (promptIndex: number) => void;
  className?: string;
}) {
  const { t } = useTranslation();
  const conv = context?.conversation ?? null;
  const project = context?.project ?? null;
  const git = context?.git ?? null;
  const commands = Object.entries(project?.commands ?? {});

  return (
    <aside className={cn("flex min-h-0 flex-col overflow-y-auto border-e border-border/70 bg-card/40", className)}>
      <div className="border-b border-border/60 px-3 pb-3 pt-3.5">
        <p className="truncate text-[15px] font-semibold tracking-tight" title={conv?.title || project?.name}>
          {conv?.title || project?.name || t("chat.liveShare.guest.live", { defaultValue: "Live session" })}
        </p>
        {conv?.model ? <p className="mt-0.5 truncate text-[12px] text-muted-foreground">{conv.model}</p> : null}
        {onOpenSubagentHistory ? <button type="button" onClick={onOpenSubagentHistory} className="mt-2 flex min-h-10 w-full items-center gap-2 rounded-md px-1 text-start text-xs text-muted-foreground hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"><Users className="h-4 w-4" />{t("thread.subagents.historyTitle", { defaultValue: "Subagent history" })}</button> : null}
        {conv ? (
          <div className="mt-3 grid grid-cols-3 gap-1.5">
            {[
              { value: conv.user_messages, label: t("chat.liveShare.guest.ctx.promptsLabel", { defaultValue: "Prompts" }) },
              { value: conv.assistant_messages, label: t("chat.liveShare.guest.ctx.repliesLabel", { defaultValue: "Replies" }) },
              { value: conv.tool_calls, label: t("chat.liveShare.guest.ctx.toolsLabel", { defaultValue: "Tools" }) },
            ].map((stat) => (
              <div key={stat.label} className="rounded-lg border border-border/60 bg-muted/30 px-2 py-1.5 text-center">
                <div className="text-[15px] font-semibold tabular-nums leading-tight">{stat.value}</div>
                <div className="truncate text-[10.5px] text-muted-foreground">{stat.label}</div>
              </div>
            ))}
          </div>
        ) : null}
        <dl className="mt-3 space-y-1.5 text-[12px]">
          {[
            { icon: <Clock className="h-3.5 w-3.5" aria-hidden />, label: t("chat.liveShare.guest.ctx.startedLabel", { defaultValue: "Started" }), value: when(conv?.created_at) },
            { icon: <Activity className="h-3.5 w-3.5" aria-hidden />, label: t("chat.liveShare.guest.ctx.lastActivityLabel", { defaultValue: "Last activity" }), value: when(conv?.updated_at) },
            { icon: <Link2 className="h-3.5 w-3.5" aria-hidden />, label: t("chat.liveShare.guest.ctx.expiresLabel", { defaultValue: "Link valid until" }), value: when(expiresAt) },
          ].map((row) => (
            <div key={row.label} className="flex items-center gap-2 text-muted-foreground">
              {row.icon}
              <dt className="min-w-0 flex-1 truncate">{row.label}</dt>
              <dd className="shrink-0 tabular-nums text-foreground/85">{row.value}</dd>
            </div>
          ))}
        </dl>
      </div>

      {conv?.prompts.length ? (
        <Section id="history" defaultOpen icon={<History className="h-3.5 w-3.5" aria-hidden />} title={t("chat.liveShare.guest.ctx.history", { defaultValue: "History" })} count={conv.prompts.length}>
          <ol className="max-h-80 space-y-0.5 overflow-y-auto">
            {conv.prompts.map((prompt) => (
              <li key={prompt.index}>
                <button
                  type="button"
                  onClick={() => onJump(prompt.index)}
                  className="flex w-full gap-2 rounded px-1.5 py-1 text-start hover:bg-muted"
                  title={prompt.text}
                >
                  <span className="w-5 shrink-0 text-end tabular-nums text-muted-foreground">{prompt.index + 1}</span>
                  <span className="line-clamp-2 min-w-0 flex-1">{prompt.text}</span>
                </button>
              </li>
            ))}
          </ol>
        </Section>
      ) : null}

      {project ? (
        <Section id="project" icon={<FolderGit2 className="h-3.5 w-3.5" aria-hidden />} title={t("chat.liveShare.guest.ctx.project", { defaultValue: "Project" })}>
          <Row label={t("chat.liveShare.guest.ctx.name", { defaultValue: "Name" })}>{project.name}</Row>
          <Row label={t("chat.liveShare.guest.ctx.path", { defaultValue: "Host path" })}>
            <code className="text-[11.5px]">{project.path}</code>
          </Row>
          {project.stack.length ? (
            <Row label={t("chat.liveShare.guest.ctx.stack", { defaultValue: "Stack" })}>
              <span className="flex flex-wrap gap-1">
                {project.stack.map((item) => (
                  <span key={item} className="rounded bg-muted px-1.5 py-0.5 text-[11px]">{item}</span>
                ))}
              </span>
            </Row>
          ) : null}
          {project.instructions.length ? (
            <Row label={t("chat.liveShare.guest.ctx.instructions", { defaultValue: "Instructions" })}>{project.instructions.join(", ")}</Row>
          ) : null}
          {project.readme ? (
            <details className="rounded-md bg-muted/40 px-2 py-1.5">
              <summary className="cursor-pointer text-muted-foreground">README</summary>
              <pre className="mt-1.5 max-h-56 overflow-auto whitespace-pre-wrap text-[11.5px] text-foreground/85">{project.readme}</pre>
            </details>
          ) : null}
        </Section>
      ) : null}

      {commands.length ? (
        <Section id="commands" icon={<Terminal className="h-3.5 w-3.5" aria-hidden />} title={t("chat.liveShare.guest.ctx.commands", { defaultValue: "Commands" })} count={commands.length}>
          <ul className="space-y-1">
            {commands.map(([name, cmd]) => (
              <li key={name}>
                <code className="text-[11.5px] text-foreground">{name}</code>
                {cmd ? <span className="block truncate text-[11.5px] text-muted-foreground" title={cmd}>{cmd}</span> : null}
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      {git ? (
        <>
          <Section id="git" icon={<GitBranch className="h-3.5 w-3.5" aria-hidden />} title="Git">
            <Row label={t("chat.liveShare.guest.ctx.branch", { defaultValue: "Branch" })}>
              <code className="text-[11.5px]">{git.detached ? `(detached) ${git.head}` : git.branch}</code>
              <span className="ms-1.5 text-muted-foreground">{git.head}</span>
            </Row>
            {git.upstream ? (
              <Row label={t("chat.liveShare.guest.ctx.upstream", { defaultValue: "Upstream" })}>
                {git.upstream}
                <span className="ms-1.5 text-muted-foreground">
                  {t("chat.liveShare.guest.ctx.aheadBehind", { defaultValue: "↑{{ahead}} ↓{{behind}}", ahead: git.ahead, behind: git.behind })}
                </span>
              </Row>
            ) : null}
            {git.remote ? (
              <Row label={t("chat.liveShare.guest.ctx.remote", { defaultValue: "Remote" })}>
                <span className="text-[11.5px]">{git.remote}</span>
              </Row>
            ) : null}
            <Row label={t("chat.liveShare.guest.ctx.uncommitted", { defaultValue: "Uncommitted" })}>
              {t("chat.liveShare.guest.ctx.changesSummary", {
                defaultValue: "{{count}} files",
                count: git.changes_total,
              })}
              <span className="ms-1.5 text-emerald-600 dark:text-emerald-300">+{git.insertions}</span>
              <span className="ms-1 text-rose-600 dark:text-rose-300">-{git.deletions}</span>
              {git.stashes ? <span className="ms-1.5 text-muted-foreground">· {git.stashes} stash</span> : null}
            </Row>
            {git.branches.length > 1 ? (
              <Row label={t("chat.liveShare.guest.ctx.branches", { defaultValue: "Branches" })}>
                <span className="text-[11.5px]">{git.branches.join(", ")}</span>
              </Row>
            ) : null}
          </Section>

          {git.changes.length ? (
            <Section id="changes" icon={<FileCode2 className="h-3.5 w-3.5" aria-hidden />} title={t("chat.liveShare.guest.ctx.changes", { defaultValue: "Pending changes" })} count={git.changes_total}>
              <ul className="space-y-0.5">
                {git.changes.map((change) => (
                  <li key={change.path} className="flex gap-2">
                    <code className={cn("w-6 shrink-0 text-[11px]", STATUS_TONE[change.status] ?? "text-muted-foreground")}>{change.status}</code>
                    <span className="min-w-0 flex-1 truncate text-[12px]" title={change.path}>{change.path}</span>
                  </li>
                ))}
              </ul>
            </Section>
          ) : null}

          {git.commits.length ? (
            <Section id="commits" icon={<GitCommitHorizontal className="h-3.5 w-3.5" aria-hidden />} title={t("chat.liveShare.guest.ctx.commits", { defaultValue: "Commits" })} count={git.commits.length}>
              <ol className="space-y-1.5">
                {git.commits.map((commit) => (
                  <li key={commit.sha}>
                    <p className="line-clamp-2">{commit.subject}</p>
                    <p className="text-[11px] text-muted-foreground">
                      <code>{commit.sha}</code> · {commit.author} · {when(commit.date)}
                    </p>
                  </li>
                ))}
              </ol>
            </Section>
          ) : null}
        </>
      ) : null}

      {conv?.files_touched.length ? (
        <Section id="files" icon={<FileCode2 className="h-3.5 w-3.5" aria-hidden />} title={t("chat.liveShare.guest.ctx.filesTouched", { defaultValue: "Edited in this session" })} count={conv.files_touched.length}>
          <ul className="space-y-0.5">
            {conv.files_touched.map((path) => (
              <li key={path} className="truncate text-[12px]" title={path}>{path}</li>
            ))}
          </ul>
        </Section>
      ) : null}
    </aside>
  );
}
