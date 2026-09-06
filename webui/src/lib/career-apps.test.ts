import { describe, expect, it } from "vitest";

import type { CareerApplication, CareerOpportunity } from "@/lib/career-api";
import {
  applicationStatusKey,
  followupDue,
  formatCareerDate,
  listTrackedApplications,
} from "@/lib/career-apps";

function job(partial: Partial<CareerOpportunity> & { id: string }): CareerOpportunity {
  return {
    source: "web",
    title: "Data Engineer",
    stage: "discovered",
    ...partial,
  };
}

describe("tracked applications", () => {
  it("keeps packs and ignores raw discoveries", () => {
    const packed: CareerApplication = {
      id: "app-1",
      opportunity_id: "job-1",
      title: "Data Engineer",
      pack_ready: true,
      stage: "ready",
    };
    const rows = listTrackedApplications(
      [packed],
      [job({ id: "skip", stage: "discovered" }), job({ id: "job-1", title: "Data Engineer", company: "Navin", stage: "ready" })],
      "freelance",
    );
    expect(rows).toHaveLength(1);
    expect(rows[0]?.company).toBe("Navin");
    expect(applicationStatusKey(rows[0]!)).toBe("pack");
  });

  it("marks employer-opened rows and dates a J+3 follow-up from applied_at seconds", () => {
    const opened: CareerApplication = {
      id: "app-2",
      opportunity_id: "job-2",
      employer_opened: true,
      stage: "matched",
      applied_at: 1_700_000_000,
    };
    const rows = listTrackedApplications([opened], [], "jobs");
    expect(applicationStatusKey(rows[0]!)).toBe("opened");
    expect(followupDue(1_700_000_000, 3)).toBe(1_700_000_000 + 3 * 86400);
    expect(formatCareerDate(1_700_000_000, "en-GB", "-")).toMatch(/2023/);
  });
});
