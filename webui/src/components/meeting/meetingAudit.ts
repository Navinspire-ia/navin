/** Local GDPR-oriented audit trail for Meeting desk actions. */

import { saveTextFile } from "@/lib/save-blob";

export type MeetingAuditEntry = {
  at: string;
  meetingId: string;
  action: string;
  detail?: string;
};

const STORAGE_KEY = "navin.meeting.audit.v1";
const MAX_ENTRIES = 2000;

export function readAuditLog(): MeetingAuditEntry[] {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as MeetingAuditEntry[];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function appendAudit(entry: Omit<MeetingAuditEntry, "at"> & { at?: string }) {
  const next: MeetingAuditEntry = {
    at: entry.at ?? new Date().toISOString(),
    meetingId: entry.meetingId,
    action: entry.action,
    detail: entry.detail,
  };
  const all = [...readAuditLog(), next].slice(-MAX_ENTRIES);
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(all));
  } catch {
    // ignore
  }
  return next;
}

export function auditForMeeting(meetingId: string): MeetingAuditEntry[] {
  return readAuditLog().filter((row) => row.meetingId === meetingId);
}

export function downloadAuditMarkdown(meetingId: string, title: string) {
  const rows = auditForMeeting(meetingId);
  const body = [
    `# Audit trail - ${title}`,
    "",
    "Local-only log (browser). Not uploaded to any third-party meeting cloud.",
    "",
    ...rows.map((row) => `- ${row.at} · **${row.action}**${row.detail ? ` - ${row.detail}` : ""}`),
    "",
  ].join("\n");
  downloadTextFile(
    `meeting-audit-${meetingId.slice(0, 8)}.md`,
    body,
    "text/markdown;charset=utf-8",
  );
}

export function downloadTextFile(filename: string, content: string, mime: string) {
  saveTextFile(filename, content, mime);
}
