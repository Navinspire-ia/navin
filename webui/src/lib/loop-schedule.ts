/**
 * Presets for scheduled loops, kept in step with navin/cron/recurrence.py.
 *
 * The server owns the rules; these lists exist so the editor only ever offers
 * combinations the server will accept, and so a saved loop reopens as the
 * preset that created it.
 */

import type { LoopRecurrence, LoopRecurrenceKind, SessionAutomationJob } from "@/lib/types";

/** Only divisors of 60 keep every gap the same length. */
export const MINUTE_INTERVALS = [1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30] as const;

/** Only divisors of 24 keep every gap the same length. */
export const HOUR_INTERVALS = [1, 2, 3, 4, 6, 8, 12] as const;

/** Day 29-31 is missing from some months, so the editor offers "last" instead. */
export const MAX_MONTH_DAY = 28;

export const LAST_DAY = "last";

export const RECURRENCE_KINDS: readonly LoopRecurrenceKind[] = [
  "minutes",
  "hourly",
  "every_hours",
  "daily",
  "weekly",
  "monthly",
];

/** ISO weekdays, Monday first. */
export const WEEKDAYS = [1, 2, 3, 4, 5, 6, 7] as const;

export type Translate = (key: string, fallback: string, values?: Record<string, unknown>) => string;

export const DEFAULT_RECURRENCE: LoopRecurrence = {
  kind: "daily",
  interval: 1,
  minute: 0,
  hour: 9,
  weekday: 1,
  day: 1,
};

export function isRecurrenceKind(value: string): value is LoopRecurrenceKind {
  return (RECURRENCE_KINDS as readonly string[]).includes(value);
}

/** Return the preset behind a loop, or null when it has a hand-written schedule. */
export function recurrenceOf(job: SessionAutomationJob | null): LoopRecurrence | null {
  const recurrence = job?.schedule.recurrence;
  return recurrence ? { ...recurrence } : null;
}

function nearest(values: readonly number[], value: number): number {
  return values.reduce((best, candidate) =>
    Math.abs(candidate - value) < Math.abs(best - value) ? candidate : best,
  );
}

/**
 * Move a preset to another kind, keeping whatever still makes sense.
 *
 * Intervals do not carry over between units: every 30 minutes is a rhythm, every
 * 30 hours is not a schedule the server accepts, so the interval lands on the
 * closest one the new kind offers.
 */
export function withKind(recurrence: LoopRecurrence, kind: LoopRecurrenceKind): LoopRecurrence {
  if (kind === recurrence.kind) return { ...recurrence };
  const intervals =
    kind === "minutes" ? MINUTE_INTERVALS : kind === "every_hours" ? HOUR_INTERVALS : null;
  return {
    ...recurrence,
    kind,
    interval: intervals ? nearest(intervals, recurrence.interval) : 1,
  };
}

/**
 * Read a plain interval as the preset closest to it.
 *
 * Loops created before presets existed just repeat every N milliseconds, which
 * no preset can describe exactly because such a loop counts from its last run
 * rather than from the clock. The editor still opens on the preset that matches
 * its rhythm, so changing a ceiling does not silently retime the loop.
 */
export function recurrenceFromInterval(everyMs: number): LoopRecurrence | null {
  const hours = everyMs / 3_600_000;
  if (Number.isInteger(hours) && (HOUR_INTERVALS as readonly number[]).includes(hours)) {
    return { ...DEFAULT_RECURRENCE, kind: "every_hours", interval: hours, hour: 0 };
  }
  const minutes = everyMs / 60_000;
  if (Number.isInteger(minutes) && (MINUTE_INTERVALS as readonly number[]).includes(minutes)) {
    return { ...DEFAULT_RECURRENCE, kind: "minutes", interval: minutes, hour: 0 };
  }
  return null;
}

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

/** Return "07:30", in the 24-hour form every locale can read unambiguously. */
export function formatTimeOfDay(hour: number, minute: number): string {
  return `${pad(hour)}:${pad(minute)}`;
}

export function weekdayLabel(weekday: number, locale: string): string {
  // 2024-01-01 was a Monday, so ISO weekday 1 lands on the 1st.
  const date = new Date(Date.UTC(2024, 0, weekday));
  return new Intl.DateTimeFormat(locale, { weekday: "long", timeZone: "UTC" }).format(date);
}

/** Return a sentence describing when a preset fires. */
export function describeRecurrence(
  recurrence: LoopRecurrence,
  t: Translate,
  locale: string,
): string {
  const time = formatTimeOfDay(recurrence.hour, recurrence.minute);
  switch (recurrence.kind) {
    case "minutes":
      return recurrence.interval === 1
        ? t("loops.schedule.everyMinute", "Every minute")
        : t("loops.schedule.everyMinutes", "Every {{count}} minutes", {
            count: recurrence.interval,
          });
    case "hourly":
      return t("loops.schedule.hourlyAt", "Every hour at :{{minute}}", {
        minute: pad(recurrence.minute),
      });
    case "every_hours":
      return recurrence.interval === 1
        ? t("loops.schedule.hourlyAt", "Every hour at :{{minute}}", {
            minute: pad(recurrence.minute),
          })
        : t("loops.schedule.everyHoursAt", "Every {{count}} hours, at :{{minute}}", {
            count: recurrence.interval,
            minute: pad(recurrence.minute),
          });
    case "daily":
      return t("loops.schedule.dailyAt", "Every day at {{time}}", { time });
    case "weekly":
      return t("loops.schedule.weeklyAt", "Every {{weekday}} at {{time}}", {
        weekday: weekdayLabel(recurrence.weekday, locale),
        time,
      });
    case "monthly":
      return recurrence.day === LAST_DAY
        ? t("loops.schedule.monthlyLastAt", "The last day of each month at {{time}}", { time })
        : t("loops.schedule.monthlyAt", "The {{day}}th of each month at {{time}}", {
            day: recurrence.day,
            time,
          });
    default:
      return "";
  }
}

/** Return a sentence describing when a loop runs, whatever its schedule shape. */
export function describeSchedule(
  job: SessionAutomationJob,
  t: Translate,
  locale: string,
): string {
  const recurrence = recurrenceOf(job);
  if (recurrence) {
    const described = describeRecurrence(recurrence, t, locale);
    return job.schedule.tz ? `${described} · ${job.schedule.tz}` : described;
  }
  if (job.schedule.kind === "every" && job.schedule.every_ms) {
    return describeInterval(job.schedule.every_ms, t);
  }
  if (job.schedule.kind === "cron" && job.schedule.expr) {
    // No preset describes it, so show the expression rather than guess.
    return job.schedule.tz ? `${job.schedule.expr} · ${job.schedule.tz}` : job.schedule.expr;
  }
  if (job.schedule.kind === "at" && job.schedule.at_ms) {
    return t("loops.schedule.once", "Once, at {{time}}", {
      time: new Date(job.schedule.at_ms).toLocaleString(locale),
    });
  }
  if (job.schedule.kind === "local") {
    return t("loops.schedule.onCommand", "On command");
  }
  return t("loops.schedule.unknown", "No schedule");
}

/** Describe a drifting interval, which fires relative to the previous run. */
export function describeInterval(everyMs: number, t: Translate): string {
  const units: Array<{ ms: number; one: [string, string]; many: [string, string] }> = [
    {
      ms: 86_400_000,
      one: ["loops.schedule.everyDay", "Every day"],
      many: ["loops.schedule.everyDays", "Every {{count}} days"],
    },
    {
      ms: 3_600_000,
      one: ["loops.schedule.everyHour", "Every hour"],
      many: ["loops.schedule.everyHours", "Every {{count}} hours"],
    },
    {
      ms: 60_000,
      one: ["loops.schedule.everyMinute", "Every minute"],
      many: ["loops.schedule.everyMinutes", "Every {{count}} minutes"],
    },
  ];
  for (const unit of units) {
    if (everyMs >= unit.ms && everyMs % unit.ms === 0) {
      const count = everyMs / unit.ms;
      const [key, fallback] = count === 1 ? unit.one : unit.many;
      return t(key, fallback, { count });
    }
  }
  return t("loops.schedule.everySeconds", "Every {{count}} seconds", {
    count: Math.max(1, Math.round(everyMs / 1000)),
  });
}

/** The loops navin runs for itself, whose schedule and ceilings are editable. */
const SYSTEM_LOOPS = ["dream", "heartbeat"] as const;

type SystemLoop = (typeof SYSTEM_LOOPS)[number];

function systemLoop(job: SessionAutomationJob): SystemLoop | null {
  const key = job.system_key ?? (job.protected ? job.id : null);
  return SYSTEM_LOOPS.find((loop) => loop === key) ?? null;
}

/**
 * True for a loop the runtime owns but the user can still retime.
 *
 * Its name and message come from the runtime and deleting it would only bring
 * it back on the next start, so the schedule, the ceilings and whether it runs
 * at all are the parts worth exposing.
 */
export function isConfigurableSystemLoop(job: SessionAutomationJob): boolean {
  return systemLoop(job) !== null;
}

/**
 * Return the display name of a loop.
 *
 * System loops are stored under the id the runtime knows them by, which is not
 * a name anybody asked for, so the client supplies the wording.
 */
export function loopLabel(job: SessionAutomationJob, t: Translate): string {
  const key = systemLoop(job);
  if (key === "dream") return t("loops.system.dream", "Memory consolidation");
  if (key === "heartbeat") return t("loops.system.heartbeat", "Watch");
  return job.name;
}

/** Return what a system loop is for, to replace "System-managed loop". */
export function loopPurpose(job: SessionAutomationJob, t: Translate): string | null {
  const key = systemLoop(job);
  if (key === "dream") {
    return t(
      "loops.system.dreamPurpose",
      "Reviews recent sessions and folds what matters into long-term memory.",
    );
  }
  if (key === "heartbeat") {
    return t(
      "loops.system.heartbeatPurpose",
      "Wakes the agent to check on pending work and anything overdue.",
    );
  }
  return null;
}

export interface SpendState {
  used: number;
  budget: number;
  /** Share of the budget already spent, 0 to 1; null when there is no ceiling. */
  ratio: number | null;
  exhausted: boolean;
}

/** Return how much of a loop's daily ceiling is spent. */
export function spendState(job: SessionAutomationJob): SpendState {
  const used = Math.max(0, job.state.tokens_today ?? 0);
  const budget = Math.max(0, job.limits?.daily_token_budget ?? 0);
  if (budget <= 0) return { used, budget: 0, ratio: null, exhausted: false };
  return {
    used,
    budget,
    ratio: Math.min(1, used / budget),
    exhausted: used >= budget,
  };
}

/** True when the service paused the loop itself rather than the user. */
export function isAutoPaused(job: SessionAutomationJob): boolean {
  return Boolean(job.state.paused_reason);
}
