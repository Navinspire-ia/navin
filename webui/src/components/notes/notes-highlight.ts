// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

export interface HighlightPart {
  text: string;
  highlighted: boolean;
}

export function highlightMatch(text: string, query: string): HighlightPart[] {
  const needle = query.trim();
  if (!needle) return [{ text, highlighted: false }];
  const index = text.toLocaleLowerCase().indexOf(needle.toLocaleLowerCase());
  if (index < 0) return [{ text, highlighted: false }];
  return [
    { text: text.slice(0, index), highlighted: false },
    { text: text.slice(index, index + needle.length), highlighted: true },
    { text: text.slice(index + needle.length), highlighted: false },
  ].filter((part) => part.text.length > 0);
}

export function formatSnapshotStamp(stamp: string, locale: string): string {
  const match = stamp.match(
    /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})(\d{0,6})$/,
  );
  if (!match) return stamp;
  const [, year, month, day, hour, minute, second, fraction] = match;
  const milliseconds = Number((fraction || "0").slice(0, 3).padEnd(3, "0"));
  const date = new Date(
    Date.UTC(
      Number(year),
      Number(month) - 1,
      Number(day),
      Number(hour),
      Number(minute),
      Number(second),
      milliseconds,
    ),
  );
  return new Intl.DateTimeFormat(locale, {
    dateStyle: "medium",
    timeStyle: "medium",
  }).format(date);
}
