// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useState } from "react";
import type { MouseEvent, ReactNode } from "react";
import {
  CalendarClock,
  CircleAlert,
  Loader2,
  PauseCircle,
  Pencil,
  PlayCircle,
  Plus,
  RefreshCcw,
  Trash2,
} from "lucide-react";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";

import { AutomationEditDialog } from "@/components/settings/AutomationEditDialog";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useSessionAutomationJobs } from "@/hooks/useSessionAutomationJobs";
import { currentLocale } from "@/i18n";
import { runAutomationAction, updateAutomation } from "@/lib/api";
import { fmtDateTime } from "@/lib/format";
import {
  isConfigurableSystemLoop,
  loopLabel,
} from "@/lib/loop-schedule";
import type {
  AutomationUpdatePayload,
  SessionAutomationJob,
} from "@/lib/types";
import { cn } from "@/lib/utils";

const RELATIVE_THRESHOLDS: [number, Intl.RelativeTimeFormatUnit][] = [
  [60, "second"],
  [60, "minute"],
  [24, "hour"],
  [7, "day"],
  [4.345, "week"],
  [12, "month"],
  [Number.POSITIVE_INFINITY, "year"],
];

type AutomationAction = "enable" | "disable" | "delete" | "run";

interface SessionInfoPopoverProps {
  sessionKey: string;
  token: string;
  title: string;
  productModule?: string | null;
  onCreateLoop?: () => void;
  /** Match the compact workbench chat-bar icon buttons. */
  compact?: boolean;
  /** Show the planner name next to the icon (workbench tools row). */
  showLabel?: boolean;
  /** Overrides on the trigger, e.g. to match the composer's 28px tool buttons. */
  triggerClassName?: string;
}

export function SessionInfoPopover({
  sessionKey,
  token,
  title,
  productModule = null,
  onCreateLoop,
  compact = false,
  showLabel = false,
  triggerClassName,
}: SessionInfoPopoverProps) {
  const { t } = useTranslation("common");
  const [open, setOpen] = useState(false);
  const [actionKey, setActionKey] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [pendingEdit, setPendingEdit] = useState<SessionAutomationJob | null>(null);
  const [pendingDelete, setPendingDelete] = useState<SessionAutomationJob | null>(null);
  const { jobs, loading, loadFailed, now, refresh } = useSessionAutomationJobs(
    open || Boolean(pendingEdit) || Boolean(pendingDelete),
    token,
    sessionKey,
  );
  const activeCount = jobs.filter((job) => job.enabled).length;

  const handleAction = async (action: AutomationAction, job: SessionAutomationJob) => {
    const key = `${action}:${job.id}`;
    setActionKey(key);
    setActionError(null);
    try {
      await runAutomationAction(token, action, job.id);
      if (action === "delete") setPendingDelete(null);
      await refresh(false);
      if (action === "run") {
        window.setTimeout(() => void refresh(false), 1200);
        window.setTimeout(() => void refresh(false), 4000);
      }
    } catch (err) {
      setActionError((err as Error).message);
    } finally {
      setActionKey(null);
    }
  };

  const handleEdit = async (job: SessionAutomationJob, values: AutomationUpdatePayload) => {
    setActionKey(`update:${job.id}`);
    setActionError(null);
    try {
      await updateAutomation(token, job.id, values);
      setPendingEdit(null);
      await refresh(false);
    } catch (err) {
      setActionError((err as Error).message);
    } finally {
      setActionKey(null);
    }
  };

  const automationContent = loading ? (
    <div className="flex items-center gap-2 rounded-[16px] bg-muted/45 px-3 py-3 text-[12.5px] text-muted-foreground">
      <RefreshCcw className="h-3.5 w-3.5 animate-spin" />
      {t("thread.sessionInfo.loading")}
    </div>
  ) : loadFailed ? (
    <div className="flex items-center gap-2 rounded-[16px] bg-amber-500/10 px-3 py-3 text-[12.5px] text-amber-700 dark:text-amber-300">
      <CircleAlert className="h-3.5 w-3.5" />
      {t("thread.sessionInfo.loadFailed")}
    </div>
  ) : jobs.length ? (
    <div className="space-y-1.5">
      {jobs.map((job) => (
        <AutomationRow
          key={job.id}
          job={job}
          now={now}
          actionKey={actionKey}
          onAction={handleAction}
          onRequestEdit={(next) => {
            setOpen(false);
            setPendingEdit(next);
          }}
          onRequestDelete={(next) => {
            setOpen(false);
            setPendingDelete(next);
          }}
        />
      ))}
    </div>
  ) : (
    <div className="rounded-[16px] bg-muted/35 px-3 py-3 text-[12.5px] leading-relaxed text-muted-foreground">
      {t("thread.sessionInfo.empty")}
      <p className="mt-1.5 text-[12px] text-muted-foreground/85">
        {t("thread.sessionInfo.createHint")}
      </p>
    </div>
  );

  return (
    <>
      <DropdownMenu modal={false} open={open} onOpenChange={setOpen}>
        <DropdownMenuTrigger asChild>
          <Button
            variant="ghost"
            size={showLabel ? "default" : "icon"}
            aria-label={t("thread.header.sessionInfo")}
            title={t("thread.header.sessionInfo")}
            className={cn(
              "host-no-drag relative text-muted-foreground/85 hover:text-foreground",
              showLabel
                ? "h-8 gap-1.5 rounded-md px-2 text-[12px] font-medium text-muted-foreground/80 hover:bg-muted/60 hover:text-foreground"
                : compact
                  ? "h-8 w-8 rounded-md text-muted-foreground/70 hover:bg-muted/60"
                  : "h-8 w-8 rounded-full hover:bg-accent/40",
              triggerClassName,
            )}
          >
            <CalendarClock className={cn(showLabel ? "h-3.5 w-3.5" : "h-4 w-4 stroke-[1.75]")} />
            {showLabel ? (
              <span className="truncate">
                {t("thread.sessionInfo.plannerLabel", { defaultValue: "Planner" })}
              </span>
            ) : null}
            {activeCount > 0 ? (
              <span
                className={cn(
                  "absolute rounded-full bg-emerald-500",
                  showLabel
                    ? "right-1 top-1 h-1.5 w-1.5"
                    : compact
                      ? "right-1 top-1 h-1.5 w-1.5"
                      : "right-1.5 top-1.5 h-1.5 w-1.5",
                )}
                aria-hidden
              />
            ) : null}
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent
          align="end"
          sideOffset={8}
          className="w-[min(24rem,calc(100vw-1.5rem))] rounded-[24px] p-0"
        >
          <div className="space-y-3 px-4 py-3.5">
            <div className="min-w-0">
              <div className="text-[12px] font-normal text-muted-foreground/75">
                {t("thread.sessionInfo.title")}
              </div>
              <div className="mt-0.5 truncate text-[14px] font-medium text-foreground">
                {title || t("thread.sessionInfo.untitled")}
              </div>
            </div>

            <div className="h-px bg-border/45" />

            <div className="flex items-center justify-between gap-3">
              <div className="flex min-w-0 items-center gap-2">
                <CalendarClock className="h-3.5 w-3.5 shrink-0 text-muted-foreground/80" />
                <span className="truncate text-[13px] font-medium text-foreground">
                  {t("thread.sessionInfo.automations")}
                </span>
              </div>
              <span className="rounded-full bg-muted/70 px-2 py-0.5 text-[11px] text-muted-foreground">
                {t("thread.sessionInfo.count", { count: jobs.length })}
              </span>
            </div>

            <p className="text-[12px] leading-relaxed text-muted-foreground">
              {t("thread.sessionInfo.loopIntro", {
                module: moduleLabel(productModule, t),
              })}
            </p>

            {automationContent}

            {actionError ? (
              <div className="rounded-[14px] bg-amber-500/10 px-3 py-2 text-[12px] text-amber-700 dark:text-amber-300">
                {actionError}
              </div>
            ) : null}

            {onCreateLoop ? (
              <Button
                type="button"
                variant="secondary"
                className="h-9 w-full justify-center gap-1.5 rounded-full text-[12.5px] font-medium"
                onClick={() => {
                  setOpen(false);
                  onCreateLoop();
                }}
              >
                <Plus className="h-3.5 w-3.5" aria-hidden />
                {t("thread.sessionInfo.create")}
              </Button>
            ) : null}
          </div>
        </DropdownMenuContent>
      </DropdownMenu>

      <AutomationEditDialog
        job={pendingEdit}
        saving={Boolean(pendingEdit && actionKey === `update:${pendingEdit.id}`)}
        onOpenChange={(next) => {
          if (!next) setPendingEdit(null);
        }}
        onSave={handleEdit}
      />

      <AlertDialog
        open={Boolean(pendingDelete)}
        onOpenChange={(next) => {
          if (!next) setPendingDelete(null);
        }}
      >
        <AlertDialogContent className="w-[min(calc(100vw-2rem),24rem)] rounded-[28px]">
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t("thread.sessionInfo.deleteTitle", {
                name: pendingDelete
                  ? loopLabel(pendingDelete, (key, fallback) =>
                      t(key, { defaultValue: fallback }),
                    )
                  : "",
              })}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t("thread.sessionInfo.deleteDescription")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={Boolean(actionKey)}>
              {t("deleteConfirm.cancel", { defaultValue: "Cancel" })}
            </AlertDialogCancel>
            <AlertDialogAction
              disabled={!pendingDelete || Boolean(actionKey)}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={(event) => {
                event.preventDefault();
                if (pendingDelete) void handleAction("delete", pendingDelete);
              }}
            >
              {actionKey?.startsWith("delete:") ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden />
              ) : null}
              {t("thread.sessionInfo.deleteConfirm")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

function AutomationRow({
  job,
  now,
  actionKey,
  onAction,
  onRequestEdit,
  onRequestDelete,
}: {
  job: SessionAutomationJob;
  now: number;
  actionKey: string | null;
  onAction: (action: AutomationAction, job: SessionAutomationJob) => void | Promise<void>;
  onRequestEdit: (job: SessionAutomationJob) => void;
  onRequestDelete: (job: SessionAutomationJob) => void;
}) {
  const { t } = useTranslation("common");
  const label = loopLabel(job, (key, fallback) => t(key, { defaultValue: fallback }));
  const schedule = formatSchedule(job, t);
  const nextRun = formatNextRun(job, t, now);
  const systemLoop = isConfigurableSystemLoop(job);
  const localTrigger = isLocalTriggerAutomation(job);
  const userOwned = !job.protected;
  const canManage = userOwned || systemLoop;
  const toggleAction: AutomationAction = job.enabled ? "disable" : "enable";
  const canToggle = canManage && (job.enabled || Boolean(job.origin) || systemLoop);
  const canRun =
    userOwned && Boolean(job.origin) && job.enabled && !job.state.pending && !localTrigger;
  const statusClass = job.enabled
    ? job.state.last_status === "error"
      ? "bg-destructive"
      : "bg-emerald-500"
    : "bg-muted-foreground/35";

  return (
    <div className="rounded-[16px] px-3 py-2.5 transition-colors hover:bg-muted/40">
      <div className="flex items-start gap-2.5">
        <span className={cn("mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full", statusClass)} />
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 items-center gap-2">
            <span className="truncate text-[13px] font-medium text-foreground">{label}</span>
            {!job.enabled ? (
              <span className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-[10.5px] text-muted-foreground">
                {t("thread.sessionInfo.disabled")}
              </span>
            ) : null}
          </div>
          <div className="mt-1 line-clamp-2 text-[12px] leading-snug text-muted-foreground">
            {job.payload.message}
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11.5px] text-muted-foreground/80">
            <span>{schedule}</span>
            <span aria-hidden>·</span>
            <span title={nextRun.title}>{nextRun.label}</span>
          </div>
          {canManage ? (
            <div className="mt-2.5 flex flex-wrap items-center gap-1">
              <RowActionButton
                label={t("settings.automations.edit", { defaultValue: "Edit" })}
                disabled={Boolean(actionKey)}
                onClick={() => onRequestEdit(job)}
              >
                <Pencil className="h-3.5 w-3.5" aria-hidden />
              </RowActionButton>
              {!localTrigger && !systemLoop ? (
                <RowActionButton
                  label={t("settings.automations.runNow", { defaultValue: "Run now" })}
                  busy={actionKey === `run:${job.id}`}
                  disabled={!canRun || Boolean(actionKey)}
                  onClick={() => void onAction("run", job)}
                >
                  <PlayCircle className="h-3.5 w-3.5" aria-hidden />
                </RowActionButton>
              ) : null}
              <RowActionButton
                label={
                  job.enabled
                    ? t("settings.automations.pause", { defaultValue: "Pause" })
                    : t("settings.automations.resume", { defaultValue: "Resume" })
                }
                busy={actionKey === `${toggleAction}:${job.id}`}
                disabled={!canToggle || Boolean(actionKey)}
                onClick={() => void onAction(toggleAction, job)}
              >
                {job.enabled ? (
                  <PauseCircle className="h-3.5 w-3.5" aria-hidden />
                ) : (
                  <PlayCircle className="h-3.5 w-3.5" aria-hidden />
                )}
              </RowActionButton>
              {!systemLoop ? (
                <RowActionButton
                  label={t("settings.automations.delete", { defaultValue: "Delete" })}
                  tone="danger"
                  disabled={Boolean(actionKey)}
                  onClick={() => onRequestDelete(job)}
                >
                  <Trash2 className="h-3.5 w-3.5" aria-hidden />
                </RowActionButton>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function RowActionButton({
  label,
  busy,
  disabled,
  tone = "default",
  onClick,
  children,
}: {
  label: string;
  busy?: boolean;
  disabled?: boolean;
  tone?: "default" | "danger";
  onClick: (event: MouseEvent<HTMLButtonElement>) => void;
  children: ReactNode;
}) {
  return (
    <Button
      type="button"
      size="icon"
      variant="ghost"
      aria-label={label}
      title={label}
      disabled={disabled || busy}
      onClick={onClick}
      className={cn(
        "h-7 w-7 rounded-full text-muted-foreground",
        tone === "danger"
          ? "hover:bg-destructive/10 hover:text-destructive"
          : "hover:bg-muted hover:text-foreground",
      )}
    >
      {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> : children}
    </Button>
  );
}

function moduleLabel(module: string | null | undefined, t: TFunction): string {
  const key = (module || "chat").trim() || "chat";
  return t(`thread.sessionInfo.modules.${key}`, {
    defaultValue: t("thread.sessionInfo.modules.chat"),
  });
}

function formatSchedule(job: SessionAutomationJob, t: TFunction) {
  const locale = currentLocale();
  if (isLocalTriggerAutomation(job)) {
    return t("thread.sessionInfo.schedule.local");
  }
  if (job.schedule.kind === "at" && job.schedule.at_ms) {
    return t("thread.sessionInfo.schedule.at", { time: fmtDateTime(job.schedule.at_ms, locale) });
  }
  if (job.schedule.kind === "every" && job.schedule.every_ms) {
    return t("thread.sessionInfo.schedule.every", {
      duration: formatDuration(job.schedule.every_ms, locale),
    });
  }
  if (job.schedule.kind === "cron" && job.schedule.expr) {
    return job.schedule.tz
      ? t("thread.sessionInfo.schedule.cronWithTz", {
          expr: job.schedule.expr,
          tz: job.schedule.tz,
        })
      : t("thread.sessionInfo.schedule.cron", { expr: job.schedule.expr });
  }
  return t("thread.sessionInfo.schedule.unknown");
}

function formatNextRun(job: SessionAutomationJob, t: TFunction, now: number) {
  const locale = currentLocale();
  if (!job.enabled) {
    return { label: t("thread.sessionInfo.next.disabled"), title: "" };
  }
  if (job.state.pending) {
    return { label: t("thread.sessionInfo.next.pending"), title: "" };
  }
  if (isLocalTriggerAutomation(job)) {
    return { label: t("thread.sessionInfo.next.local"), title: "" };
  }
  const next = job.state.next_run_at_ms;
  if (!next) {
    return { label: t("thread.sessionInfo.next.none"), title: "" };
  }
  return {
    label: t("thread.sessionInfo.next.label", { time: relativeTimeFrom(next, now, locale) }),
    title: fmtDateTime(next, locale),
  };
}

function isLocalTriggerAutomation(job: SessionAutomationJob): boolean {
  return (
    job.kind === "local_trigger"
    || job.payload.kind === "local_trigger"
    || job.schedule.kind === "local"
  );
}

function relativeTimeFrom(value: number, now: number, locale: string): string {
  let delta = (value - now) / 1000;
  const formatter = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
  for (const [step, unit] of RELATIVE_THRESHOLDS) {
    if (Math.abs(delta) < step) {
      return formatter.format(Math.round(delta), unit);
    }
    delta /= step;
  }
  return formatter.format(Math.round(delta), "year");
}

function formatDuration(ms: number, locale: string): string {
  const units: Array<[Intl.NumberFormatOptions["unit"], number]> = [
    ["day", 86_400_000],
    ["hour", 3_600_000],
    ["minute", 60_000],
    ["second", 1000],
  ];
  for (const [unit, size] of units) {
    if (ms >= size && ms % size === 0) {
      return new Intl.NumberFormat(locale, {
        style: "unit",
        unit,
        unitDisplay: "long",
        maximumFractionDigits: 0,
      }).format(ms / size);
    }
  }
  return new Intl.NumberFormat(locale, {
    style: "unit",
    unit: "minute",
    unitDisplay: "long",
    maximumFractionDigits: 1,
  }).format(ms / 60_000);
}

/** Prefill text so the agent creates a cron loop bound to this chat. */
export function createLoopComposerSeed(
  productModule: string | null | undefined,
  t: TFunction,
): string {
  const key = (productModule || "chat").trim() || "chat";
  return t(`thread.sessionInfo.createSeed.${key}`, {
    defaultValue: t("thread.sessionInfo.createSeed.chat"),
  });
}
