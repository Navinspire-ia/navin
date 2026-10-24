// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { Cpu, Square } from "lucide-react";
import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";

import type { ComposerTurnMode } from "@/components/thread/ComposerModeMenu";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import type { ShareContext } from "./relay-socket";

/**
 * The modes a driving guest may pick. Each one goes out as the same slash
 * prefix the host composer uses, and the host only lets those through
 * (navin/share/host.py SAFE_SLASH); Montage is host-only.
 */
export const GUEST_MODES: readonly ComposerTurnMode[] = ["ask", "plan", "agent", "review", "security", "debug", "reverse"];

const MODE_KEY = "navin.guest.mode";

export function useGuestMode(): [ComposerTurnMode, (mode: ComposerTurnMode) => void] {
  const [mode, setMode] = useState<ComposerTurnMode>(() => {
    try {
      const saved = window.localStorage.getItem(MODE_KEY) as ComposerTurnMode | null;
      if (saved && GUEST_MODES.includes(saved)) return saved;
    } catch {
      // Private mode: the default.
    }
    return "agent";
  });
  const choose = useCallback((next: ComposerTurnMode) => {
    setMode(next);
    try {
      window.localStorage.setItem(MODE_KEY, next);
    } catch {
      // Kept for this visit only.
    }
  }, []);
  return [mode, choose];
}

function shortModel(model: string): string {
  return model.split("/").pop() || model;
}

function compactTokens(tokens: number): string {
  if (tokens >= 1_000_000) return `${(tokens / 1_000_000).toFixed(tokens % 1_000_000 ? 1 : 0)}M`;
  if (tokens >= 1_000) return `${Math.round(tokens / 1_000)}k`;
  return String(tokens);
}

/** Model, context fill, turn mode and stop, right above the guest composer. */
export function GuestControlBar({
  usage,
  canDrive,
  streaming,
  mode,
  onMode,
  onStop,
}: {
  usage: ShareContext["usage"];
  canDrive: boolean;
  streaming: boolean;
  mode: ComposerTurnMode;
  onMode: (mode: ComposerTurnMode) => void;
  onStop: () => void;
}) {
  const { t } = useTranslation();
  const percent = Math.max(0, Math.min(100, usage?.percent ?? 0));
  const meterTone = percent >= 85 ? "bg-red-500" : percent >= 60 ? "bg-amber-500" : "bg-emerald-500";
  return (
    <div className="mx-auto mb-2 flex w-full max-w-3xl flex-wrap items-center gap-x-3 gap-y-1.5 text-[12px] text-muted-foreground">
      {usage?.model ? (
        <span className="flex min-w-0 items-center gap-1.5" title={usage.model}>
          <Cpu className="h-3.5 w-3.5 shrink-0" aria-hidden />
          <span className="truncate font-medium text-foreground/85">{shortModel(usage.model)}</span>
        </span>
      ) : null}
      {usage && usage.context_window > 0 ? (
        <span
          className="flex items-center gap-1.5"
          title={t("chat.liveShare.guest.control.contextTitle", {
            defaultValue: "Context: {{used}} of {{window}} tokens",
            used: usage.tokens.toLocaleString(),
            window: usage.context_window.toLocaleString(),
          })}
        >
          <span
            className="relative h-1.5 w-16 overflow-hidden rounded-full bg-muted"
            role="meter"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={percent}
            aria-label={t("chat.liveShare.guest.control.context", { defaultValue: "Context" })}
          >
            <span className={cn("absolute inset-y-0 start-0 rounded-full", meterTone)} style={{ width: `${percent}%` }} />
          </span>
          <span className="tabular-nums">
            {compactTokens(usage.tokens)} / {compactTokens(usage.context_window)} ({percent}%)
          </span>
        </span>
      ) : null}
      {canDrive ? (
        <div
          className="ms-auto flex flex-wrap items-center gap-0.5 rounded-md border border-border/70 p-0.5"
          role="radiogroup"
          aria-label={t("thread.composer.mode.aria", { defaultValue: "Turn mode" })}
        >
          {GUEST_MODES.map((option) => (
            <button
              key={option}
              type="button"
              role="radio"
              aria-checked={mode === option}
              title={t(`thread.composer.mode.${option}Help`, { defaultValue: "" }) || undefined}
              className={cn(
                "rounded px-2 py-0.5 transition-colors",
                mode === option ? "bg-foreground text-background" : "hover:text-foreground",
              )}
              onClick={() => onMode(option)}
            >
              {t(`thread.composer.mode.${option}`, { defaultValue: option })}
            </button>
          ))}
        </div>
      ) : null}
      {streaming && canDrive ? (
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="h-7 gap-1.5 border-red-300/70 px-2 text-red-700 hover:bg-red-50 dark:border-red-400/30 dark:text-red-300 dark:hover:bg-red-500/10"
          onClick={onStop}
        >
          <Square className="h-3 w-3 fill-current" aria-hidden />
          {t("chat.liveShare.guest.control.stopAgent", { defaultValue: "Stop the agent" })}
        </Button>
      ) : null}
    </div>
  );
}
