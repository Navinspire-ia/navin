import { describe, expect, it } from "vitest";
import { DEFAULT_PLATFORM_CATALOG, candidateSheet, missionSourcesForCountries, platformRelevant, sourcingStatusText } from "./career-prospecting";

describe("company sourcing markets", () => {
  it("distinguishes unavailable searches from empty results in French and English", () => {
    const source = { source: "profiles:public_web:FR:role:linkedin", status: "error", count: 0, error_code: "rate_limited" };
    expect(sourcingStatusText(source, "fr")).toContain("temporairement bloqué");
    expect(sourcingStatusText(source, "en")).toContain("temporarily blocked");
    expect(sourcingStatusText({ ...source, error_code: undefined, message: "Web search was limited by the provider." }, "fr")).toContain("temporairement bloqué");
    expect(sourcingStatusText({ ...source, status: "not_configured" }, "fr")).toContain("clé API manquante");
    expect(sourcingStatusText({ ...source, status: "not_configured" }, "en")).toContain("missing API key");
    expect(sourcingStatusText({ ...source, status: "ok" }, "fr")).toBe("0 résultat(s)");
  });
  it("keeps the complete directory available before the gateway sends its catalogs", () => {
    const missions = missionSourcesForCountries(["AE", "MA"], []);
    expect(missions.map(source => source.id)).toEqual(expect.arrayContaining(["bayt", "gulftalent", "naukrigulf", "rekrute"]));
    expect(DEFAULT_PLATFORM_CATALOG).toHaveLength(26);
    expect(DEFAULT_PLATFORM_CATALOG.filter(source => platformRelevant(source.markets, ["AE", "MA"])).map(source => source.id))
      .toEqual(expect.arrayContaining(["linkedin", "freelancermap", "peopleperhour", "freelancer", "upwork"]));
  });
  it("keeps international providers for every Gulf market and Morocco", () => {
    for (const country of ["SA", "OM", "AE", "BH", "QA", "KW", "MA"]) {
      const ids = missionSourcesForCountries([country]).map(row => row.id);
      expect(ids).toContain("linkedin");
      expect(ids).toContain("google_jobs");
      expect(ids).not.toContain("freework");
    }
  });
  it("offers regional platforms only in their target markets", () => {
    expect(platformRelevant("FR, GB", ["FR"])).toBe(true);
    expect(platformRelevant("Europe", ["CH"])).toBe(true);
    expect(platformRelevant("GB", ["AE", "MA"])).toBe(false);
    expect(platformRelevant("US, International", ["OM"])).toBe(true);
    expect(missionSourcesForCountries(["GB"]).some(s => s.id === "freework")).toBe(true);
  });
  it("exports observed facts as a profile sheet with unconfirmed availability", () => {
    const sheet = candidateSheet({ id: "person", name: "Sam", headline: "Nurse", snippet: "Available",
      source: "LinkedIn", url: "https://linkedin.com/in/sam", skills: ["Care"], country: "MA", city: "",
      signal: "declared", observed_at: 1700000000 });
    expect(sheet).toContain("Fiche profil");
    expect(sheet).toContain("à confirmer directement");
    expect(sheet).toContain("https://linkedin.com/in/sam");
    expect(sheet).not.toContain("CV complet");
  });
});
