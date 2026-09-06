import type { MeetingDiskRecord } from "@/lib/api";

export type MeetingDiarizationSource =
  | "provider_native"
  | "llm_fallback"
  | "manual"
  | "unknown";

export type PersistedMeeting = {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  transcript: string;
  notes: string;
  summary?: string;
  templateId: string;
  speakers: string[];
  calendarUid?: string;
  chatLog?: string;
  conferenceUrl?: string;
  startedAt?: string;
  durationSec?: number;
  diarizationSource?: MeetingDiarizationSource;
  translatedTranscript?: string;
  translatedSummary?: string;
  translationLanguage?: string;
  botCursor?: number;
  /** Id of the note this meeting was filed into (Notes module), if any. */
  noteId?: string;
};

export const MEETING_CACHE_KEY = "navin.meeting.desk.v2";

function iso(value: unknown, fallback: string): string {
  return typeof value === "string" && value ? value : fallback;
}

export function normalizeCachedMeetings(
  value: unknown,
  now: string = new Date().toISOString(),
): PersistedMeeting[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((row): row is Record<string, unknown> => Boolean(row && typeof row === "object"))
    .map((row) => ({
      id: String(row.id || crypto.randomUUID()),
      title: String(row.title || "Meeting"),
      createdAt: iso(row.createdAt, now),
      updatedAt: iso(row.updatedAt, now),
      transcript: String(row.transcript || ""),
      notes: String(row.notes || ""),
      summary: typeof row.summary === "string" ? row.summary : "",
      templateId: String(row.templateId || "standard"),
      speakers: Array.isArray(row.speakers)
        ? row.speakers.map(String).filter(Boolean)
        : [],
      calendarUid: typeof row.calendarUid === "string" ? row.calendarUid : undefined,
      chatLog: typeof row.chatLog === "string" ? row.chatLog : "",
      conferenceUrl:
        typeof row.conferenceUrl === "string" ? row.conferenceUrl : undefined,
      startedAt: typeof row.startedAt === "string" ? row.startedAt : undefined,
      durationSec:
        typeof row.durationSec === "number" && row.durationSec > 0
          ? Math.round(row.durationSec)
          : undefined,
      diarizationSource:
        row.diarizationSource === "provider_native"
        || row.diarizationSource === "llm_fallback"
        || row.diarizationSource === "manual"
          ? row.diarizationSource
          : "unknown",
      translatedTranscript:
        typeof row.translatedTranscript === "string" ? row.translatedTranscript : "",
      translatedSummary:
        typeof row.translatedSummary === "string" ? row.translatedSummary : "",
      translationLanguage:
        typeof row.translationLanguage === "string" ? row.translationLanguage : "",
      botCursor:
        typeof row.botCursor === "number" && row.botCursor >= 0
          ? Math.round(row.botCursor)
          : 0,
      noteId: typeof row.noteId === "string" && row.noteId ? row.noteId : undefined,
    }));
}

export function readMeetingCache(storage: Pick<Storage, "getItem">): PersistedMeeting[] {
  try {
    const raw =
      storage.getItem(MEETING_CACHE_KEY)
      || storage.getItem("navin.meeting.desk.v1");
    return raw ? normalizeCachedMeetings(JSON.parse(raw)) : [];
  } catch {
    return [];
  }
}

export function writeMeetingCache(
  storage: Pick<Storage, "setItem">,
  meetings: PersistedMeeting[],
): boolean {
  try {
    storage.setItem(MEETING_CACHE_KEY, JSON.stringify(meetings));
    return true;
  } catch {
    return false;
  }
}

export function toDiskRecord(meeting: PersistedMeeting): Record<string, unknown> {
  return {
    id: meeting.id,
    meta: {
      id: meeting.id,
      title: meeting.title,
      created_at: meeting.createdAt,
      updated_at: meeting.updatedAt,
      template_id: meeting.templateId,
      speakers: meeting.speakers,
      calendar_uid: meeting.calendarUid,
      conference_url: meeting.conferenceUrl,
      started_at: meeting.startedAt,
      duration_sec: meeting.durationSec,
      diarization_source: meeting.diarizationSource || "unknown",
      translated_transcript: meeting.translatedTranscript || "",
      translated_summary: meeting.translatedSummary || "",
      translation_language: meeting.translationLanguage || "",
      bot_cursor: meeting.botCursor || 0,
      note_id: meeting.noteId || "",
    },
    transcript: meeting.transcript,
    notes: meeting.notes,
    summary: meeting.summary || "",
    chat: meeting.chatLog ? [{ role: "meeting", content: meeting.chatLog }] : [],
  };
}

export function fromDiskRecord(record: MeetingDiskRecord): PersistedMeeting {
  const meta = record.meta || { id: record.id };
  const chat = Array.isArray(record.chat) ? record.chat : [];
  const chatLog = chat
    .map((row) =>
      row && typeof row === "object" && "content" in row
        ? String((row as { content: unknown }).content || "")
        : "",
    )
    .filter(Boolean)
    .join("\n\n");
  return normalizeCachedMeetings([
    {
      id: record.id,
      title: meta.title,
      createdAt: meta.created_at,
      updatedAt: meta.updated_at,
      transcript: record.transcript,
      notes: record.notes,
      summary: record.summary,
      templateId: meta.template_id,
      speakers: meta.speakers,
      calendarUid: meta.calendar_uid,
      conferenceUrl: meta.conference_url,
      startedAt: meta.started_at,
      durationSec: meta.duration_sec,
      diarizationSource: meta.diarization_source,
      translatedTranscript: meta.translated_transcript,
      translatedSummary: meta.translated_summary,
      translationLanguage: meta.translation_language,
      botCursor: meta.bot_cursor,
      noteId: meta.note_id,
      chatLog,
    },
  ])[0];
}

export function meetingMatches(meeting: PersistedMeeting, query: string): boolean {
  const terms = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  if (!terms.length) return true;
  const haystack = [
    meeting.title,
    meeting.transcript,
    meeting.notes,
    meeting.summary,
    meeting.chatLog,
  ].join("\n").toLocaleLowerCase();
  return terms.every((term) => haystack.includes(term));
}

export function highlightParts(
  text: string,
  query: string,
): Array<{ text: string; match: boolean }> {
  const needle = query.trim();
  if (!needle) return [{ text, match: false }];
  const escaped = needle.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return text.split(new RegExp(`(${escaped})`, "gi")).filter(Boolean).map((part) => ({
    text: part,
    match: part.toLocaleLowerCase() === needle.toLocaleLowerCase(),
  }));
}

export function base64ToBlob(base64: string, mime: string): Blob {
  const bytes = Uint8Array.from(atob(base64), (char) => char.charCodeAt(0));
  return new Blob([bytes], { type: mime });
}

export function audioSeekTarget<T extends { offset_ms?: number }>(
  segments: T[],
  seconds: number,
): { segment: T; relativeSeconds: number } | null {
  const segment = [...segments].reverse().find(
    (row) => (row.offset_ms || 0) / 1000 <= seconds,
  ) || segments[0];
  if (!segment) return null;
  return {
    segment,
    relativeSeconds: Math.max(0, seconds - (segment.offset_ms || 0) / 1000),
  };
}

export function botShouldPoll(
  state: string,
): state is "starting" | "joining" | "waiting" | "live" {
  return ["starting", "joining", "waiting", "live"].includes(state);
}
