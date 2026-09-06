import type { CareerApplication, CareerOpportunity, CareerTrack } from "@/lib/career-api";

export type ApplicationView = {
  app: CareerApplication;
  job: CareerOpportunity | null;
  title: string;
  company: string;
  source: string;
  stage: string;
  appliedAt?: number;
  updatedAt?: number;
  nextAction: string;
};

const SENT = new Set(["applied", "replied", "interview", "offer", "won"]);

function jobFor(app: CareerApplication, jobs: CareerOpportunity[]): CareerOpportunity | null {
  const id = String(app.opportunity_id || "").trim();
  if (!id) return null;
  return jobs.find((row) => row.id === id) || null;
}

export function applicationStatusKey(view: ApplicationView): "pack" | "opened" | string {
  if (SENT.has(view.stage)) return view.stage;
  if (view.app.employer_opened) return "opened";
  if (view.app.pack_ready || view.stage === "ready") return "pack";
  return view.stage || "ready";
}

/** Applications the user started - not the scored search book. */
export function listTrackedApplications(
  apps: CareerApplication[],
  jobs: CareerOpportunity[],
  track: CareerTrack | string = "freelance",
): ApplicationView[] {
  const rows: ApplicationView[] = [];
  for (const app of apps) {
    const job = jobFor(app, jobs);
    if (job) {
      const jobTrack = String(job.track || "freelance");
      if (track === "freelance" && jobTrack === "jobs") continue;
      if (track === "jobs" && jobTrack === "freelance") continue;
    }
    const stage = String(app.stage || job?.stage || "ready");
    const started =
      Boolean(app.pack_ready) ||
      Boolean(app.employer_opened) ||
      SENT.has(stage) ||
      Boolean(app.applied_at) ||
      stage === "ready";
    if (!started) continue;
    rows.push({
      app,
      job,
      title: app.title || job?.title || "",
      company: app.company || job?.company || "",
      source: app.source || job?.source || "",
      stage,
      appliedAt: app.applied_at || undefined,
      updatedAt: app.updated_at || undefined,
      nextAction: app.next_action || "",
    });
  }
  return rows;
}

export function formatCareerDate(
  ts: number | null | undefined,
  locale: string,
  unknown: string,
): string {
  const n = Number(ts || 0);
  if (!n) return unknown;
  const ms = n > 1e12 ? n : n * 1000;
  try {
    return new Intl.DateTimeFormat(locale, { dateStyle: "medium" }).format(new Date(ms));
  } catch {
    return unknown;
  }
}

export function followupDue(appliedAt: number | null | undefined, days = 3): number | null {
  const n = Number(appliedAt || 0);
  if (!n) return null;
  const ms = n > 1e12 ? n : n * 1000;
  return Math.floor((ms + days * 86_400_000) / 1000);
}
