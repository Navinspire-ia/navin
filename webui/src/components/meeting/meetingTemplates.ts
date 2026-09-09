// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/** Built-in + user summary templates for the Meeting studio. */

export type MeetingSummaryTemplate = {
  id: string;
  name: string;
  builtin: boolean;
  /** Instructions injected into /meeting prompts. */
  instructions: string;
};

const STORAGE_KEY = "navin.meeting.templates.v1";

export const BUILTIN_MEETING_TEMPLATES: MeetingSummaryTemplate[] = [
  {
    id: "standard",
    name: "Standard minutes",
    builtin: true,
    instructions:
      "Full minutes, in this order: Summary (5 bullets max); Attendees and roles; "
      + "Discussion timeline (one entry per stage of the meeting, in order, citing "
      + "[mm:ss] timecodes when the transcript has them); Decisions; Proposals "
      + "(who proposed what, and what happened to each proposal); Positions and "
      + "opinions (per participant, only what they actually voiced); Actions "
      + "(owner + deadline table); Open risks; Open questions; Next steps and "
      + "next meeting.",
  },
  {
    id: "executive",
    name: "Executive brief",
    builtin: true,
    instructions:
      "Write a one-page executive brief: Outcome in 3 bullets, Decisions, Asks for leadership, Risks, Next checkpoint. Max ~300 words.",
  },
  {
    id: "discovery",
    name: "Sales discovery",
    builtin: true,
    instructions:
      "Discovery template: ICP fit, pains (verbatim), buying signals, objections, champions, next step with date. Label facts vs hypotheses.",
  },
  {
    id: "standup",
    name: "Stand-up / sync",
    builtin: true,
    instructions:
      "Stand-up template: Yesterday / Today / Blockers per person when labeled; shared risks; owners for blockers.",
  },
  {
    id: "interview",
    name: "Interview notes",
    builtin: true,
    instructions:
      "Interview template: Candidate/role, strengths with evidence quotes, gaps, culture signals, recommendation (hire/hold/no) with rationale.",
  },
];

export function readCustomTemplates(): MeetingSummaryTemplate[] {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as MeetingSummaryTemplate[];
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((row) => row && typeof row.id === "string" && typeof row.name === "string")
      .map((row) => ({
        id: row.id,
        name: row.name,
        builtin: false,
        instructions: String(row.instructions || ""),
      }));
  } catch {
    return [];
  }
}

export function writeCustomTemplates(templates: MeetingSummaryTemplate[]) {
  try {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify(templates.filter((row) => !row.builtin)),
    );
  } catch {
    // ignore
  }
}

export function allMeetingTemplates(): MeetingSummaryTemplate[] {
  return [...BUILTIN_MEETING_TEMPLATES, ...readCustomTemplates()];
}

export function findMeetingTemplate(id: string | undefined): MeetingSummaryTemplate {
  const all = allMeetingTemplates();
  return all.find((row) => row.id === id) ?? BUILTIN_MEETING_TEMPLATES[0]!;
}
