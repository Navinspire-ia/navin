// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { AlertCircle, AlertTriangle, X } from "lucide-react";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import type { FileDiagnostic, FileDiagnosticsPayload } from "@/lib/types";
import { cn } from "@/lib/utils";

type ProblemRow = FileDiagnostic & { path: string };

/**
 * Aggregated Problems panel (Cursor/VS Code style).
 * Includes workspace-mode LSP diagnostics for files that are not open.
 * Clicking a row jumps to the file + line via `onOpen`.
 */
export function DevProblemsPanel({
  diagnosticsByPath,
  onOpen,
  onClose,
}: {
  diagnosticsByPath: Record<string, FileDiagnosticsPayload>;
  onOpen: (path: string, line: number) => void;
  onClose?: () => void;
}) {
  const { t } = useTranslation();
  const rows = useMemo(() => {
    const out: ProblemRow[] = [];
    for (const [path, payload] of Object.entries(diagnosticsByPath)) {
      for (const row of payload.diagnostics ?? []) {
        out.push({ ...row, path });
      }
    }
    out.sort((a, b) => {
      const sev = (s: string) => (s === "error" ? 0 : s === "warning" ? 1 : 2);
      return sev(a.severity) - sev(b.severity) || a.path.localeCompare(b.path) || a.line - b.line;
    });
    return out;
  }, [diagnosticsByPath]);

  const errors = rows.filter((r) => r.severity === "error").length;
  const warnings = rows.filter((r) => r.severity === "warning").length;

  return (
    <div className="flex h-full min-h-0 flex-col border-t border-border/60 bg-background">
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-border/50 px-2 py-1">
        <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
          {t("dev.problems.title", { defaultValue: "Problems" })}
          <span className="ml-2 font-normal normal-case tabular-nums text-muted-foreground/80">
            {errors} {t("dev.problems.errors", { defaultValue: "errors" })}
            {" · "}
            {warnings} {t("dev.problems.warnings", { defaultValue: "warnings" })}
          </span>
        </span>
        {onClose ? (
          <button
            type="button"
            onClick={onClose}
            className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
            aria-label={t("dev.problems.close", { defaultValue: "Close problems" })}
          >
            <X className="h-3.5 w-3.5" aria-hidden />
          </button>
        ) : null}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {rows.length === 0 ? (
          <p className="px-3 py-3 text-[12px] text-muted-foreground">
            {t("dev.problems.empty", {
              defaultValue: "No problems in the workspace.",
            })}
          </p>
        ) : (
          rows.map((row, index) => {
            const isError = row.severity === "error";
            return (
              <button
                key={`${row.path}:${row.line}:${row.col}:${index}`}
                type="button"
                onClick={() => onOpen(row.path, row.line)}
                className="flex w-full cursor-pointer items-start gap-2 px-2 py-1.5 text-left transition-colors hover:bg-muted/60"
              >
                {isError ? (
                  <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-destructive" aria-hidden />
                ) : (
                  <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-500" aria-hidden />
                )}
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[12px] text-foreground">
                    {row.code ? `${row.code}: ` : ""}
                    {row.message}
                  </span>
                  <span
                    className={cn(
                      "block truncate font-mono text-[10.5px] text-muted-foreground tabular-nums",
                    )}
                  >
                    {row.path}:{row.line}
                    {row.col ? `:${row.col}` : ""}
                  </span>
                </span>
              </button>
            );
          })
        )}
      </div>
    </div>
  );
}
