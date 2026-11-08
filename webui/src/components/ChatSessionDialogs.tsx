// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { MarkdownText, preloadMarkdownText } from "@/components/MarkdownText";
import { fetchWebuiPrompts, updateBoard, type BoardPayload, type SessionPlan, type SessionPlanItem } from "@/lib/api";
import { fetchBoardShared } from "@/lib/board-fetch";
import { formatSessionPlanMarkdown, planMarkdownLabels, type PlanLabelTranslator } from "@/lib/plan-markdown";
import type { WebuiPromptEntry } from "@/lib/types";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";

const badge = "inline-flex shrink-0 rounded-full px-2 py-0.5 text-xs font-medium";
const tones = {
  done: "bg-green-500/15 text-green-700 dark:text-green-400",
  unfinished: "bg-orange-500/15 text-orange-700 dark:text-orange-400",
  error: "bg-red-500/15 text-red-700 dark:text-red-400",
  active: "bg-blue-500/15 text-blue-700 dark:text-blue-400",
  review: "bg-violet-500/15 text-violet-700 dark:text-violet-400",
  blocked: "bg-red-500/15 text-red-700 dark:text-red-400",
  cancelled: "bg-orange-500/15 text-orange-700 dark:text-orange-400",
  pending: "bg-muted text-muted-foreground",
};

const COPY_FEEDBACK_MS = 2000;

/** Same reading as the full plan of the Code column. */
const PLAN_PROSE = [
  "prose prose-sm max-w-none dark:prose-invert",
  "prose-headings:font-semibold prose-headings:tracking-tight",
  "prose-h1:mb-2 prose-h1:text-[1.2rem] prose-h2:mt-6 prose-h2:mb-2 prose-h2:text-[1.05rem]",
  "prose-h3:mt-5 prose-h3:mb-1.5 prose-h3:text-[0.95rem] prose-p:my-2 prose-p:leading-relaxed",
  "prose-li:my-0.5 prose-ul:my-2 prose-strong:text-foreground",
].join(" ");

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

type PlanList = { current: SessionPlan | null; earlier: SessionPlan[] };

function planList(board: Pick<BoardPayload, "session_plan" | "session_plans">): PlanList {
  const current = board.session_plan && board.session_plan.items.length ? board.session_plan : null;
  return { current, earlier: (board.session_plans ?? []).filter((plan) => plan.items.length > 0) };
}

export function ChatPlansDialog({ open, onOpenChange, sessionKey, token, onRelaunch }: DialogProps & { onRelaunch: (text: string) => void }) {
  const { t } = useTranslation();
  const [plans, setPlans] = useState<PlanList>({ current: null, earlier: [] });
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState(false);
  useEffect(() => {
    if (!open || !sessionKey) return;
    let active = true;
    setState("loading");
    setPlans({ current: null, earlier: [] });
    setActionError(false);
    fetchBoardShared(token, sessionKey).then(
      (board) => { if (active) { setPlans(planList(board)); setState("ready"); } },
      () => { if (active) setState("error"); },
    );
    return () => { active = false; };
  }, [open, sessionKey, token]);

  const relaunch = (tasks: SessionPlanItem[]) => {
    const instruction = tasks.map((task) => `Continue board task ${task.id}: ${task.title}`).join("\n");
    onRelaunch(instruction);
    onOpenChange(false);
  };
  // Hiding is a view choice of this chat: the tasks and the run go on.
  const setHidden = async (plan: SessionPlan, hidden: boolean) => {
    if (!sessionKey || busy) return;
    setBusy(true);
    setActionError(false);
    try {
      const board = await updateBoard(token, sessionKey, { action: hidden ? "hide_plan" : "show_plan", plan_id: plan.current_id });
      setPlans(planList(board));
    } catch {
      setActionError(true);
    } finally {
      setBusy(false);
    }
  };
  const all = plans.current ? [plans.current, ...plans.earlier] : plans.earlier;
  const earlierId = useId();
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85dvh] max-w-2xl overflow-y-auto" aria-describedby={undefined}>
        <DialogHeader><DialogTitle>{t("chat.plans.title")}</DialogTitle></DialogHeader>
        {state === "loading" ? <p role="status">{t("chat.plans.loading")}</p>
          : state === "error" ? <p role="alert">{t("chat.plans.error")}</p>
          : !all.length ? <p>{t("chat.plans.empty")}</p>
          : <div className="grid gap-6">
              {actionError ? <p role="alert" className="text-sm text-red-700 dark:text-red-400">{t("chat.plans.actionFailed")}</p> : null}
              {plans.current ? <PlanSection plan={plans.current} heading={t("chat.plans.current")} onRelaunch={relaunch}
                visibility={{ busy, onToggle: (hidden) => void setHidden(plans.current as SessionPlan, hidden) }} /> : null}
              {plans.earlier.length ? (
                <section className="grid gap-4" aria-labelledby={earlierId}>
                  <h3 id={earlierId} className="text-sm font-semibold text-muted-foreground">{t("chat.plans.earlier")}</h3>
                  {plans.earlier.map((plan, index) => (
                    <PlanSection key={`${index}-${plan.items[0]?.id}`} plan={plan} heading={plan.goal || plan.items[0]?.title || t("chat.plans.title")} onRelaunch={relaunch} />
                  ))}
                </section>
              ) : null}
            </div>}
      </DialogContent>
    </Dialog>
  );
}

/** One plan of the chat: progress, its tasks and what can be relaunched. */
function PlanSection({ plan, heading, onRelaunch, visibility }: {
  plan: SessionPlan;
  heading: string;
  onRelaunch: (tasks: SessionPlanItem[]) => void;
  visibility?: { busy: boolean; onToggle: (hidden: boolean) => void };
}) {
  const { t } = useTranslation();
  const items = plan.items;
  // Unfinished work the user can relaunch: tasks stopped with /stop
  // (cancelled) included, tasks already running (in_progress) excluded.
  const pending = items.filter((item) => !item.done && item.status !== "done" && item.status !== "in_progress");
  const completed = items.filter((item) => item.done || item.status === "done").length;
  const review = items.filter((item) => !item.done && (item.status === "review" || item.status === "audit")).length;
  const progress = t("chat.plans.progress", { done: completed, total: items.length });
  return (
    <section className="grid min-w-0 gap-3" aria-label={heading}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 dir="auto" className="flex flex-wrap items-center gap-2 break-words text-sm font-semibold [text-wrap:balance]">
            {heading}
            {visibility && plan.hidden ? <span className={`${badge} ${tones.pending}`}>{t("chat.plans.hiddenBadge")}</span> : null}
          </h3>
          <p className="text-sm font-medium tabular-nums">
            {progress}
            {review ? <span className="font-normal text-muted-foreground"> · {t("thread.plan.toValidate", { count: review, defaultValue: "{{count}} to validate" })}</span> : null}
          </p>
        </div>
        {visibility ? (
          <PlanVisibilityButton hidden={Boolean(plan.hidden)} busy={visibility.busy} onToggle={visibility.onToggle} />
        ) : null}
      </div>
      <PlanProgressBar completed={completed} total={items.length} label={progress} />
      {pending.length > 1 ? <button type="button" className="justify-self-start rounded-md bg-primary px-3 py-2 text-sm text-primary-foreground" onClick={() => onRelaunch(pending)}>{t("chat.plans.relaunchAll")}</button> : null}
      <ol className="grid gap-3">{items.map((item) => {
        // Handed over for a check before blocked: its own dependencies may still be open.
        const tone = item.done || item.status === "done" ? "done" : item.cancelled || item.status === "cancelled" ? "cancelled" : item.status === "review" || item.status === "audit" ? "review" : item.blocked || item.status === "blocked" ? "blocked" : item.active || ["in_progress", "fix"].includes(item.status) ? "active" : "pending";
        return <li key={item.id} className="min-w-0 rounded-lg border p-3">
          <div className="flex flex-wrap items-start justify-between gap-2"><strong dir="auto" className="min-w-0 break-words text-sm">{item.title} <span className="text-muted-foreground">({item.id})</span></strong><span className={`${badge} ${tones[tone]}`}>{t(`chat.plans.status.${tone}`)}</span></div>
          {item.description ? <p dir="auto" className="mt-1 whitespace-pre-wrap break-words text-sm text-muted-foreground">{item.description}</p> : null}
          {pending.includes(item) ? <button type="button" className="mt-2 rounded-md border px-3 py-1.5 text-xs hover:bg-muted" aria-label={t("chat.plans.relaunchAria", { title: item.title, id: item.id })} onClick={() => onRelaunch([item])}>{t("chat.plans.relaunch")}</button> : null}
        </li>;
      })}</ol>
    </section>
  );
}

/** Hides or shows the chat's current plan: only the view changes, its tasks and the run go on. */
function PlanVisibilityButton({ hidden, busy, onToggle }: { hidden: boolean; busy: boolean; onToggle: (hidden: boolean) => void }) {
  const { t } = useTranslation();
  return (
    <button type="button" disabled={busy}
      className="min-h-10 rounded-md border px-3 text-sm transition-[background-color,transform] hover:bg-muted active:scale-[0.96] disabled:opacity-50"
      title={hidden ? undefined : t("chat.plans.hideHint")}
      onClick={() => onToggle(!hidden)}>
      {t(hidden ? "chat.plans.show" : "chat.plans.hide")}
    </button>
  );
}

function PlanProgressBar({ completed, total, label }: { completed: number; total: number; label: string }) {
  return (
    <div role="progressbar" aria-valuenow={completed} aria-valuemin={0} aria-valuemax={total} aria-label={label} className="h-2 overflow-hidden rounded-full bg-muted">
      <div className="h-full bg-green-600 transition-[width] duration-300 ease-out" style={{ width: `${total ? completed / total * 100 : 0}%` }} />
    </div>
  );
}

/**
 * "Current plan" of the chat actions menu: the chat's current plan with all
 * its detail (goal, criteria, constraints and facts of its mission, every step
 * with its full description, status, acceptance and validation), and whether
 * the chat shows it. A plan hidden from the chat is still read here.
 */
export function ChatCurrentPlanDialog({ open, onOpenChange, sessionKey, token }: DialogProps) {
  const { t } = useTranslation();
  const tx = useCallback<PlanLabelTranslator>(
    (key, fallback, values) => t(key, { defaultValue: fallback, ...values }),
    [t],
  );
  const [board, setBoard] = useState<BoardPayload | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState(false);
  useEffect(() => {
    if (!open || !sessionKey) return;
    let active = true;
    setState("loading");
    setBoard(null);
    setActionError(false);
    preloadMarkdownText();
    fetchBoardShared(token, sessionKey).then(
      (payload) => { if (active) { setBoard(payload); setState("ready"); } },
      () => { if (active) setState("error"); },
    );
    return () => { active = false; };
  }, [open, sessionKey, token]);

  const plan = board?.session_plan && board.session_plan.items.length ? board.session_plan : null;
  // The project ledger belongs to the chat that runs it: the server only puts
  // `goal` on this chat's plan when it works on that mission.
  const markdown = useMemo(
    () => (plan ? formatSessionPlanMarkdown(plan, board?.tasks ?? [], plan.goal ? board?.mission ?? null : null, planMarkdownLabels(tx)) : ""),
    [board, plan, tx],
  );
  const setHidden = async (hidden: boolean) => {
    if (!sessionKey || !plan || busy) return;
    setBusy(true);
    setActionError(false);
    try {
      const next = await updateBoard(token, sessionKey, { action: hidden ? "hide_plan" : "show_plan", plan_id: plan.current_id });
      // Keep the detail already read when the answer leaves part of the board out.
      setBoard((previous) => (previous ? { ...previous, ...next } : next));
    } catch {
      setActionError(true);
    } finally {
      setBusy(false);
    }
  };
  const completed = plan ? plan.items.filter((item) => item.done || item.status === "done").length : 0;
  const progress = plan ? t("chat.plans.progress", { done: completed, total: plan.items.length }) : "";
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85dvh] max-w-3xl overflow-y-auto" aria-describedby={undefined}>
        <DialogHeader><DialogTitle>{t("chat.plans.current")}</DialogTitle></DialogHeader>
        {state === "loading" ? <p role="status">{t("chat.plans.loading")}</p>
          : state === "error" ? <p role="alert">{t("chat.plans.error")}</p>
          : !plan ? <p>{t("chat.plans.currentEmpty")}</p>
          : <div className="grid min-w-0 gap-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="flex flex-wrap items-center gap-2 text-sm font-medium tabular-nums">
                  {progress}
                  {plan.hidden ? <span className={`${badge} ${tones.pending}`}>{t("chat.plans.hiddenBadge")}</span> : null}
                </p>
                <PlanVisibilityButton hidden={Boolean(plan.hidden)} busy={busy} onToggle={(hidden) => void setHidden(hidden)} />
              </div>
              <PlanProgressBar completed={completed} total={plan.items.length} label={progress} />
              {actionError ? <p role="alert" className="text-sm text-red-700 dark:text-red-400">{t("chat.plans.actionFailed")}</p> : null}
              <div data-testid="chat-current-plan-detail" className={`min-w-0 break-words ${PLAN_PROSE}`}>
                <MarkdownText>{markdown}</MarkdownText>
              </div>
            </div>}
      </DialogContent>
    </Dialog>
  );
}
