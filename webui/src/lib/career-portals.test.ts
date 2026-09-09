// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import { authorizedPortals } from "@/lib/career-api";
import {
  isClosedJobUrl,
  isLinkedInUrl,
  officialSearchPack,
  parsePastedOffer,
} from "@/lib/career-portals";

describe("career official portals", () => {
  it("builds LinkedIn search URLs per market without scrape hosts", () => {
    const pack = officialSearchPack("Data Engineer", ["FR", "AE", "SA"], "freelance", "remote");
    expect(pack.some((row) => row.id === "li-FR")).toBe(true);
    expect(pack.some((row) => row.id === "li-AE")).toBe(true);
    const france = pack.find((row) => row.id === "li-FR");
    expect(france?.url).toContain("linkedin.com/jobs/search");
    expect(france?.url).toContain("France");
    expect(france?.url).toContain("f_JT=C");
    expect(pack.some((row) => row.url.includes("francetravail.fr"))).toBe(true);
    expect(pack.some((row) => row.url.includes("bayt.com"))).toBe(true);
    expect(pack.some((row) => row.url.includes("dubaicareers.ae"))).toBe(true);
    const remotive = pack.find((row) => row.id === "remotive");
    expect(remotive?.url).toContain("remotive.com/remote-jobs?search=");
    expect(remotive?.url).not.toContain("/remote-jobs/search");
    expect(pack.some((row) => row.url.includes("jadarat.sa"))).toBe(true);
    expect(pack.every((row) => !row.url.includes("www.jadarat.sa"))).toBe(true);
  });

  it("treats LinkedIn and Bayt as closed", () => {
    expect(isLinkedInUrl("https://www.linkedin.com/jobs/view/123")).toBe(true);
    expect(isClosedJobUrl("https://www.linkedin.com/jobs/view/123")).toBe(true);
    expect(isClosedJobUrl("https://www.bayt.com/en/uae/jobs/q/data/")).toBe(true);
    expect(isClosedJobUrl("https://remotive.com/remote-jobs/123")).toBe(false);
  });

  it("imports a pasted LinkedIn offer without fetching", () => {
    const row = parsePastedOffer({
      url: "https://www.linkedin.com/jobs/view/123",
      title: "Senior Data Engineer",
      company: "Acme",
      body: "Python Spark remote Paris",
      track: "freelance",
      country: "FR",
    });
    expect(row?.source).toBe("linkedin");
    expect(row?.title).toBe("Senior Data Engineer");
    expect(row?.url).toContain("linkedin.com");
  });
});

describe("authorizedPortals wrapper", () => {
  it("exposes LinkedIn plus official boards", () => {
    const portals = authorizedPortals("Data Engineer freelance", ["FR", "US"]);
    expect(portals.some((row) => row.url.includes("linkedin.com/jobs/search"))).toBe(true);
    expect(portals.some((row) => row.url.includes("francetravail.fr"))).toBe(true);
    expect(portals.some((row) => row.id === "remotive")).toBe(true);
  });
});
