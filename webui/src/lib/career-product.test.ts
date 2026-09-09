// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import { authorizedPortals } from "@/lib/career-api";
import {
  classifyReply,
  computeCareerKpis,
  dedupeApplications,
  draftFollowup,
  normalizeJobUrl,
  scoreOpportunity,
  sha1Hex,
  stableJobId,
} from "@/lib/career-match";
import { BILLABLE_DAYS, careerMoney } from "@/lib/career-money";
import { officialSearchPack } from "@/lib/career-portals";
import { webSearchQueries } from "@/lib/career-web";

const GOLDEN_PROFILE = {
  titles: ["Data Engineer"],
  track: "freelance" as const,
  stack: ["Python", "Spark", "AWS"],
  languages: ["fr", "en"],
  work_mode: "remote",
  min_rate: 650,
  countries_primary: ["FR"],
  countries_secondary: ["US"],
  countries_excluded: ["GB"],
  country_weights: { FR: 100, US: 50 },
  strengths: ["Spark"],
  currency: "EUR",
};

const GOLDEN_JOB = {
  id: "job-golden",
  source: "remotive",
  title: "Senior Data Engineer freelance",
  description: "Python Spark AWS remote Paris fr en",
  remote: "remote",
  compensation: 700,
  track: "freelance",
  stack: ["Python", "Spark", "AWS"],
  languages: ["fr", "en"],
  stage: "discovered",
};

const MARKETS = ["FR", "BE", "CH", "GB", "US", "CA", "DE", "NL", "AE", "SA", "QA", "KW", "OM", "BH"] as const;

describe("career product scores match the agent desk", () => {
  it("uses the same golden numbers as the Python desk", () => {
    const paris = scoreOpportunity({ ...GOLDEN_JOB, country: "FR" }, GOLDEN_PROFILE);
    const usa = scoreOpportunity({ ...GOLDEN_JOB, country: "US" }, GOLDEN_PROFILE);
    const uk = scoreOpportunity({ ...GOLDEN_JOB, country: "GB" }, GOLDEN_PROFILE);
    const cdi = scoreOpportunity(
      { ...GOLDEN_JOB, id: "job-cdi", title: "Senior Data Engineer", country: "FR", track: "jobs" },
      GOLDEN_PROFILE,
    );
    expect(paris.match_score).toBe(100);
    expect(paris.bucket).toBe("perfect");
    expect(usa.match_score).toBe(91);
    expect(uk.match_score).toBe(24);
    expect(uk.bucket).toBe("skip");
    expect(cdi.match_score).toBe(88);
    expect(Number(paris.match_score)).toBeGreaterThan(Number(usa.match_score));
  });

  it("keeps an excluded market at 24 even with strength bonus", () => {
    const scored = scoreOpportunity(
      { ...GOLDEN_JOB, country: "GB" },
      { ...GOLDEN_PROFILE, strengths: ["Spark", "Python", "AWS", "remote"] },
    );
    expect(scored.match_score).toBe(24);
  });
});

describe("career product desk surfaces", () => {
  it("opens LinkedIn first for every priority market", () => {
    for (const iso of MARKETS) {
      const pack = officialSearchPack("Senior Data Engineer", [iso], "freelance", "remote");
      expect(pack[0]?.id).toBe(`li-${iso}`);
      expect(pack[0]?.url).toContain("linkedin.com/jobs/search");
      expect(pack.some((row) => row.kind === "board" || row.kind === "api")).toBe(true);
    }
    const multi = authorizedPortals("Senior Data Engineer", ["FR", "AE", "SA"], "freelance", "remote");
    expect(multi.some((row) => row.id === "li-FR")).toBe(true);
    expect(multi.some((row) => row.url.includes("dubaicareers.ae"))).toBe(true);
    expect(multi.some((row) => row.url.includes("chooseyourboss.com"))).toBe(true);
  });

  it("searches each market in its own languages", () => {
    const blob = webSearchQueries("Senior Data Engineer", ["FR", "BE", "CH", "AE", "DE"], "freelance")
      .map((row) => row.query)
      .join(" ");
    expect(blob).toContain("offre OR emploi OR mission");
    expect(blob).toContain("vacature OR freelance");
    expect(blob).toContain("Stelle OR Freiberuflich OR Job");
    expect(blob).toContain("وظائف OR jobs");
    expect(blob).toContain("site:chooseyourboss.com");
    expect(blob).toContain("site:stepstone.be");
  });

  it("turns a 650 EUR day rate into a real monthly number", () => {
    const money = careerMoney(GOLDEN_PROFILE, [{ ...GOLDEN_JOB, country: "FR", match_score: 100, stage: "ready" }], "freelance");
    expect(money.monthly).toBe(650 * BILLABLE_DAYS);
    expect(money.yearly).toBe(650 * BILLABLE_DAYS * 12);
    expect(money.atOrAboveGoal).toBe(1);
    expect(money.strong).toBe(1);
    expect(money.inFlight).toBe(1);
  });

  it("keeps inbox and follow-up copy aligned with the desk", () => {
    expect(classifyReply("We would like a technical interview next Tuesday.")).toBe("interview");
    const j3 = draftFollowup({ title: "Senior Data Engineer", company: "Paris Co" }, "j3");
    const j7 = draftFollowup({ title: "Senior Data Engineer", company: "Paris Co" }, "j7");
    expect(j3).toContain("follow up");
    expect(j7).toContain("second time");
    expect(j3).not.toContain("\u2014");
    expect(j7).not.toContain("\u2013");
    const kpis = computeCareerKpis(
      [
        { ...GOLDEN_JOB, country: "FR", match_score: 100, stage: "interview" },
        { ...GOLDEN_JOB, id: "job-uk", country: "GB", match_score: 24, stage: "discovered" },
      ],
      [{ id: "app-1" }],
      [{ id: "in-1", classification: "interview" }],
    );
    expect(kpis.strong).toBe(1);
    expect(kpis.interviews).toBe(1);
    expect(kpis.headline).toContain("opportunities");
  });

  it("uses the same SHA-1 job id as the Python desk", () => {
    expect(sha1Hex("hello")).toBe("aaf4c61ddcc5e8a2dabede0f3b482cd9aea9434d");
    expect(stableJobId("remotive", "https://example.com/j", "Data Engineer")).toBe("job-b77b52053647");
    const unicodeTitle = "Ing\u00e9nieur donn\u00e9es";
    expect(stableJobId("remotive", "https://example.com/j", unicodeTitle)).toBe(
      `job-${sha1Hex(`remotive|https://example.com/j|${unicodeTitle}`).slice(0, 12)}`,
    );
    expect(normalizeJobUrl("https://www.Example.com/jobs/1/?utm_source=x")).toBe(
      "example.com/jobs/1?utm_source=x",
    );
  });

  it("keeps one application pack per offer", () => {
    const rows = dedupeApplications([
      { id: "ap-1", opportunity_id: "job-acme" },
      { id: "ap-2", opportunity_id: "job-acme" },
      { id: "ap-3", opportunity_id: "job-other" },
    ]);
    expect(rows).toHaveLength(2);
    expect(rows.map((row) => row.id)).toEqual(["ap-2", "ap-3"]);
  });
});
