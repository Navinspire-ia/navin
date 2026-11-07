// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useEffect, useRef, useState } from "react";
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
  cancelled: "bg-orange-500/15 text-orange-700 dark:text-orange-400",
  pending: "bg-muted text-muted-foreground",
};

const COPY_FEEDBACK_MS = 2000;

/** Short one-line name of a prompt, used to tell repeated buttons apart. */
function promptLabel(text: string): string {
  const line = text.replace(/\s+/g, " ").trim();
  return line.length > 60 ? `${line.slice(0, 60)}...` : line;
}

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
  const [copy, setCopy] = useState<{ index: number; ok: boolean } | null>(null);
  const copyTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => { if (copyTimer.current) clearTimeout(copyTimer.current); }, []);

  const copyPrompt = (prompt: WebuiPromptEntry) => {
    const show = (ok: boolean) => {
      if (copyTimer.current) clearTimeout(copyTimer.current);
      setCopy({ index: prompt.index, ok });
      copyTimer.current = setTimeout(() => setCopy(null), COPY_FEEDBACK_MS);
    };
    try {
      navigator.clipboard.writeText(prompt.text).then(() => show(true), () => show(false));
    } catch {
      show(false);
    }
  };

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
                const title = promptLabel(prompt.text);
                const copied = copy?.index === prompt.index ? copy : null;
                return <li key={prompt.index} className="min-w-0 rounded-lg border p-3">
                  <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                    <time className="text-xs text-muted-foreground" dateTime={time != null ? new Date(time).toISOString() : undefined}>
                      {time != null ? new Intl.DateTimeFormat(i18n.language, { dateStyle: "medium", timeStyle: "short" }).format(time) : t("chat.history.unknownDate")}
                    </time>
                    <span className={`${badge} ${tones[status]}`}>{t(`chat.history.status.${status}`)}</span>
                  </div>
                  <p dir="auto" className="whitespace-pre-wrap break-words text-sm">{long && !showAll ? `${prompt.text.slice(0, 400)}...` : prompt.text}</p>
                  <div className="mt-2 flex items-center gap-2">
                    {long ? <button type="button" className="text-xs text-primary hover:underline" aria-expanded={showAll} aria-label={t(showAll ? "chat.history.showLessAria" : "chat.history.showMoreAria", { title })} onClick={() => setExpanded((current) => showAll ? current.filter((index) => index !== prompt.index) : [...current, prompt.index])}>{t(showAll ? "chat.history.showLess" : "chat.history.showMore")}</button> : null}
                    <button type="button" className="text-xs text-primary hover:underline" aria-label={t("chat.history.copyAria", { title })} onClick={() => copyPrompt(prompt)}>{t("chat.history.copy")}</button>
                    <span role="status" className={copied && !copied.ok ? "text-xs text-red-700 dark:text-red-400" : "text-xs text-muted-foreground"}>{copied ? t(copied.ok ? "chat.history.copied" : "chat.history.copyFailed") : ""}</span>
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
  // Unfinished work the user can relaunch: tasks stopped with /stop
  // (cancelled) included, tasks already running (in_progress) excluded.
  const pending = items.filter((item) => !item.done && item.status !== "done" && item.status !== "in_progress");
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
                const tone = item.done || item.status === "done" ? "done" : item.cancelled || item.status === "cancelled" ? "cancelled" : item.blocked || item.status === "blocked" ? "blocked" : item.active || ["in_progress", "review", "audit", "fix"].includes(item.status) ? "active" : "pending";
                return <li key={item.id} className="min-w-0 rounded-lg border p-3">
                  <div className="flex flex-wrap items-start justify-between gap-2"><strong dir="auto" className="min-w-0 break-words text-sm">{item.title} <span className="text-muted-foreground">({item.id})</span></strong><span className={`${badge} ${tones[tone]}`}>{t(`chat.plans.status.${tone}`)}</span></div>
                  {item.description ? <p dir="auto" className="mt-1 whitespace-pre-wrap break-words text-sm text-muted-foreground">{item.description}</p> : null}
                  {pending.includes(item) ? <button type="button" className="mt-2 rounded-md border px-3 py-1.5 text-xs hover:bg-muted" aria-label={t("chat.plans.relaunchAria", { title: item.title, id: item.id })} onClick={() => relaunch([item])}>{t("chat.plans.relaunch")}</button> : null}
                </li>;
              })}</ol>
            </>}
      </DialogContent>
    </Dialog>
  );
}
