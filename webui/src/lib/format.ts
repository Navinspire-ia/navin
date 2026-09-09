// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import i18n, { currentLocale } from "@/i18n";

const LOW_INFORMATION_TITLE_PREVIEWS = new Set([
  "hi",
  "hello",
  "hey",
  "hello nano",
  "hello navin",
  "hi nano",
  "hi navin",
  "你好",
  "您好",
  "嗨",
  "哈喽",
  "哈啰",
  "在吗",
]);

function isLowInformationTitlePreview(text: string): boolean {
  const normalized = text.toLowerCase().replace(/[.!?。！？~～\s]+$/g, "").trim();
  return (
    normalized.startsWith("/") ||
    LOW_INFORMATION_TITLE_PREVIEWS.has(normalized)
  );
}

/** Truncate the first user message into a chat title. */
export function deriveTitle(preview: string | undefined, fallback: string): string {
  if (!preview) return fallback;
  const oneLine = preview.replace(/\s+/g, " ").trim();
  if (!oneLine) return fallback;
  if (isLowInformationTitlePreview(oneLine)) return fallback;
  return oneLine.length > 60 ? `${oneLine.slice(0, 57)}…` : oneLine;
}

/** Loose ISO-or-epoch parser; returns ``null`` for missing/invalid input. */
function parseDate(value: string | number | null | undefined): Date | null {
  if (value === null || value === undefined || value === "") return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

const RELATIVE_THRESHOLDS: [number, Intl.RelativeTimeFormatUnit][] = [
  [60, "second"],
  [60, "minute"],
  [24, "hour"],
  [7, "day"],
  [4.345, "week"],
  [12, "month"],
  [Number.POSITIVE_INFINITY, "year"],
];

const relativeTimeFormatters = new Map<string, Intl.RelativeTimeFormat>();
const dateTimeFormatters = new Map<string, Intl.DateTimeFormat>();

function activeLocale(locale?: string): string {
  return locale || i18n.resolvedLanguage || i18n.language || currentLocale();
}

function relativeTimeFormatter(locale: string): Intl.RelativeTimeFormat {
  const existing = relativeTimeFormatters.get(locale);
  if (existing) return existing;
  const formatter = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
  relativeTimeFormatters.set(locale, formatter);
  return formatter;
}

function dateTimeFormatter(locale: string): Intl.DateTimeFormat {
  const existing = dateTimeFormatters.get(locale);
  if (existing) return existing;
  const formatter = new Intl.DateTimeFormat(locale, {
    dateStyle: "medium",
    timeStyle: "short",
  });
  dateTimeFormatters.set(locale, formatter);
  return formatter;
}

export function relativeTime(
  value: string | number | null | undefined,
  locale?: string,
): string {
  const date = parseDate(value);
  if (!date) return "";
  let delta = (date.getTime() - Date.now()) / 1000;
  const formatter = relativeTimeFormatter(activeLocale(locale));
  for (const [step, unit] of RELATIVE_THRESHOLDS) {
    if (Math.abs(delta) < step) {
      return formatter.format(Math.round(delta), unit);
    }
    delta /= step;
  }
  return formatter.format(Math.round(delta), "year");
}

/**
 * Relative age in Cursor sidebar style: ``now``, ``5m``, ``18h``, ``1d``.
 * Compact and language-agnostic so the chat list stays dense.
 * Switches to days from 24h onward (``1d``, ``2d``, …).
 */
export function compactRelativeTime(
  value: string | number | null | undefined,
): string {
  const date = parseDate(value);
  if (!date) return "";
  const seconds = Math.max(0, Math.round((Date.now() - date.getTime()) / 1000));
  if (seconds < 60) return "now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.round(hours / 24);
  if (days < 14) return `${days}d`;
  const weeks = Math.round(days / 7);
  if (weeks < 8) return `${weeks}w`;
  const months = Math.round(days / 30);
  if (months < 24) return `${months}mo`;
  return `${Math.round(days / 365)}y`;
}

export function fmtDateTime(
  value: string | number | null | undefined,
  locale?: string,
): string {
  const date = parseDate(value);
  return date ? dateTimeFormatter(activeLocale(locale)).format(date) : "";
}

/** Human-readable turn duration (wall-clock), locale-aware via ``Intl`` (seconds/minutes). */
export function formatTurnLatency(ms: number, locale?: string): string {
  const loc = activeLocale(locale);
  const msClamped = Math.max(0, ms);
  const secTotal = msClamped / 1000;
  if (secTotal < 60) {
    return new Intl.NumberFormat(loc, {
      style: "unit",
      unit: "second",
      unitDisplay: "narrow",
      maximumFractionDigits: secTotal < 10 ? 1 : 0,
      minimumFractionDigits: 0,
    }).format(secTotal);
  }
  const wholeMin = Math.floor(secTotal / 60);
  const remSec = Math.max(0, Math.round(secTotal - wholeMin * 60));
  const minStr = new Intl.NumberFormat(loc, {
    style: "unit",
    unit: "minute",
    unitDisplay: "narrow",
  }).format(wholeMin);
  const secStr = new Intl.NumberFormat(loc, {
    style: "unit",
    unit: "second",
    unitDisplay: "narrow",
    maximumFractionDigits: 0,
  }).format(remSec);
  return `${minStr}\u00a0${secStr}`;
}
