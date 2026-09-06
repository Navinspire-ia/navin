import { LAST_DAY, formatTimeOfDay, weekdayLabel } from "@/lib/loop-schedule";
import type { TradingLoopKind, TradingLoopSchedule } from "@/lib/trading-api";

export const TRADING_LOOP_KINDS: readonly TradingLoopKind[] = [
  "daily",
  "weekdays",
  "weekend",
  "weekly",
  "monthly",
];

export const DEFAULT_TRADING_SCHEDULE: TradingLoopSchedule = {
  kind: "daily",
  hour: 9,
  minute: 0,
  weekday: 1,
  day: 1,
};

type Translate = (key: string, fallback: string, values?: Record<string, string | number>) => string;

function clamp(value: number, low: number, high: number, fallback: number): number {
  if (!Number.isFinite(value)) return fallback;
  return Math.min(high, Math.max(low, Math.trunc(value)));
}

export function browserTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "";
  } catch {
    return "";
  }
}

export function normalizeTradingSchedule(
  raw: Partial<TradingLoopSchedule> | null | undefined,
): TradingLoopSchedule {
  const kind = TRADING_LOOP_KINDS.includes(raw?.kind as TradingLoopKind)
    ? (raw?.kind as TradingLoopKind)
    : "daily";
  const day = raw?.day === LAST_DAY ? LAST_DAY : clamp(Number(raw?.day ?? 1), 1, 28, 1);
  return {
    kind,
    hour: clamp(Number(raw?.hour ?? 9), 0, 23, 9),
    minute: clamp(Number(raw?.minute ?? 0), 0, 59, 0),
    weekday: clamp(Number(raw?.weekday ?? 1), 1, 7, 1),
    day,
    tz: raw?.tz || null,
    expr: raw?.expr,
  };
}

export function parseTimeInput(value: string, current: TradingLoopSchedule): TradingLoopSchedule {
  const match = /^(\d{1,2}):(\d{2})$/.exec(value.trim());
  if (!match) return current;
  return normalizeTradingSchedule({
    ...current,
    hour: Number(match[1]),
    minute: Number(match[2]),
  });
}

export function describeTradingSchedule(
  schedule: TradingLoopSchedule,
  tx: Translate,
  locale: string,
): string {
  const time = formatTimeOfDay(schedule.hour, schedule.minute);
  switch (schedule.kind) {
    case "daily":
      return tx("scheduleDescDaily", "Every day at {{time}}", { time });
    case "weekdays":
      return tx("scheduleDescWeekdays", "Weekdays at {{time}}", { time });
    case "weekend":
      return tx("scheduleDescWeekend", "Weekends at {{time}}", { time });
    case "weekly":
      return tx("scheduleDescWeekly", "Every {{weekday}} at {{time}}", {
        weekday: weekdayLabel(schedule.weekday || 1, locale),
        time,
      });
    case "monthly":
      return schedule.day === LAST_DAY
        ? tx("scheduleDescMonthlyLast", "The last day of each month at {{time}}", { time })
        : tx("scheduleDescMonthly", "The {{day}} of each month at {{time}}", {
            day: String(schedule.day || 1),
            time,
          });
    default:
      return tx("scheduleNone", "No schedule yet");
  }
}

export function formatNextDue(ts: number | undefined, locale: string): string {
  if (!ts || ts <= 0) return "";
  return new Date(ts * 1000).toLocaleString(locale, {
    weekday: "short",
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}
