// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import { classifyReply, jobIsRelevant, scoreOpportunity, stripHtml } from "@/lib/career-match";

describe("career match score", () => {
  it("raises score for primary market and stack", () => {
    const scored = scoreOpportunity(
      {
        id: "job-1",
        source: "remotive",
        title: "Senior Data Engineer freelance",
        description: "Python Spark Databricks AWS remote Paris",
        country: "FR",
        remote: "remote",
        compensation: 700,
        track: "freelance",
        stage: "discovered",
        stack: ["Python", "Spark"],
        languages: ["fr", "en"],
      },
      {
        titles: ["Data Engineer"],
        countries_primary: ["FR", "AE"],
        stack: ["Python", "Spark", "Databricks", "AWS"],
        work_mode: "remote",
        min_rate: 650,
        languages: ["fr", "en"],
        track: "freelance",
      },
    );
    expect(scored.match_score || 0).toBeGreaterThanOrEqual(80);
    expect(scored.bucket).toBe("perfect");
  });

  it("skips excluded countries", () => {
    const scored = scoreOpportunity(
      {
        id: "job-2",
        source: "web",
        title: "Data Engineer",
        description: "Python Spark",
        country: "GB",
        track: "freelance",
        stage: "discovered",
      },
      {
        titles: ["Data Engineer"],
        countries_excluded: ["GB"],
        stack: ["Python"],
        track: "freelance",
      },
    );
    expect(scored.match_score || 0).toBeLessThanOrEqual(24);
    expect(scored.bucket).toBe("skip");
  });

  it("classifies recruiter replies", () => {
    expect(classifyReply("Recruiter wants a call next Tuesday")).toBe("interview");
    expect(classifyReply("Unfortunately the position is filled")).toBe("rejected");
    expect(classifyReply("Please provide expected daily rate")).toBe("need_response");
  });

  it("strips HTML job descriptions into readable text", () => {
    const text = stripHtml("<p>Are you a talented <strong>Senior Data Engineer</strong>?</p><ul><li>Python</li></ul>");
    expect(text).toContain("Are you a talented Senior Data Engineer");
    expect(text).not.toContain("<p>");
    expect(text).not.toContain("<strong>");
    expect(text).toContain("Python");
  });

  it("strips entity-encoded tags left in stored descriptions", () => {
    const text = stripHtml("&lt;p&gt;Lemon.io mission&lt;/p&gt;");
    expect(text).toContain("Lemon.io mission");
    expect(text).not.toContain("<p>");
  });

  it("skips off-role titles when the brief is AI Engineer", () => {
    const profile = { titles: ["AI Engineer"], countries_primary: ["AE", "FR"], track: "freelance" };
    const junk = scoreOpportunity(
      { id: "junk", source: "remotive", title: "Sales Jedi", description: "engineer culture remote", country: "REMOTE", remote: "remote", track: "freelance", stage: "discovered" },
      profile,
    );
    expect(junk.match_score || 0).toBeLessThanOrEqual(35);
    expect(junk.bucket).toBe("skip");
    const citizen = scoreOpportunity(
      {
        id: "us",
        source: "web",
        title: "AI Engineer || United States citizenship is required - Dice",
        description: "US only",
        country: "US",
        track: "jobs",
        stage: "discovered",
      },
      profile,
    );
    expect(citizen.match_score || 0).toBeLessThanOrEqual(35);
    expect(jobIsRelevant({ title: "Remote Office Assistant", description: "" }, profile)).toBe(false);
    expect(jobIsRelevant({ title: "AI Engineer remote Dubai", description: "" }, profile)).toBe(true);
  });
});
