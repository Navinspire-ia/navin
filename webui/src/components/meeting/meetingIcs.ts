// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/** Minimal ICS parser for upcoming calendar events (local-only). */

export type CalendarEvent = {
  uid: string;
  summary: string;
  start: string; // ISO
  end?: string;
  location?: string;
  description?: string;
  /** Conference link found in the event, used by the Join button. */
  url?: string;
};

const CONFERENCE_HOSTS =
  /(zoom\.us|meet\.google\.com|teams\.microsoft\.com|teams\.live\.com|webex\.com|whereby\.com|meet\.jit\.si|gotomeeting\.com|bluejeans\.com|livestorm\.co|ringcentral\.com)/i;

/** First conference link in the event, falling back to any https link. */
export function meetingJoinUrl(event: CalendarEvent): string | null {
  if (event.url) return event.url;
  const haystack = [event.location, event.description].filter(Boolean).join("\n");
  const links = haystack.match(/https?:\/\/[^\s<>"'\]),]+/g) ?? [];
  return links.find((link) => CONFERENCE_HOSTS.test(link)) ?? links[0] ?? null;
}

function unfoldIcs(text: string): string[] {
  const lines = text.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
  const out: string[] = [];
  for (const line of lines) {
    if ((line.startsWith(" ") || line.startsWith("\t")) && out.length) {
      out[out.length - 1] += line.slice(1);
    } else {
      out.push(line);
    }
  }
  return out;
}

function parseIcsDate(value: string): string | null {
  const raw = value.trim();
  // DATE: 20260810
  if (/^\d{8}$/.test(raw)) {
    const y = Number(raw.slice(0, 4));
    const m = Number(raw.slice(4, 6));
    const d = Number(raw.slice(6, 8));
    return new Date(Date.UTC(y, m - 1, d, 9, 0, 0)).toISOString();
  }
  // DATETIME UTC: 20260810T140000Z
  const utc = raw.match(/^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/);
  if (utc) {
    const [, y, mo, d, h, mi, s] = utc;
    return new Date(
      Date.UTC(Number(y), Number(mo) - 1, Number(d), Number(h), Number(mi), Number(s)),
    ).toISOString();
  }
  // Local DATETIME without Z - treat as local
  const local = raw.match(/^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})$/);
  if (local) {
    const [, y, mo, d, h, mi, s] = local;
    return new Date(
      Number(y),
      Number(mo) - 1,
      Number(d),
      Number(h),
      Number(mi),
      Number(s),
    ).toISOString();
  }
  return null;
}

function unescapeIcs(value: string): string {
  return value
    .replace(/\\n/gi, "\n")
    .replace(/\\,/g, ",")
    .replace(/\\;/g, ";")
    .replace(/\\\\/g, "\\");
}

export function parseIcsEvents(icsText: string): CalendarEvent[] {
  const lines = unfoldIcs(icsText);
  const events: CalendarEvent[] = [];
  let current: Partial<CalendarEvent> | null = null;

  for (const line of lines) {
    if (line === "BEGIN:VEVENT") {
      current = {};
      continue;
    }
    if (line === "END:VEVENT") {
      if (current?.start && current.summary) {
        const event: CalendarEvent = {
          uid: current.uid || `${current.start}-${current.summary}`,
          summary: current.summary,
          start: current.start,
          end: current.end,
          location: current.location,
          description: current.description,
          url: current.url,
        };
        events.push({ ...event, url: meetingJoinUrl(event) ?? undefined });
      }
      current = null;
      continue;
    }
    if (!current) continue;
    const idx = line.indexOf(":");
    if (idx < 0) continue;
    const keyPart = line.slice(0, idx);
    const value = unescapeIcs(line.slice(idx + 1));
    const key = keyPart.split(";")[0]?.toUpperCase() ?? "";
    if (key === "UID") current.uid = value;
    if (key === "SUMMARY") current.summary = value;
    if (key === "LOCATION") current.location = value;
    if (key === "DESCRIPTION") current.description = value;
    if ((key === "URL" || key === "X-GOOGLE-CONFERENCE") && /^https?:\/\//i.test(value)) {
      current.url = value;
    }
    if (key === "DTSTART") {
      const iso = parseIcsDate(value);
      if (iso) current.start = iso;
    }
    if (key === "DTEND") {
      const iso = parseIcsDate(value);
      if (iso) current.end = iso;
    }
  }

  return events.sort(
    (a, b) => new Date(a.start).getTime() - new Date(b.start).getTime(),
  );
}

export function upcomingEvents(
  events: CalendarEvent[],
  options: { now?: Date; withinHours?: number } = {},
): CalendarEvent[] {
  const { now = new Date(), withinHours = 72 } = options;
  const start = now.getTime();
  const end = start + withinHours * 3600_000;
  return events.filter((event) => {
    const t = new Date(event.start).getTime();
    return t >= start - 15 * 60_000 && t <= end;
  });
}

export function eventsStartingSoon(
  events: CalendarEvent[],
  options: { now?: Date; withinMinutes?: number } = {},
): CalendarEvent[] {
  const { now = new Date(), withinMinutes = 5 } = options;
  const start = now.getTime();
  const end = start + withinMinutes * 60_000;
  return events.filter((event) => {
    const t = new Date(event.start).getTime();
    return t >= start && t <= end;
  });
}
