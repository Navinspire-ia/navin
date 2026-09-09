// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import { isJobListingPage, normalizeWebHit, parseDdgHtml, unwrapDdg, webSearchQueries, webSearchRunPack } from "@/lib/career-web";

const DDG_FIXTURE = `
<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.welcometothejungle.com%2Ffr%2Fcompanies%2Facme%2Fjobs%2Fsenior-data-engineer">Senior Data Engineer - Acme</a>
<a class="result__snippet">Python Spark remote Paris freelance hiring</a>
<a class="result__a" href="https://www.youtube.com/watch?v=1">Funny cats</a>
<a class="result__snippet">not a job</a>
`;

describe("career web search", () => {
  it("searches each market in its own languages", () => {
    const queries = webSearchQueries("Senior Data Engineer", ["FR", "BE", "DE", "AE", "CA"], "freelance");
    const blob = queries.map((row) => row.query).join(" ");
    expect(blob).toContain("offre OR emploi OR mission");
    expect(blob).toContain("jobs OR hiring OR contract");
    expect(blob).toContain("Stelle OR Freiberuflich OR Job");
    expect(blob).toContain("vacature OR freelance");
    expect(blob).toContain("وظائف OR jobs");
    expect(blob).toContain("contractuel");
    expect(blob).toContain("Bruxelles");
  });

  it("builds a compact run pack with site queries", () => {
    const pack = webSearchRunPack("Data Engineer", ["FR", "AE"], "freelance", ["Python"]);
    expect(pack.some((row) => row.query.includes("site:francetravail.fr"))).toBe(true);
    expect(pack.some((row) => row.query.includes("site:bayt.com"))).toBe(true);
    expect(pack.every((row) => row.kind === "web")).toBe(true);
    expect(pack.length).toBeGreaterThan(0);
    expect(pack.length).toBeLessThanOrEqual(12);
  });

  it("parses DDG HTML and unwraps redirects", () => {
    const hits = parseDdgHtml(DDG_FIXTURE);
    expect(unwrapDdg("//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fjob")).toBe(
      "https://example.com/job",
    );
    expect(hits[0]?.url).toContain("welcometothejungle.com");
    expect(hits[0]?.title).toContain("Senior Data Engineer");
  });

  it("keeps job hits and drops noise", () => {
    const profile = { titles: ["Data Engineer"], countries_primary: ["FR"], stack: ["Python"] };
    const kept = normalizeWebHit(
      {
        title: "Senior Data Engineer - Acme",
        url: "https://www.welcometothejungle.com/fr/jobs/1",
        snippet: "Python Spark remote Paris",
      },
      "FR",
      "freelance",
      profile,
    );
    const noise = normalizeWebHit(
      {
        title: "Funny cats",
        url: "https://www.youtube.com/watch?v=1",
        snippet: "video",
      },
      "FR",
      "freelance",
      profile,
    );
    expect(kept?.source).toBe("web");
    expect(kept?.title).toContain("Data Engineer");
    expect(noise).toBeNull();
  });

  it("drops listing and fiche metier pages", () => {
    const profile = { titles: ["Data Engineer"], countries_primary: ["FR"] };
    expect(isJobListingPage("Fiche métier Data engineer | Apec", "https://www.apec.fr/tous-nos-metiers")).toBe(true);
    expect(isJobListingPage("Fiche metier Data engineer | MetierScope", "https://candidat.francetravail.fr/metierscope/fiche-metier/1")).toBe(true);
    expect(isJobListingPage("86 offres d'emploi Data Engineer Freelance - LinkedIn")).toBe(true);
    expect(isJobListingPage("Data Engineer Jobs for August 2026 | FreelancerMissions")).toBe(true);
    expect(isJobListingPage("Jobs in Tunisia - Page 2 - Bayt.com")).toBe(true);
    expect(isJobListingPage("Senior Data Engineer - Acme", "https://www.welcometothejungle.com/fr/jobs/1")).toBe(false);
    expect(isJobListingPage("Engineer jobs | Dice.com")).toBe(true);
    expect(isJobListingPage("engineer in various locations - Search - Job Bank")).toBe(true);
    expect(isJobListingPage("bServe - Find your next job")).toBe(true);
    expect(
      normalizeWebHit(
        {
          title: "86 offres d'emploi Data Engineer Freelance - France - LinkedIn",
          url: "https://www.linkedin.com/jobs/search",
          snippet: "offres",
        },
        "FR",
        "freelance",
        profile,
      ),
    ).toBeNull();
    expect(
      normalizeWebHit(
        {
          title: "Clutch Data Engineer | Welcome to the Jungle",
          url: "https://www.welcometothejungle.com/fr/companies/clutch/jobs/data-engineer",
          snippet: "Python Spark Paris",
        },
        "FR",
        "freelance",
        profile,
      )?.source,
    ).toBe("web");
  });

  it("stores LinkedIn search hits as snippets only", () => {
    const row = normalizeWebHit(
      {
        title: "Data Engineer - Capgemini | LinkedIn",
        url: "https://www.linkedin.com/jobs/view/123",
        snippet: "Python Spark Paris hiring",
      },
      "FR",
      "freelance",
      { titles: ["Data Engineer"], countries_primary: ["FR"] },
    );
    expect(row?.source).toBe("linkedin");
    expect(row?.description).toContain("Python");
  });
});
