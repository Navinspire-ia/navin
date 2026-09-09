// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";

export function formatEta(seconds: number | undefined): string | null {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return null;
  const total = Math.round(seconds);
  if (total < 60) return `${total}s`;
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  return rest ? `${minutes}m ${rest}s` : `${minutes}m`;
}

/**
 * Compact fill bar used by ShellRunCard and the chat/workbench strip.
 * Determinate when ``percent`` is set; otherwise a sliding indeterminate pulse.
 */
export function TaskProgressBar({
  percent,
  indeterminate = false,
  etaSeconds,
  label,
  className,
  compact = false,
}: {
  percent?: number;
  indeterminate?: boolean;
  etaSeconds?: number;
  label?: string;
  className?: string;
  compact?: boolean;
}) {
  const { t } = useTranslation();
  const hasPercent = percent != null && Number.isFinite(percent);
  const width = hasPercent ? Math.max(0, Math.min(100, percent)) : 0;
  const showIndeterminate = !hasPercent || indeterminate;
  const eta = formatEta(etaSeconds);

  return (
    <div
      className={cn("min-w-0", className)}
      data-testid="task-progress-bar"
      aria-label={label || t("thread.progress.working", { defaultValue: "Working…" })}
    >
      {(label || eta) && !compact ? (
        <div className="mb-1 flex min-w-0 items-center justify-between gap-2 text-[10.5px] text-muted-foreground/75">
          {label ? <span className="min-w-0 truncate">{label}</span> : <span />}
          {eta ? (
            <span className="shrink-0 tabular-nums">
              {t("thread.progress.eta", { defaultValue: "ETA {{time}}", time: eta })}
            </span>
          ) : null}
        </div>
      ) : null}
      <div
        className={cn(
          "relative overflow-hidden rounded-full bg-muted/60",
          compact ? "h-1" : "h-1.5",
        )}
      >
        {showIndeterminate ? (
          <div
            className="absolute inset-y-0 w-1/3 animate-[task-progress-slide_1.2s_ease-in-out_infinite] rounded-full bg-sky-500/80"
            aria-hidden
          />
        ) : (
          <div
            className="h-full rounded-full bg-sky-500/85 transition-[width] duration-300 ease-out"
            style={{ width: `${width}%` }}
            aria-hidden
          />
        )}
      </div>
      {compact && (label || eta) ? (
        <div className="mt-0.5 flex min-w-0 items-center justify-between gap-2 text-[10px] text-muted-foreground/70">
          {label ? <span className="min-w-0 truncate">{label}</span> : <span />}
          <span className="shrink-0 tabular-nums">
            {hasPercent && !showIndeterminate ? `${Math.round(width)}%` : null}
            {hasPercent && !showIndeterminate && eta ? " · " : null}
            {eta
              ? t("thread.progress.eta", { defaultValue: "ETA {{time}}", time: eta })
              : null}
          </span>
        </div>
      ) : null}
    </div>
  );
}
