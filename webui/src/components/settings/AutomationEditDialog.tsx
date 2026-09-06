import { useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import { AlertTriangle, CalendarClock, Gauge, Loader2, MessageSquare } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  DEFAULT_RECURRENCE,
  HOUR_INTERVALS,
  LAST_DAY,
  MAX_MONTH_DAY,
  MINUTE_INTERVALS,
  WEEKDAYS,
  describeRecurrence,
  isConfigurableSystemLoop,
  isRecurrenceKind,
  loopLabel,
  recurrenceFromInterval,
  recurrenceOf,
  weekdayLabel,
  withKind,
} from "@/lib/loop-schedule";
import type {
  AutomationUpdatePayload,
  LoopRecurrence,
  SessionAutomationJob,
} from "@/lib/types";
import { cn } from "@/lib/utils";

type ScheduleMode = LoopRecurrence["kind"] | "cron" | "at";
type EditorTab = "schedule" | "message" | "limits";

interface Draft {
  name: string;
  message: string;
  mode: ScheduleMode;
  recurrence: LoopRecurrence;
  cronExpr: string;
  tz: string;
  atLocal: string;
  dailyTokenBudget: string;
  maxConsecutiveFailures: string;
}

function localDateTimeInput(ms: number): string {
  const date = new Date(ms);
  if (!Number.isFinite(date.getTime())) return "";
  const local = new Date(ms - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}

function draftFromJob(job: SessionAutomationJob | null): Draft {
  const recurrence =
    recurrenceOf(job) ??
    (job?.schedule.kind === "every" && job.schedule.every_ms
      ? recurrenceFromInterval(job.schedule.every_ms)
      : null);
  const mode: ScheduleMode = recurrence
    ? recurrence.kind
    : job?.schedule.kind === "at"
      ? "at"
      : job?.schedule.kind === "cron"
        ? "cron"
        : "daily";
  return {
    name: job?.name ?? "",
    message: job?.payload.message ?? "",
    mode,
    recurrence: recurrence ?? { ...DEFAULT_RECURRENCE },
    cronExpr: job?.schedule.expr ?? "0 9 * * *",
    tz: job?.schedule.tz ?? "",
    atLocal: localDateTimeInput(job?.schedule.at_ms ?? Date.now() + 3_600_000),
    dailyTokenBudget: String(job?.limits?.daily_token_budget ?? 0),
    maxConsecutiveFailures: String(job?.limits?.max_consecutive_failures ?? 0),
  };
}

function isLocalTrigger(job: SessionAutomationJob | null): boolean {
  return job?.kind === "local_trigger" || job?.schedule.kind === "local";
}

/** Return the non-negative integer a field holds, or null when it holds junk. */
function countField(raw: string): number | null {
  const trimmed = raw.trim();
  if (!trimmed) return 0;
  const value = Number(trimmed);
  return Number.isInteger(value) && value >= 0 ? value : null;
}

export function AutomationEditDialog({
  job,
  saving,
  onOpenChange,
  onSave,
}: {
  job: SessionAutomationJob | null;
  saving: boolean;
  onOpenChange: (open: boolean) => void;
  onSave: (job: SessionAutomationJob, values: AutomationUpdatePayload) => void | Promise<void>;
}) {
  const { t, i18n } = useTranslation();
  const tx = (key: string, fallback: string, values?: Record<string, unknown>) =>
    t(key, { defaultValue: fallback, ...(values ?? {}) });
  const locale = i18n.resolvedLanguage || i18n.language;
  const [draft, setDraft] = useState<Draft>(() => draftFromJob(null));
  const [tab, setTab] = useState<EditorTab>("schedule");
  const localTrigger = isLocalTrigger(job);
  const systemLoop = job ? isConfigurableSystemLoop(job) : false;

  useEffect(() => {
    setDraft(draftFromJob(job));
    setTab("schedule");
  }, [job]);

  const patch = (values: Partial<Draft>) => setDraft((prev) => ({ ...prev, ...values }));
  const patchRecurrence = (values: Partial<LoopRecurrence>) =>
    setDraft((prev) => ({ ...prev, recurrence: { ...prev.recurrence, ...values } }));

  const validation = useMemo(
    () => validate(draft, { localTrigger, systemLoop }, tx),
    [draft, localTrigger, systemLoop, tx],
  );

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!job || validation) return;
    void onSave(job, payloadFromDraft(draft, job, { localTrigger, systemLoop }));
  };

  const allTabs: Array<{ value: EditorTab; label: string; icon: typeof CalendarClock }> = [
    { value: "schedule", label: tx("loops.editor.tabs.schedule", "Schedule"), icon: CalendarClock },
    { value: "message", label: tx("loops.editor.tabs.message", "Message"), icon: MessageSquare },
    { value: "limits", label: tx("loops.editor.tabs.limits", "Limits"), icon: Gauge },
  ];
  const tabs = allTabs.filter((entry) => {
    if (localTrigger) return entry.value === "message";
    // A system loop's message belongs to the runtime, so there is nothing to edit.
    if (systemLoop) return entry.value !== "message";
    return true;
  });

  return (
    <Dialog open={Boolean(job)} onOpenChange={onOpenChange}>
      {job ? (
        <DialogContent
          aria-describedby={undefined}
          className="w-[min(calc(100vw-2rem),40rem)] rounded-[26px]"
        >
          <form className="space-y-5" onSubmit={submit}>
            <DialogHeader>
              <DialogTitle>
                {systemLoop ? loopLabel(job, tx) : tx("loops.editor.title", "Edit loop")}
              </DialogTitle>
            </DialogHeader>

            {systemLoop ? (
              <p className="text-[12px] leading-5 text-muted-foreground">
                {tx(
                  "loops.editor.systemHint",
                  "navin runs this loop for itself, so you set when it runs and how much it may spend.",
                )}
              </p>
            ) : (
              <label className="block space-y-1.5">
                <span className="text-[12px] font-medium text-muted-foreground">
                  {tx("loops.editor.name", "Name")}
                </span>
                <Input
                  value={draft.name}
                  onChange={(event) => patch({ name: event.target.value })}
                  className="h-10 rounded-[12px]"
                />
              </label>
            )}

            {!localTrigger ? (
              <div
                role="tablist"
                aria-label={tx("loops.editor.title", "Edit loop")}
                className="flex items-center gap-1 rounded-full bg-muted p-1 text-[12px] font-medium text-muted-foreground"
              >
                {tabs.map((entry) => {
                  const Icon = entry.icon;
                  const selected = tab === entry.value;
                  return (
                    <button
                      key={entry.value}
                      type="button"
                      role="tab"
                      aria-selected={selected}
                      onClick={() => setTab(entry.value)}
                      className={cn(
                        "inline-flex flex-1 items-center justify-center gap-1.5 rounded-full px-3 py-1.5 transition-colors",
                        selected && "bg-background text-foreground shadow-sm",
                      )}
                    >
                      <Icon className="h-3.5 w-3.5" aria-hidden />
                      {entry.label}
                    </button>
                  );
                })}
              </div>
            ) : null}

            <div className="min-h-[15rem]">
              {tab === "schedule" && !localTrigger ? (
                <ScheduleTab
                  draft={draft}
                  locale={locale}
                  repeating={systemLoop}
                  tx={tx}
                  onMode={(mode) =>
                    patch(
                      isRecurrenceKind(mode)
                        ? { mode, recurrence: withKind(draft.recurrence, mode) }
                        : { mode },
                    )
                  }
                  onRecurrence={patchRecurrence}
                  onPatch={patch}
                />
              ) : null}

              {(tab === "message" && !systemLoop) || localTrigger ? (
                <label className="block space-y-1.5">
                  <span className="text-[12px] font-medium text-muted-foreground">
                    {tx("loops.editor.message", "Message sent to the agent")}
                  </span>
                  <Textarea
                    value={draft.message}
                    onChange={(event) => patch({ message: event.target.value })}
                    disabled={localTrigger}
                    className="min-h-[13rem] resize-none rounded-[12px] text-[13px] leading-5"
                  />
                </label>
              ) : null}

              {tab === "limits" && !localTrigger ? (
                <LimitsTab draft={draft} tx={tx} onPatch={patch} />
              ) : null}
            </div>

            {validation ? (
              <div className="flex items-start gap-2 rounded-[12px] bg-destructive/8 px-3 py-2 text-[12px] text-destructive">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
                <span>{validation}</span>
              </div>
            ) : null}

            <DialogFooter>
              <Button
                type="button"
                variant="ghost"
                onClick={() => onOpenChange(false)}
                disabled={saving}
                className="rounded-full"
              >
                {tx("loops.editor.cancel", "Cancel")}
              </Button>
              <Button
                type="submit"
                disabled={Boolean(validation) || saving}
                className="rounded-full"
              >
                {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden /> : null}
                {tx("loops.editor.save", "Save")}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      ) : null}
    </Dialog>
  );
}

const FIELD_CLASS =
  "h-10 w-full rounded-[12px] border border-input bg-background px-3 text-[13px] text-foreground shadow-sm outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring";

function ScheduleTab({
  draft,
  locale,
  repeating,
  tx,
  onMode,
  onRecurrence,
  onPatch,
}: {
  draft: Draft;
  locale: string;
  /** True when the loop must repeat, which rules out a one-off run. */
  repeating: boolean;
  tx: (key: string, fallback: string, values?: Record<string, unknown>) => string;
  onMode: (mode: ScheduleMode) => void;
  onRecurrence: (values: Partial<LoopRecurrence>) => void;
  onPatch: (values: Partial<Draft>) => void;
}) {
  const allModes: Array<{ value: ScheduleMode; label: string }> = [
    { value: "minutes", label: tx("loops.editor.modes.minutes", "Minutes") },
    { value: "hourly", label: tx("loops.editor.modes.hourly", "Hourly") },
    { value: "every_hours", label: tx("loops.editor.modes.everyHours", "Every N hours") },
    { value: "daily", label: tx("loops.editor.modes.daily", "Daily") },
    { value: "weekly", label: tx("loops.editor.modes.weekly", "Weekly") },
    { value: "monthly", label: tx("loops.editor.modes.monthly", "Monthly") },
    { value: "cron", label: tx("loops.editor.modes.cron", "Expression") },
    { value: "at", label: tx("loops.editor.modes.at", "Once") },
  ];
  const modes = repeating ? allModes.filter((mode) => mode.value !== "at") : allModes;
  const preview = isRecurrenceKind(draft.mode)
    ? describeRecurrence({ ...draft.recurrence, kind: draft.mode }, tx, locale)
    : null;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-1.5">
        {modes.map((mode) => (
          <button
            key={mode.value}
            type="button"
            onClick={() => onMode(mode.value)}
            className={cn(
              "inline-flex h-8 items-center rounded-full border px-3 text-[12px] font-medium transition-colors",
              draft.mode === mode.value
                ? "border-transparent bg-foreground text-background"
                : "border-border text-muted-foreground hover:text-foreground",
            )}
          >
            {mode.label}
          </button>
        ))}
      </div>

      {draft.mode === "minutes" ? (
        <Field label={tx("loops.editor.fields.interval", "Interval")}>
          <select
            value={draft.recurrence.interval}
            onChange={(event) => onRecurrence({ interval: Number(event.target.value) })}
            className={FIELD_CLASS}
          >
            {MINUTE_INTERVALS.map((value) => (
              <option key={value} value={value}>
                {tx("loops.editor.everyMinutes", "Every {{count}} min", { count: value })}
              </option>
            ))}
          </select>
        </Field>
      ) : null}

      {draft.mode === "every_hours" ? (
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={tx("loops.editor.fields.interval", "Interval")}>
            <select
              value={draft.recurrence.interval}
              onChange={(event) => onRecurrence({ interval: Number(event.target.value) })}
              className={FIELD_CLASS}
            >
              {HOUR_INTERVALS.map((value) => (
                <option key={value} value={value}>
                  {tx("loops.editor.everyHours", "Every {{count}} h", { count: value })}
                </option>
              ))}
            </select>
          </Field>
          <MinuteField draft={draft} tx={tx} onRecurrence={onRecurrence} />
        </div>
      ) : null}

      {draft.mode === "hourly" ? (
        <MinuteField draft={draft} tx={tx} onRecurrence={onRecurrence} />
      ) : null}

      {draft.mode === "weekly" ? (
        <Field label={tx("loops.editor.fields.weekday", "Day of the week")}>
          <select
            value={draft.recurrence.weekday}
            onChange={(event) => onRecurrence({ weekday: Number(event.target.value) })}
            className={FIELD_CLASS}
          >
            {WEEKDAYS.map((weekday) => (
              <option key={weekday} value={weekday}>
                {weekdayLabel(weekday, locale)}
              </option>
            ))}
          </select>
        </Field>
      ) : null}

      {draft.mode === "monthly" ? (
        <Field label={tx("loops.editor.fields.day", "Day of the month")}>
          <select
            value={String(draft.recurrence.day)}
            onChange={(event) =>
              onRecurrence({
                day: event.target.value === LAST_DAY ? LAST_DAY : Number(event.target.value),
              })
            }
            className={FIELD_CLASS}
          >
            {Array.from({ length: MAX_MONTH_DAY }, (_, index) => index + 1).map((day) => (
              <option key={day} value={day}>
                {day}
              </option>
            ))}
            <option value={LAST_DAY}>{tx("loops.editor.lastDay", "Last day")}</option>
          </select>
        </Field>
      ) : null}

      {draft.mode === "daily" || draft.mode === "weekly" || draft.mode === "monthly" ? (
        <Field label={tx("loops.editor.fields.time", "Time")}>
          <Input
            type="time"
            value={`${String(draft.recurrence.hour).padStart(2, "0")}:${String(
              draft.recurrence.minute,
            ).padStart(2, "0")}`}
            onChange={(event) => {
              const [hour, minute] = event.target.value.split(":");
              onRecurrence({ hour: Number(hour) || 0, minute: Number(minute) || 0 });
            }}
            className="h-10 rounded-[12px]"
          />
        </Field>
      ) : null}

      {draft.mode === "cron" ? (
        <Field label={tx("loops.editor.fields.expression", "Cron expression")}>
          <Input
            value={draft.cronExpr}
            onChange={(event) => onPatch({ cronExpr: event.target.value })}
            placeholder="0 9 * * 1-5"
            className="h-10 rounded-[12px] font-mono text-[13px]"
          />
        </Field>
      ) : null}

      {draft.mode === "at" ? (
        <Field label={tx("loops.editor.fields.runAt", "Run at")}>
          <Input
            type="datetime-local"
            value={draft.atLocal}
            onChange={(event) => onPatch({ atLocal: event.target.value })}
            className="h-10 rounded-[12px]"
          />
        </Field>
      ) : null}

      {draft.mode !== "at" ? (
        <Field
          label={tx("loops.editor.fields.timezone", "Timezone")}
          hint={tx("loops.editor.timezoneHint", "Leave empty to follow the host clock.")}
        >
          <Input
            value={draft.tz}
            onChange={(event) => onPatch({ tz: event.target.value })}
            placeholder="Europe/Paris"
            className="h-10 rounded-[12px] text-[13px]"
          />
        </Field>
      ) : null}

      {preview ? (
        <p className="rounded-[12px] bg-muted/60 px-3 py-2 text-[12px] text-muted-foreground">
          {preview}
        </p>
      ) : null}
    </div>
  );
}

function MinuteField({
  draft,
  tx,
  onRecurrence,
}: {
  draft: Draft;
  tx: (key: string, fallback: string, values?: Record<string, unknown>) => string;
  onRecurrence: (values: Partial<LoopRecurrence>) => void;
}) {
  return (
    <Field label={tx("loops.editor.fields.minute", "Minute of the hour")}>
      <Input
        type="number"
        min={0}
        max={59}
        step={1}
        value={draft.recurrence.minute}
        onChange={(event) =>
          onRecurrence({ minute: Math.min(59, Math.max(0, Number(event.target.value) || 0)) })
        }
        className="h-10 rounded-[12px]"
      />
    </Field>
  );
}

function LimitsTab({
  draft,
  tx,
  onPatch,
}: {
  draft: Draft;
  tx: (key: string, fallback: string, values?: Record<string, unknown>) => string;
  onPatch: (values: Partial<Draft>) => void;
}) {
  return (
    <div className="space-y-4">
      <Field
        label={tx("loops.editor.fields.dailyBudget", "Daily token budget")}
        hint={tx(
          "loops.editor.dailyBudgetHint",
          "Once the day's runs have spent this many tokens, the loop skips its remaining runs and resumes tomorrow. 0 means no ceiling.",
        )}
      >
        <Input
          type="number"
          min={0}
          step={1000}
          value={draft.dailyTokenBudget}
          onChange={(event) => onPatch({ dailyTokenBudget: event.target.value })}
          className="h-10 rounded-[12px]"
        />
      </Field>

      <Field
        label={tx("loops.editor.fields.maxFailures", "Pause after N failures")}
        hint={tx(
          "loops.editor.maxFailuresHint",
          "Each failure delays the next attempt, doubling every time. After this many failures in a row the loop pauses and says why. 0 uses the global setting.",
        )}
      >
        <Input
          type="number"
          min={0}
          max={100}
          step={1}
          value={draft.maxConsecutiveFailures}
          onChange={(event) => onPatch({ maxConsecutiveFailures: event.target.value })}
          className="h-10 rounded-[12px]"
        />
      </Field>
    </div>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block space-y-1.5">
      <span className="text-[12px] font-medium text-muted-foreground">{label}</span>
      {children}
      {hint ? <span className="block text-[11px] leading-4 text-muted-foreground/80">{hint}</span> : null}
    </label>
  );
}

interface Ownership {
  localTrigger: boolean;
  systemLoop: boolean;
}

function validate(
  draft: Draft,
  { localTrigger, systemLoop }: Ownership,
  tx: (key: string, fallback: string, values?: Record<string, unknown>) => string,
): string | null {
  if (!systemLoop && !draft.name.trim()) {
    return tx("loops.editor.errors.name", "A name is required.");
  }
  if (localTrigger) return null;
  if (!systemLoop && !draft.message.trim()) {
    return tx("loops.editor.errors.message", "A message is required.");
  }
  if (draft.mode === "cron" && !draft.cronExpr.trim()) {
    return tx("loops.editor.errors.expression", "A cron expression is required.");
  }
  if (draft.mode === "at" && !Number.isFinite(new Date(draft.atLocal).getTime())) {
    return tx("loops.editor.errors.runAt", "A run time is required.");
  }
  if (countField(draft.dailyTokenBudget) === null) {
    return tx("loops.editor.errors.budget", "The budget must be zero or a positive number.");
  }
  const failures = countField(draft.maxConsecutiveFailures);
  if (failures === null || failures > 100) {
    return tx("loops.editor.errors.failures", "The failure ceiling must be between 0 and 100.");
  }
  return null;
}

function payloadFromDraft(
  draft: Draft,
  job: SessionAutomationJob,
  { localTrigger, systemLoop }: Ownership,
): AutomationUpdatePayload {
  const name = draft.name.trim();
  if (localTrigger) return { name };

  const payload: AutomationUpdatePayload = systemLoop
    ? {}
    : { name, message: draft.message.trim() };
  const tz = draft.tz.trim();

  if (isRecurrenceKind(draft.mode)) {
    payload.schedule = {
      kind: "recurrence",
      recurrence: { ...draft.recurrence, kind: draft.mode },
      ...(tz ? { tz } : {}),
    };
  } else if (draft.mode === "cron") {
    payload.schedule = { kind: "cron", expr: draft.cronExpr.trim(), ...(tz ? { tz } : {}) };
  } else {
    payload.schedule = { kind: "at", at_ms: new Date(draft.atLocal).getTime() };
  }

  const budget = countField(draft.dailyTokenBudget) ?? 0;
  const failures = countField(draft.maxConsecutiveFailures) ?? 0;
  if (
    budget !== (job.limits?.daily_token_budget ?? 0) ||
    failures !== (job.limits?.max_consecutive_failures ?? 0)
  ) {
    payload.limits = { daily_token_budget: budget, max_consecutive_failures: failures };
  }
  return payload;
}
