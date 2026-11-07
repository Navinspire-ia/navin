// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { fetchWebuiPrompts, type SessionPlan, type SessionPlanItem } from "@/lib/api";
import { fetchBoardShared } from "@/lib/board-fetch";
import type { WebuiPromptEntry } from "@/lib/types";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";

const badge = "inline-flex shrink-0 rounded-full px-2 py-0.5 text-xs font-medium";
const tones = {
  done: "bg-green-500/15 text-green-700 dark:text-green-400",
  unfinished: "bg-orange-500/15 text-orange-700 dark:text-orange-400",
  error: "bg-red-500/15 text-red-700 dark:text-red-400",
  active: "bg-blue-500/15 text-blue-700 dark:text-blue-400",
  blocked: "bg-red-500/15 text-red-700 dark:text-red-400",
  pending: "bg-muted text-muted-foreground",
};

type DialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  sessionKey: string | null;
  token: string;
};

export function ChatHistoryDialog({ open, onOpenChange, sessionKey, token }: DialogProps) {
  const { t, i18n } = useTranslation();
  const [prompts, setPrompts] = useState<WebuiPromptEntry[]>([]);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [expanded, setExpanded] = useState<number[]>([]);

  useEffect(() => {
    if (!open || !sessionKey) return;
    let active = true;
    setState("loading");
    setPrompts([]);
    setExpanded([]);
    fetchWebuiPrompts(token, sessionKey).then(
      (data) => { if (active) { setPrompts(data.prompts ?? []); setState("ready"); } },
      () => { if (active) setState("error"); },
    );
    return () => { active = false; };
  }, [open, sessionKey, token]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl" aria-describedby={undefined}>
        <DialogHeader><DialogTitle>{t("chat.history.title")}</DialogTitle></DialogHeader>
        {state === "loading" ? <p role="status">{t("chat.history.loading")}</p>
          : state === "error" ? <p role="alert">{t("chat.history.error")}</p>
          : prompts.length === 0 ? <p>{t("chat.history.empty")}</p>
          : <ol className="grid gap-3">
              {prompts.map((prompt) => {
                const long = prompt.text.length > 400;
                const showAll = expanded.includes(prompt.index);
                const status = prompt.status ?? "unfinished";
                const time = prompt.timestamp ?? prompt.created_at;
                return <li key={prompt.index} className="min-w-0 rounded-lg border p-3">
                  <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                    <time className="text-xs text-muted-foreground" dateTime={time != null ? new Date(time).toISOString() : undefined}>
                      {time != null ? new Intl.DateTimeFormat(i18n.language, { dateStyle: "medium", timeStyle: "short" }).format(time) : t("chat.history.unknownDate")}
                    </time>
                    <span className={`${badge} ${tones[status]}`}>{t(`chat.history.status.${status}`)}</span>
                  </div>
                  <p dir="auto" className="whitespace-pre-wrap break-words text-sm">{long && !showAll ? `${prompt.text.slice(0, 400)}...` : prompt.text}</p>
                  <div className="mt-2 flex gap-2">
                    {long ? <button type="button" className="text-xs text-primary hover:underline" onClick={() => setExpanded((current) => showAll ? current.filter((index) => index !== prompt.index) : [...current, prompt.index])}>{t(showAll ? "chat.history.showLess" : "chat.history.showMore")}</button> : null}
                    <button type="button" className="text-xs text-primary hover:underline" onClick={() => { void navigator.clipboard.writeText(prompt.text); }}>{t("chat.history.copy")}</button>
                  </div>
                </li>;
              })}
            </ol>}
      </DialogContent>
    </Dialog>
  );
}

export function ChatPlansDialog({ open, onOpenChange, sessionKey, token, onRelaunch }: DialogProps & { onRelaunch: (text: string) => void }) {
  const { t } = useTranslation();
  const [plan, setPlan] = useState<SessionPlan | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  useEffect(() => {
    if (!open || !sessionKey) return;
    let active = true;
    setState("loading");
    setPlan(null);
    fetchBoardShared(token, sessionKey).then(
      (board) => { if (active) { setPlan(board.session_plan ?? null); setState("ready"); } },
      () => { if (active) setState("error"); },
    );
    return () => { active = false; };
  }, [open, sessionKey, token]);

  const items = plan?.items ?? [];
  const pending = items.filter((item) => !item.done && !item.cancelled && item.status !== "cancelled" && item.status !== "done");
  const completed = items.filter((item) => item.done || item.status === "done").length;
  const relaunch = (tasks: SessionPlanItem[]) => {
    const instruction = tasks.map((task) => `Continue board task ${task.id}: ${task.title}`).join("\n");
    onRelaunch(instruction);
    onOpenChange(false);
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl" aria-describedby={undefined}>
        <DialogHeader><DialogTitle>{t("chat.plans.title")}</DialogTitle></DialogHeader>
        {state === "loading" ? <p role="status">{t("chat.plans.loading")}</p>
          : state === "error" ? <p role="alert">{t("chat.plans.error")}</p>
          : !items.length ? <p>{t("chat.plans.empty")}</p>
          : <>
              <div>
                <p className="text-sm font-medium">{t("chat.plans.progress", { done: completed, total: items.length })}</p>
                <div role="progressbar" aria-valuenow={completed} aria-valuemin={0} aria-valuemax={items.length} aria-label={t("chat.plans.progress", { done: completed, total: items.length })} className="mt-2 h-2 overflow-hidden rounded-full bg-muted">
                  <div className="h-full bg-green-600" style={{ width: `${completed / items.length * 100}%` }} />
                </div>
              </div>
              {pending.length > 1 ? <button type="button" className="rounded-md bg-primary px-3 py-2 text-sm text-primary-foreground" onClick={() => relaunch(pending)}>{t("chat.plans.relaunchAll")}</button> : null}
              <ol className="grid gap-3">{items.map((item) => {
                const tone = item.done || item.status === "done" ? "done" : item.blocked || item.status === "blocked" ? "blocked" : item.active || ["in_progress", "review", "audit", "fix"].includes(item.status) ? "active" : "pending";
                return <li key={item.id} className="min-w-0 rounded-lg border p-3">
                  <div className="flex flex-wrap items-start justify-between gap-2"><strong dir="auto" className="min-w-0 break-words text-sm">{item.title} <span className="text-muted-foreground">({item.id})</span></strong><span className={`${badge} ${tones[tone]}`}>{t(`chat.plans.status.${tone}`)}</span></div>
                  {item.description ? <p dir="auto" className="mt-1 whitespace-pre-wrap break-words text-sm text-muted-foreground">{item.description}</p> : null}
                  {pending.includes(item) ? <button type="button" className="mt-2 rounded-md border px-3 py-1.5 text-xs hover:bg-muted" onClick={() => relaunch([item])}>{t("chat.plans.relaunch")}</button> : null}
                </li>;
              })}</ol>
            </>}
      </DialogContent>
    </Dialog>
  );
}
