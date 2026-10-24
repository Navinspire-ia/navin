// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { Fragment, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { cleanTerminalText, hasRunOutputNote, type RunOutput } from "@/lib/ansi";
import { redactSecrets } from "@/lib/redact-secrets";
import { cn } from "@/lib/utils";

/** Colour for a coverage / progress percentage: green when high, amber, red when low. */
export function percentTone(value: number): string {
  if (value >= 80) return "text-emerald-400 font-semibold";
  if (value >= 50) return "text-amber-300 font-semibold";
  return "text-rose-400 font-semibold";
}

const TOKEN =
  /(\b\d{1,3}(?:\.\d+)?%)|(\b\d+\s+(?:passed|pass|ok)\b|\bPASS(?:ED)?\b|\u2713|\u2714)|(\b\d+\s+(?:failed|errors?)\b|\bFAIL(?:ED|URE)?\b|\bERROR\b|\bAssertionError\b|\u2717|\u2718|\u00d7)|(\b\d+\s+(?:skipped|xfailed|warnings?|todo)\b|\bSKIP(?:PED)?\b|\bWARN(?:ING)?\b)/g;

const LINE_TONE: Array<[RegExp, string]> = [
  [/^\s*(=+|-+|_+).*(=+|-+|_+)\s*$/, "text-slate-500"],
  [/^\s*(FAILED|ERROR)\b|^E\s{2,}/, "text-rose-300/90"],
  [/^\s*(Test Files|Tests|Duration|Start at)\b/, "text-slate-100"],
];

function highlightLine(line: string, lineKey: number): ReactNode {
  const parts: ReactNode[] = [];
  let last = 0;
  for (const match of line.matchAll(TOKEN)) {
    const start = match.index ?? 0;
    if (start > last) parts.push(line.slice(last, start));
    const [text, pct, ok, bad] = match;
    const tone = pct
      ? percentTone(Number.parseFloat(pct))
      : ok
        ? "text-emerald-400 font-semibold"
        : bad
          ? "text-rose-400 font-semibold"
          : "text-amber-300";
    parts.push(<span key={`${lineKey}-${start}`} className={tone} data-tone={pct ? "percent" : undefined}>{text}</span>);
    last = start + text.length;
  }
  if (last < line.length) parts.push(line.slice(last));
  const lineTone = LINE_TONE.find(([re]) => re.test(line))?.[1];
  return lineTone ? <span className={lineTone}>{parts}</span> : parts;
}

/** Shell / test output with results and percentages coloured for quick reading. */
export function HighlightedOutput({ text }: { text: string }) {
  const lines = redactSecrets(cleanTerminalText(text)).split("\n");
  return (
    <>
      {lines.map((line, i) => (
        <Fragment key={i}>
          {highlightLine(line, i)}
          {i < lines.length - 1 ? "\n" : null}
        </Fragment>
      ))}
    </>
  );
}

/**
 * One quiet line under a command's output for what the runtime said about it:
 * a failing exit code, and that a long output was shortened (the full copy's
 * path on hover). Nothing when the command succeeded in full.
 */
export function RunOutputFooter({
  run,
  variant = "terminal",
  className,
}: {
  run: RunOutput;
  variant?: "terminal" | "plain";
  className?: string;
}) {
  const { t, i18n } = useTranslation();
  if (!hasRunOutputNote(run)) return null;
  const failed = run.exitCode !== null && run.exitCode !== 0;
  const terminal = variant === "terminal";
  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[12px]",
        terminal ? "px-2.5 py-1.5 text-slate-400/80" : "pt-1 text-foreground/55",
        className,
      )}
      data-testid="run-output-footer"
      data-exit-code={run.exitCode ?? undefined}
    >
      {failed ? (
        <span className={cn("font-medium", terminal ? "text-rose-300/90" : "text-rose-600 dark:text-rose-300")}>
          {t("message.runOutputExitCode", { code: run.exitCode, defaultValue: "Exit code {{code}}" })}
        </span>
      ) : null}
      {run.hiddenChars !== null ? (
        <span
          title={run.fullOutputPath
            ? t("message.runOutputFullPath", { path: run.fullOutputPath, defaultValue: "Full output: {{path}}" })
            : undefined}
        >
          {t("message.runOutputShortened", {
            chars: run.hiddenChars.toLocaleString(i18n?.language || undefined),
            defaultValue: "Output shortened: {{chars}} characters not shown",
          })}
        </span>
      ) : null}
    </div>
  );
}
