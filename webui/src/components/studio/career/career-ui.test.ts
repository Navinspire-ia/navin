// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import {
  CAREER_API_SOURCE_IDS,
  CAREER_HASH,
  CAREER_LIVE_OFF,
  DEFAULT_SEARCH_COUNTRIES,
  MARKET_PRESETS,
  OFFER_ROW_MENU,
  TOOLS_HASH,
  careerDeskHash,
  careerHashJob,
  careerHashPane,
  careerLiveSourceOn,
  careerShowsDesk,
  careerShowsWizard,
  expandSearchCountries,
  openInIdeHash,
  openOfficialCareerUrl,
  toggleCareerLiveSource,
} from "@/components/studio/career/career-ui";
import catalogUrls from "@/lib/career-catalog-urls.json";
import { officialCareerHref } from "@/lib/career-api";

describe("career IDE hashes stay inside the WebView", () => {
  it("keeps Career and Tools on hash routes", () => {
    expect(CAREER_HASH).toBe("#/career");
    expect(TOOLS_HASH).toBe("#/tools");
  });

  it("never hands an IDE hash to window.open", () => {
    const source = `${openInIdeHash.toString()}\n${openOfficialCareerUrl.toString()}`;
    expect(source).not.toContain("window.open");
    expect(source).toContain("location.hash");
    expect(source).toContain("openInOsBrowser");
  });

  it("refuses to send the Career desk hash to the OS browser", () => {
    expect(officialCareerHref("#/career")).toBeNull();
    expect(officialCareerHref("#/tools")).toBeNull();
    expect(officialCareerHref("#/notes")).toBeNull();
    expect(officialCareerHref("http://tauri.localhost/#/career")).toBeNull();
    expect(officialCareerHref("http://tauri.localhost/#/tools")).toBeNull();
    expect(officialCareerHref("tauri://localhost/#/career")).toBeNull();
    expect(officialCareerHref("https://www.linkedin.com/jobs/view/4242")).toContain("linkedin.com");
  });

  it("exposes every backend market as a wizard chip, and no extras", () => {
    expect([...MARKET_PRESETS]).toEqual([
      "FR",
      "BE",
      "CH",
      "GB",
      "US",
      "CA",
      "DE",
      "NL",
      "AE",
      "SA",
      "QA",
      "KW",
      "OM",
      "BH",
      "MA",
      "TN",
      "ES",
      "IT",
      "PT",
      "IE",
      "SE",
      "PL",
    ]);
  });
});

describe("career catalog URLs leave the IDE", () => {
  it("lists only http(s) boards the OS browser can open", () => {
    expect(catalogUrls.length).toBe(135);
    for (const row of catalogUrls) {
      expect(officialCareerHref(row.url)).toBeTruthy();
      expect(row.url.startsWith("http://") || row.url.startsWith("https://")).toBe(true);
    }
  });

  it("keeps the Career desk source free of window.open", () => {
    const desk = readFileSync(
      join(dirname(fileURLToPath(import.meta.url)), "CareerWorkspace.tsx"),
      "utf8",
    );
    expect(desk).not.toContain("window.open");
    expect(desk).toContain("openOfficialCareerUrl");
    expect(desk).toContain("data-open-url");
    expect(desk).not.toContain('tx("askAgent"');
    expect(desk.indexOf('tx("findMission"')).toBeLessThan(desk.indexOf('tx("hideChat"'));
    expect(OFFER_ROW_MENU.calloutProps?.preventDismissOnScroll).toBe(true);
    expect(desk).toContain("openFavorites");
    expect(desk).toContain("openArchive");
    expect(desk).toContain("OFFER_ROW_MENU");
    // The desk lands on Offers: no dashboard pane, no dashboard button.
    expect(desk).not.toContain("homeDashboard");
    expect(desk).not.toContain("<CareerDashboard");
    expect(desk).not.toContain('pane === "home"');
    expect(desk).toContain('useState<Pane>("offers")');
    expect(desk).toContain("offerBook");
    expect(desk).not.toContain("offerBookBody");
    expect(desk).not.toContain("openHome");
    expect(desk).toContain("CareerWizard");
    expect(desk).not.toContain("CareerStart");
    expect(desk).toContain("careerShowsWizard");
    expect(desk).toContain("reopenSetup");
    expect(desk).toContain("career-open-setup");
    expect(desk).toContain("profileConfig");
    expect(desk).toContain('tx("settings", "Settings")');
    expect(desk).toContain('iconProps={{ iconName: "Settings" }}');
    expect(desk).toContain("career-refresh");
    expect(desk).toContain('data-testid="career-scroll"');
    expect(desk).toContain("overflow-y-auto");
    expect(desk).toContain("refreshDesk");
    expect(desk).toContain('postCareer(token || "", "status"');
    expect(desk).toContain("career-title");
    // One header line: title, track toggle, actions, then Settings on the right. No tagline.
    expect(desk).not.toContain("career-tagline");
    expect(desk).not.toContain('tx("oneLiner"');
    expect(desk).not.toContain('tx("watchNow"');
    expect(desk).toContain('tx("cycle"');
    expect(desk).not.toContain('tx("schedule", "Schedule")');
    expect(desk.indexOf('tx("title"')).toBeLessThan(desk.indexOf('tx("trackFreelance"'));
    expect(desk.indexOf('tx("trackFreelance"')).toBeLessThan(desk.indexOf('tx("offerBook"'));
    expect(desk.indexOf('tx("findMission"')).toBeLessThan(desk.indexOf("career-open-setup"));
    expect(desk).toContain('className="ml-auto flex shrink-0 items-center gap-2"');
    expect(desk).toContain("career-offer-panel");
    expect(desk).toContain("career-offer-facts");
    expect(desk).toContain("selectOffer");
    expect(desk).toContain('onDismiss={() => onSelect("")}');
    expect(desk).not.toContain("if (!selectedId && selected)");
    expect(desk).not.toContain(": rows[0] || null");
    expect(desk).not.toContain('data-testid="career-watch"');
    expect(desk).not.toContain('data-testid="career-run-cycle"');
    expect(desk).not.toContain('data-testid="career-schedule"');
    expect(desk).not.toContain("match_reasons");
    expect(desk).not.toContain('tx("kicker"');
    expect(desk).toContain('tx("title", "Navin Career")');
    expect(desk).not.toContain("Studio Navin");
    expect(desk).toContain("career-offers");
    expect(desk).toContain("career-offer-grid");
    expect(desk).toContain("career-offer-facts");
    expect(desk).toContain("career-applications");
    expect(desk).toContain("appsEmptyBody");
    expect(desk).toContain("appsEmptyPipeline");
    expect(desk).toContain("markApplied");
    expect(desk).toContain("listTrackedApplications");
    expect(desk).toContain("career-export");
    expect(desk).toContain("trailing=");
    expect(desk).not.toContain("Easy Apply");
    expect(desk).toContain("career-pane-");
    expect(desk).toContain("max-w-6xl items-stretch justify-end gap-0.5 py-1");
    expect(desk).toContain('role="tablist" aria-label={tx("listViews", "Offer lists")}');
    // Buckets, list tabs, filters and export share one toolbar line; cards print the board facts.
    expect(desk).not.toContain("briefPlaceholder");
    expect(desk).not.toContain('tx("rescore", "Rescore")');
    expect(desk).toContain("leading={listTabs}");
    expect(desk).toContain("career-offer-card");
    expect(desk).toContain('tx("viewOffer", "View this offer")');
    expect(desk).toContain("career-result-count");
    expect(desk).toContain("downloadCareerExport");
    expect(desk).toContain("{{size}}");
    expect(desk).toContain("onDeskPane");
    expect(desk).toContain("deskReady");
    expect(desk).not.toContain("Easy Apply");
    const dash = readFileSync(
      join(dirname(fileURLToPath(import.meta.url)), "CareerDashboard.tsx"),
      "utf8",
    );
    expect(dash).toContain("career-home-dashboard");
    expect(dash).not.toContain("Easy Apply");
    expect(dash).not.toContain("window.open");
    const wizard = readFileSync(
      join(dirname(fileURLToPath(import.meta.url)), "CareerWizard.tsx"),
      "utf8",
    );
    expect(wizard).toContain("data-testid=\"career-wizard\"");
    expect(wizard).toContain("career-wizard-steps");
    expect(wizard).toContain("career-wizard-api-");
    expect(wizard).toContain("CareerCountryMultiSelect");
    expect(wizard).toContain("career-countries-primary");
    expect(wizard).not.toContain("MARKET_PRESETS.map");
    expect(wizard).toContain("wizardKicker");
    expect(wizard).toContain("wizardBack");
    expect(wizard).toContain('tx("continue"');
    const start = readFileSync(
      join(dirname(fileURLToPath(import.meta.url)), "CareerStart.tsx"),
      "utf8",
    );
    expect(start).toContain("CareerCountryMultiSelect");
    expect(start).not.toContain("QUICK_COUNTRIES");
  });
});

describe("career desk hash", () => {
  it("keeps the dashboard on #/career and the book on pane=offers", () => {
    expect(careerDeskHash()).toBe("#/career");
    expect(careerDeskHash({ pane: "home" })).toBe("#/career");
    expect(careerDeskHash({ pane: "offers" })).toBe("#/career?pane=offers");
    expect(careerDeskHash({ pane: "offers", job: "job-1" })).toBe("#/career?job=job-1");
    expect(careerHashPane("#/career")).toBe("home");
    expect(careerHashPane("#/career?pane=offers")).toBe("offers");
    expect(careerHashPane("#/career?pane=discover")).toBe("offers");
    expect(careerHashPane("#/career?job=abc")).toBe("offers");
    expect(careerHashJob("#/career?job=abc")).toBe("abc");
  });

  it("opens Configuration first until the wizard is complete, like Tenders", () => {
    expect(
      careerShowsWizard({ deskReady: true, wizardComplete: false, view: "work" }),
    ).toBe(true);
    expect(
      careerShowsDesk({ deskReady: true, wizardComplete: false, view: "work" }),
    ).toBe(false);
    expect(
      careerShowsWizard({ deskReady: true, wizardComplete: false, view: "setup" }),
    ).toBe(true);
  });

  it("lands on the desk after setup, and can reopen configuration", () => {
    expect(
      careerShowsWizard({ deskReady: true, wizardComplete: true, view: "work" }),
    ).toBe(false);
    expect(
      careerShowsDesk({ deskReady: true, wizardComplete: true, view: "work" }),
    ).toBe(true);
    expect(
      careerShowsWizard({ deskReady: true, wizardComplete: true, view: "setup" }),
    ).toBe(true);
    expect(
      careerShowsDesk({ deskReady: true, wizardComplete: true, view: "setup" }),
    ).toBe(false);
  });

  it("opens the dashboard when offers already exist, even during setup", () => {
    expect(
      careerShowsWizard({
        deskReady: true,
        wizardComplete: false,
        view: "work",
        hasOffers: true,
      }),
    ).toBe(false);
    expect(
      careerShowsDesk({
        deskReady: true,
        wizardComplete: false,
        view: "work",
        hasOffers: true,
      }),
    ).toBe(true);
  });

  it("waits for the desk snapshot so a remount does not flash the wizard", () => {
    expect(
      careerShowsWizard({ deskReady: false, wizardComplete: false, view: "work" }),
    ).toBe(false);
    expect(
      careerShowsDesk({ deskReady: false, wizardComplete: true, view: "work" }),
    ).toBe(false);
  });

  it("expands a 5-country profile onto Gulf, Maghreb and core Europe", () => {
    const scope = expandSearchCountries(["FR", "BE", "CH", "DE", "NL"]);
    expect(scope.slice(0, 5)).toEqual(["FR", "BE", "CH", "DE", "NL"]);
    expect(scope).toEqual(expect.arrayContaining(["AE", "QA", "SA", "OM", "BH", "KW", "MA", "TN", "ES", "IT"]));
    expect(DEFAULT_SEARCH_COUNTRIES).toContain("AE");
  });
});

describe("career live source toggles", () => {
  const live = ["remotive", "greenhouse", "web-job-search"];

  it("keeps every live source on when the profile is empty or keyed-only", () => {
    expect(careerLiveSourceOn([], live, "remotive")).toBe(true);
    expect(careerLiveSourceOn(["linkedin"], live, "greenhouse")).toBe(true);
  });

  it("turns every live family off and keeps keyed boards", () => {
    let ids = ["linkedin", ...live];
    for (const row of live) ids = toggleCareerLiveSource(ids, live, row);
    expect(ids).toEqual(["linkedin", CAREER_LIVE_OFF]);
    expect(careerLiveSourceOn(ids, live, "remotive")).toBe(false);
  });

  it("turns one live family back on after live-off", () => {
    const ids = toggleCareerLiveSource(["linkedin", CAREER_LIVE_OFF], live, "remotive");
    expect(ids).toEqual(["linkedin", "remotive"]);
    expect(careerLiveSourceOn(ids, live, "remotive")).toBe(true);
    expect(careerLiveSourceOn(ids, live, "web-job-search")).toBe(false);
  });

  it("keeps official APIs out of the live family list", () => {
    expect(CAREER_API_SOURCE_IDS).toEqual(["adzuna", "jooble", "usajobs"]);
    expect(careerLiveSourceOn(["adzuna"], live, "remotive")).toBe(true);
  });
});
