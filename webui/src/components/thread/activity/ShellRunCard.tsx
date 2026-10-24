// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  Loader2,
  ShieldCheck,
  ShieldOff,
  TerminalSquare,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { HighlightedOutput, RunOutputFooter } from "@/components/thread/activity/HighlightedOutput";
import { formatEta } from "@/components/thread/activity/TaskProgressBar";
import { openInspector } from "@/lib/activity-inspector";
import { hasRunOutputNote, tidyRunOutput } from "@/lib/ansi";
import { parseTestReport } from "@/lib/test-report";
import { cn } from "@/lib/utils";

export type ShellRunStatus = "running" | "done" | "error";

export interface ShellRunSummary {
  key: string;
  command: string;
  status: ShellRunStatus;
  output?: string;
  error?: string;
  startedAt?: number;
  endedAt?: number;
  percent?: number;
  indeterminate?: boolean;
  etaSeconds?: number;
  label?: string;
  /** OS sandbox backend the command ran in; undefined when unconfined. */
  sandbox?: string;
  /** The user approved running this one command outside the sandbox. */
  sandboxLifted?: boolean;
}

/**
 * Cursor-style "Sandboxed" marker for one command. Quiet when the command is
 * confined (that is the normal state), amber when the user lifted the sandbox
 * for it, nothing when no OS sandbox is configured.
 */
export function SandboxBadge({
  sandbox,
  lifted,
  compact = false,
  className,
}: {
  sandbox?: string;
  lifted?: boolean;
  compact?: boolean;
  className?: string;
}) {
  const { t } = useTranslation();
  if (!lifted && !sandbox) return null;
  const label = lifted
    ? t("message.activitySandboxLifted", { defaultValue: "Unsandboxed" })
    : t("message.activitySandboxOn", { defaultValue: "Sandboxed" });
  const hint = lifted
    ? t("message.activitySandboxLiftedHint", {
        defaultValue: "You approved running this command outside the sandbox: it could write anywhere on this machine.",
      })
    : t("message.activitySandboxOnHint", {
        defaultValue: "Ran in the OS sandbox: writes limited to the project and toolchain caches, reads and network open.",
      });
  const Icon = lifted ? ShieldOff : ShieldCheck;
  return (
    <span
      title={hint}
      aria-label={`${label}. ${hint}`}
      data-testid="activity-sandbox-badge"
      data-sandbox={lifted ? "lifted" : sandbox}
      className={cn(
        "inline-flex h-[19px] shrink-0 items-center gap-1 rounded-[5px] border px-1.5 align-middle",
        "text-[12px] font-medium leading-none tracking-[0.01em] antialiased",
        lifted
          ? "border-amber-500/35 bg-amber-500/10 text-amber-700 dark:text-amber-300"
          : "border-border/70 bg-muted/60 text-muted-foreground",
        compact && "px-1",
        className,
      )}
    >
      <Icon className="h-3 w-3" aria-hidden />
      {compact ? null : <span>{label}</span>}
    </span>
  );
}

function formatDuration(ms: number): string {
  const seconds = ms > 0 && ms < 1000 ? 1 : Math.max(0, Math.round(ms / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return rest ? `${minutes}m ${rest}s` : `${minutes}m`;
}

function commandPreview(command: string): { first: string; extraLines: number } {
  const lines = command
    .replace(/\r\n/g, "\n")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  return { first: lines[0] ?? "", extraLines: Math.max(0, lines.length - 1) };
}

/**
 * Cursor-style terminal card for one shell command: header with the command,
 * live elapsed time while running, then the captured output.
 */
export function ShellRunCard({
  run,
  active,
  hideCommand = false,
}: {
  run: ShellRunSummary;
  active: boolean;
  /** Under a journal row that already names the command: no header line,
   *  only the full script (when it spans lines) and the output. */
  hideCommand?: boolean;
}) {
  const { t } = useTranslation();
  const running = active && run.status === "running";
  const failed = run.status === "error";
  const [expanded, setExpanded] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const outputRef = useRef<HTMLPreElement | null>(null);

  useEffect(() => {
    if (!running) return undefined;
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [running]);

  const durationMs = running
    ? run.startedAt !== undefined
      ? Math.max(0, now - run.startedAt)
      : undefined
    : run.startedAt !== undefined && run.endedAt !== undefined
      ? Math.max(0, run.endedAt - run.startedAt)
      : undefined;

  const rawOutput = (failed ? run.error || run.output : run.output) ?? "";
  // What the command printed; the runtime's "Exit code" / "[compacted ...]"
  // lines become the footer instead of reading as output.
  const tidy = useMemo(() => tidyRunOutput(rawOutput), [rawOutput]);
  const output = tidy.body;
  const hasNote = hasRunOutputNote(tidy);

  useEffect(() => {
    const el = outputRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [output, expanded]);

  const { first, extraLines } = useMemo(() => commandPreview(run.command), [run.command]);
  const hasBody = output.length > 0 || running || hasNote;
  const etaLabel = formatEta(run.etaSeconds);

  if (hideCommand) {
    return (
      <div
        className="min-w-0 overflow-hidden rounded-lg border border-border/60 bg-[#101318] shadow-sm"
        data-testid="activity-shell-run"
        data-variant="body"
      >
        {extraLines > 0 ? (
          <pre dir="ltr"
            className="max-h-48 overflow-auto whitespace-pre-wrap break-words border-b border-white/10 px-2.5 py-2 font-mono text-[13px] leading-[1.55] text-slate-400/90"
            data-testid="activity-shell-script"
          >
            {run.command}
          </pre>
        ) : null}
        {output ? (
          <pre dir="ltr"
            ref={outputRef}
            className="max-h-[26rem] overflow-auto whitespace-pre-wrap break-words px-2.5 py-2 font-mono text-[13px] leading-[1.55] text-slate-200/90 scrollbar-thin scrollbar-track-transparent"
          >
            <HighlightedOutput text={output} />
          </pre>
        ) : hasNote ? null : (
          <p className="px-2.5 py-2 font-mono text-[13px] text-slate-400/80">
            {running ? (
              <span className="animate-pulse">
                {t("message.shellOutputPending", { defaultValue: "Command running…" })}
              </span>
            ) : (
              t("message.shellRunNoOutput", { defaultValue: "No output" })
            )}
          </p>
        )}
        <RunOutputFooter run={tidy} />
        {!running ? <InspectButton run={run} durationMs={durationMs} /> : null}
      </div>
    );
  }

  return (
    <div
      className={cn(
        "min-w-0 overflow-hidden rounded-lg border border-border/60 bg-background/80 shadow-sm",
      )}
      data-testid="activity-shell-run"
    >
      <button
        type="button"
        onClick={() => setExpanded((open) => !open)}
        className="flex w-full min-w-0 items-center gap-2 px-2.5 py-1.5 text-start transition-colors hover:bg-muted/40"
        title={run.command}
        aria-expanded={expanded}
      >
        <TerminalSquare
          className="h-3.5 w-3.5 shrink-0 text-muted-foreground/70"
          aria-hidden
        />
        <SandboxBadge sandbox={run.sandbox} lifted={run.sandboxLifted} />
        <span dir="ltr" className="min-w-0 flex-1 truncate text-start font-mono text-[14px] text-foreground/90">
          {first}
          {extraLines > 0 ? (
            <span className="text-muted-foreground/55"> +{extraLines}</span>
          ) : null}
        </span>
        <span className="flex shrink-0 items-center gap-1.5 text-[12px] text-muted-foreground/70">
          {running ? (
            <>
              <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
              {durationMs !== undefined ? formatDuration(durationMs) : t("message.shellRunRunning", { defaultValue: "Running" })}
              {run.percent != null && !run.indeterminate ? (
                <span className="tabular-nums">{Math.round(run.percent)}%</span>
              ) : null}
              {etaLabel ? <span className="tabular-nums">ETA {etaLabel}</span> : null}
            </>
          ) : failed ? (
            <>
              <AlertCircle className="h-3 w-3 text-muted-foreground/60" aria-hidden />
              <span className="text-muted-foreground/70">
                {t("message.toolStepAdjusted", { defaultValue: "Adjusted and continued" })}
              </span>
              {durationMs !== undefined ? formatDuration(durationMs) : null}
            </>
          ) : (
            <>
              <CheckCircle2 className="h-3 w-3 text-emerald-500/80" aria-hidden />
              {durationMs !== undefined ? formatDuration(durationMs) : null}
            </>
          )}
          {hasBody ? (
            <ChevronDown
              className={cn(
                "h-3 w-3 transition-transform duration-200",
                expanded && "rotate-180",
              )}
              aria-hidden
            />
          ) : null}
        </span>
      </button>
      {hasBody ? (
        <div className="border-t border-border/50 bg-[#101318]">
          {output ? (
            <pre dir="ltr"
              ref={outputRef}
              className={cn(
                "overflow-auto whitespace-pre-wrap break-words px-2.5 py-2 font-mono text-[14px] leading-[1.55] text-slate-200/90 scrollbar-thin scrollbar-track-transparent",
                expanded ? "max-h-[26rem]" : "max-h-36",
              )}
            >
              <HighlightedOutput text={output} />
            </pre>
          ) : running ? (
            <p className="px-2.5 py-2 font-mono text-[14px] text-slate-400/80">
              <span className="animate-pulse">
                {t("message.shellOutputPending", { defaultValue: "Command running…" })}
              </span>
            </p>
          ) : null}
          <RunOutputFooter run={tidy} />
          {!running ? <InspectButton run={run} durationMs={durationMs} /> : null}
        </div>
      ) : null}
    </div>
  );
}

function InspectButton({ run, durationMs }: { run: ShellRunSummary; durationMs?: number }) {
  const { t } = useTranslation();
  const report = parseTestReport(run.output ?? run.error ?? "");
  return (
    <div className="flex justify-end border-t border-white/10 px-2 py-1">
      <button
        type="button"
        data-testid="activity-shell-inspect"
        onClick={() =>
          openInspector({
            kind: "command",
            id: run.key,
            command: run.command,
            output: [run.output, run.error].filter(Boolean).join("\n"),
            status: run.status === "error" ? "error" : run.status === "running" ? "running" : "done",
            durationMs,
          })
        }
        className="rounded px-1.5 py-0.5 text-[11px] font-medium text-slate-400 transition-colors hover:bg-white/10 hover:text-slate-100"
      >
        {report
          ? t("inspector.openTests", {
              passed: report.passed,
              failed: report.failed,
              defaultValue: "Test report ({{passed}} ok, {{failed}} failed)",
            })
          : t("inspector.open", { defaultValue: "Inspect" })}
      </button>
    </div>
  );
}
