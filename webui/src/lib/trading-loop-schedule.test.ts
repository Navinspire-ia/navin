// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import { LAST_DAY } from "@/lib/loop-schedule";
import {
  describeTradingSchedule,
  normalizeTradingSchedule,
  parseTimeInput,
} from "@/lib/trading-loop-schedule";

const tx = (key: string, fallback: string, values?: Record<string, string | number>) => {
  let text = fallback;
  for (const [name, value] of Object.entries(values || {})) {
    text = text.replaceAll(`{{${name}}}`, String(value));
  }
  return `${key}:${text}`;
};

describe("trading-loop-schedule", () => {
  it("fills a daily 09:00 schedule when the payload is empty", () => {
    expect(normalizeTradingSchedule(null)).toEqual({
      kind: "daily",
      hour: 9,
      minute: 0,
      weekday: 1,
      day: 1,
      tz: null,
      expr: undefined,
    });
  });

  it("keeps weekend and monthly last-day values", () => {
    expect(
      normalizeTradingSchedule({
        kind: "weekend",
        hour: 21,
        minute: 30,
        day: LAST_DAY,
      }),
    ).toMatchObject({ kind: "weekend", hour: 21, minute: 30, day: LAST_DAY });
  });

  it("reads a clock time without changing the cadence", () => {
    const current = normalizeTradingSchedule({ kind: "weekdays", hour: 8, minute: 0 });
    expect(parseTimeInput("14:45", current)).toMatchObject({
      kind: "weekdays",
      hour: 14,
      minute: 45,
    });
  });

  it("describes each cadence in one sentence", () => {
    expect(
      describeTradingSchedule(normalizeTradingSchedule({ kind: "daily", hour: 9 }), tx, "en"),
    ).toContain("Every day at 09:00");
    expect(
      describeTradingSchedule(normalizeTradingSchedule({ kind: "weekdays", hour: 7, minute: 15 }), tx, "en"),
    ).toContain("Weekdays at 07:15");
    expect(
      describeTradingSchedule(normalizeTradingSchedule({ kind: "weekend", hour: 10 }), tx, "en"),
    ).toContain("Weekends at 10:00");
    expect(
      describeTradingSchedule(
        normalizeTradingSchedule({ kind: "monthly", day: LAST_DAY, hour: 18 }),
        tx,
        "en",
      ),
    ).toContain("last day of each month at 18:00");
  });
});
