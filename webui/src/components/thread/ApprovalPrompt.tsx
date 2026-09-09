// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useEffect, useRef, useState } from "react";
import { ShieldQuestion } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { useClient } from "@/providers/ClientProvider";
import { cn } from "@/lib/utils";
import { secondsLeft, type PendingApproval } from "@/lib/approvals";
import type { ConnectionStatus } from "@/lib/types";

interface ApprovalPromptProps {
  request: PendingApproval;
  queue?: readonly PendingApproval[];
  onRespond: (requestId: string, allowed: boolean, remember?: boolean) => void;
}

/**
 * The one thing in the WebUI the agent is actually waiting on.
 *
 * A tool call is suspended while this is on screen, so the card is deliberately
 * unlike a notification: it sits above the composer where the next action would
 * be taken, it says what is at stake rather than what happened, and it cannot be
 * dismissed - the only ways out are an answer or the countdown running out. It is
 * an ``alertdialog`` and takes focus on the safe button, so the keyboard and a
 * screen reader both land on "refuse" rather than on "allow".
 */
export function ApprovalPrompt({ request, queue = [], onRespond }: ApprovalPromptProps) {
  const { t } = useTranslation();
  const { client } = useClient();
  const [answered, setAnswered] = useState(false);
  const [remaining, setRemaining] = useState(() => secondsLeft(request, Date.now()));
  // An answer sent while the socket is down is queued and delivered against a
  // request the backend has already timed out, so the buttons go quiet instead.
  const [status, setStatus] = useState<ConnectionStatus>(client.status);
  const refuseRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => client.onStatus(setStatus), [client]);
  const connected = status === "open";

  useEffect(() => {
    refuseRef.current?.focus();
  }, [request.requestId]);

  useEffect(() => {
    setAnswered(false);
    setRemaining(secondsLeft(request, Date.now()));
    if (request.expiresAt === null) return;
    const timer = setInterval(() => {
      setRemaining(secondsLeft(request, Date.now()));
    }, 1000);
    return () => clearInterval(timer);
  }, [request]);

  const busy = answered || !connected;
  const respond = (allowed: boolean, remember = false) => {
    if (busy) return;
    setAnswered(true);
    onRespond(request.requestId, allowed, remember);
  };

  return (
    <div
      role="alertdialog"
      aria-live="assertive"
      aria-labelledby={`approval-title-${request.requestId}`}
      className={cn(
        "mb-2 rounded-lg border border-amber-500/40 bg-amber-500/[0.07]",
        "px-3 py-2.5 text-[12px] leading-5",
        "animate-in fade-in-0 slide-in-from-bottom-1",
      )}
    >
      <div className="flex items-start gap-2">
        <ShieldQuestion className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" aria-hidden />
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-2">
            <p
              id={`approval-title-${request.requestId}`}
              className="font-medium text-foreground"
            >
              {request.action}
            </p>
            {queue.length > 1 ? (
              <span
                className="shrink-0 rounded-full border border-amber-500/35 px-2 py-0.5 text-[10px] font-semibold text-amber-700 dark:text-amber-300"
                aria-label={t("thread.approval.queuePosition", {
                  current: 1,
                  total: queue.length,
                })}
              >
                1/{queue.length}
              </span>
            ) : null}
          </div>
          <p className="mt-0.5 text-muted-foreground">{request.reason}</p>
          {request.detail ? (
            <pre className="mt-1.5 max-h-32 overflow-auto whitespace-pre-wrap break-words rounded-md bg-muted/60 px-2 py-1.5 font-mono text-[11px] leading-[18px] text-foreground/90">
              {request.detail}
            </pre>
          ) : null}
          {request.consequence ? (
            <p className="mt-1.5 text-amber-600 dark:text-amber-400">
              {request.consequence}
            </p>
          ) : null}
          {queue.length > 1 ? (
            <details className="mt-1.5 rounded-md border border-amber-500/20 bg-background/40 px-2 py-1">
              <summary className="cursor-pointer select-none text-[11px] font-medium text-muted-foreground">
                {t("thread.approval.queuedCount", { count: queue.length - 1 })}
              </summary>
              <ul className="mt-1 space-y-1" aria-label={t("thread.approval.queuedList")}>
                {queue.slice(1).map((queued) => (
                  <li
                    key={queued.requestId}
                    className="flex min-w-0 items-center justify-between gap-2 text-[11px]"
                  >
                    <span className="truncate text-foreground/85">{queued.action}</span>
                    <span className="shrink-0 font-mono text-muted-foreground">
                      {queued.tool}
                    </span>
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <Button size="sm" variant="outline" ref={refuseRef} disabled={busy}
              onClick={() => respond(false)}
            >
              {t("thread.approval.refuse")}
            </Button>
            <Button size="sm" disabled={busy} onClick={() => respond(true)}>
              {t("thread.approval.allow")}
            </Button>
            {request.rememberOffered ? (
              <Button
                size="sm"
                variant="secondary"
                disabled={busy}
                onClick={() => respond(true, true)}
                title={t("thread.approval.rememberHint")}
              >
                {t("thread.approval.allowAlways")}
              </Button>
            ) : null}
            <span className="ml-auto text-[11px] text-muted-foreground">
              {!connected
                ? t("thread.approval.offline")
                : remaining !== null
                  ? t("thread.approval.expiresIn", { seconds: remaining })
                  : t("thread.approval.waiting", { tool: request.tool })}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
