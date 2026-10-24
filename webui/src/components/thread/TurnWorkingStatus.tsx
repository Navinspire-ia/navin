// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { memo, useEffect, useRef, useState } from "react";
import { motion, useReducedMotion } from "framer-motion";
import { Loader2, MessageCircleQuestion, ShieldQuestion } from "lucide-react";
import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";

function formatTurnElapsed(milliseconds: number): string {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000));
  const minutes = Math.floor(seconds / 60);
  if (minutes >= 60) return `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, "0")}m`;
  if (minutes) return `${minutes}m ${seconds % 60}s`;
  return `${seconds}s`;
}

export type TurnWaitingReason = "approval" | "choice";

/** The one live status of a turn: what the agent is doing now and for how long.
 *  It stays visible between answer segments, tool calls and reconnects. */
export const TurnWorkingStatus = memo(function TurnWorkingStatus({
  active,
  startedAt,
  activityText,
  thinking = false,
  waiting = null,
  compacting = false,
  wide = false,
}: {
  active: boolean;
  startedAt: number | null;
  /** The running step ("Running python3 x.py"); wins over the generic label. */
  activityText?: string | null;
  /** The model is streaming reasoning right now. */
  thinking?: boolean;
  /** The turn is blocked on the user (an open approval or ask_user question).
   *  Wins over every other label and pauses the clock. */
  waiting?: TurnWaitingReason | null;
  /** The context is being summarized: said in clear beside "Working". */
  compacting?: boolean;
  wide?: boolean;
}) {
  const { t } = useTranslation();
  const reduced = useReducedMotion();
  const [now, setNow] = useState(Date.now);
  const fallbackStart = useRef<number | null>(null);
  const serverStart = typeof startedAt === "number" && Number.isFinite(startedAt) && startedAt > 0
    ? startedAt * 1000 : null;
  const running = active || serverStart !== null;
  const isWaiting = running && waiting !== null;
  /** Time spent waiting on the user, excluded from the clock. */
  const pausedMsRef = useRef(0);
  const waitStartRef = useRef<number | null>(null);
  const clockKeyRef = useRef<number | null>(null);

  useEffect(() => {
    if (!running) {
      fallbackStart.current = null;
      clockKeyRef.current = null;
      pausedMsRef.current = 0;
      waitStartRef.current = null;
      return;
    }
    fallbackStart.current ??= Date.now();
    const key = serverStart ?? fallbackStart.current;
    if (clockKeyRef.current !== key) {
      // A new turn (or the server's real start arrived): forget older pauses.
      clockKeyRef.current = key;
      pausedMsRef.current = 0;
      waitStartRef.current = isWaiting ? Date.now() : null;
    } else if (isWaiting && waitStartRef.current === null) {
      waitStartRef.current = Date.now();
    } else if (!isWaiting && waitStartRef.current !== null) {
      pausedMsRef.current += Math.max(0, Date.now() - waitStartRef.current);
      waitStartRef.current = null;
    }
    setNow(Date.now());
    // The clock is frozen while waiting: no need to tick.
    if (isWaiting) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [running, serverStart, isWaiting]);

  if (!running) return null;
  const start = serverStart ?? fallbackStart.current ?? now;
  // Pauses recorded for an older start (a new turn not yet committed by the
  // effect) do not apply; a wait that just ended still counts until then.
  const sameTurn = clockKeyRef.current !== null && clockKeyRef.current === (serverStart ?? fallbackStart.current);
  const paused = sameTurn ? pausedMsRef.current : 0;
  const waitStart = sameTurn ? waitStartRef.current : null;
  const elapsed = now - start - paused - (waitStart !== null ? Math.max(0, now - waitStart) : 0);
  const duration = formatTurnElapsed(elapsed);
  const isCompacting = compacting && !isWaiting;
  const compactingLabel = t("thread.liveActivity.compacting", {
    defaultValue: "Compacting context…",
  });
  const step = isCompacting ? "" : activityText?.trim() || "";
  const label = isWaiting
    ? waiting === "approval"
      ? t("thread.liveActivity.waitingForApproval", { defaultValue: "Waiting for your approval" })
      : t("thread.liveActivity.waitingForAnswer", { defaultValue: "Waiting for your answer" })
    : thinking
      ? t("thread.assistantThinking", { defaultValue: "Working…" })
      : step || t("message.activityGroupWorking", { defaultValue: "Working" });
  const WaitingIcon = waiting === "approval" ? ShieldQuestion : MessageCircleQuestion;

  return (
    <motion.div
      data-testid="turn-working-status"
      role="status"
      aria-label={label}
      initial={false}
      animate={{ opacity: 1 }}
      transition={{ duration: reduced ? 0 : 0.12 }}
      className={cn(
        "mx-auto mb-1.5 flex min-h-8 w-full min-w-0 items-center gap-2 px-3 text-[14.5px] leading-6 text-muted-foreground",
        wide ? "max-w-[58rem]" : "max-w-[49.5rem]",
      )}
    >
      {isWaiting ? (
        <WaitingIcon
          aria-hidden
          data-testid="turn-waiting-icon"
          className="h-4 w-4 shrink-0 text-primary/70"
        />
      ) : (
        <Loader2
          aria-hidden
          className={cn("h-4 w-4 shrink-0 text-primary/70", reduced ? "" : "animate-spin")}
        />
      )}
      {!isWaiting && !thinking && step ? (
        <span
          data-testid="turn-activity-text"
          title={step}
          className="min-w-0 truncate"
        >{step}</span>
      ) : (
        <span className="min-w-0 truncate">{label}</span>
      )}
      {isCompacting ? (
        <span
          data-testid="turn-compacting"
          className="shrink-0 rounded-full bg-primary/10 px-2 py-0.5 text-[12.5px] leading-4 text-primary"
        >{compactingLabel}</span>
      ) : null}
      <span
        role="timer"
        aria-live="off"
        className="shrink-0 text-[14px] tabular-nums text-muted-foreground/75"
      >{duration}</span>
    </motion.div>
  );
});
