// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { openOfficialLeadUrl } from "@/components/studio/leads/leads-ui";
import catalogUrls from "@/lib/leads-catalog-urls.json";
import { officialLeadHref } from "@/lib/leads-api";

describe("leads IDE hashes stay inside the WebView", () => {
  it("never hands an IDE hash to window.open", () => {
    const source = openOfficialLeadUrl.toString();
    expect(source).not.toContain("window.open");
    expect(source).toContain("openInOsBrowser");
  });

  it("refuses to send the Leads desk hash to the OS browser", () => {
    expect(officialLeadHref("#/leads")).toBeNull();
    expect(officialLeadHref("#/tools")).toBeNull();
    expect(officialLeadHref("http://tauri.localhost/#/leads")).toBeNull();
    expect(officialLeadHref("http://tauri.localhost/#/tools")).toBeNull();
    expect(officialLeadHref("tauri://localhost/#/leads")).toBeNull();
    expect(officialLeadHref("https://www.linkedin.com/in/someone")).toContain("linkedin.com");
    expect(officialLeadHref("datadoghq.com")).toContain("datadoghq.com");
  });
});

describe("leads catalog URLs leave the IDE", () => {
  it("lists only http(s) docs the OS browser can open", () => {
    expect(catalogUrls.length).toBe(11);
    for (const row of catalogUrls) {
      expect(officialLeadHref(row.url)).toBeTruthy();
      expect(row.url.startsWith("http://") || row.url.startsWith("https://")).toBe(true);
    }
  });

  it("keeps the Leads desk source free of window.open", () => {
    const desk = readFileSync(
      join(dirname(fileURLToPath(import.meta.url)), "LeadsWorkspace.tsx"),
      "utf8",
    );
    expect(desk).not.toContain("window.open");
    expect(desk).toContain("openOfficialLeadUrl");
    expect(desk).toContain("leads-start-loop");
    expect(desk).toContain("leads-run-cycle");
    expect(desk).toContain("leads-refresh");
    expect(desk).toContain("leads-chat");
    expect(desk).not.toContain("leads-schedule");
    expect(desk).toContain("IconButton");
    expect(desk).toContain('iconName: "Refresh"');
    expect(desk).toContain('tx("chat", "Chat")');
    expect(desk).not.toContain('text={tx("refresh"');
    expect(desk).toContain("overflow-x-auto");
    // One-line header like the Tenders desk: no section tabs, no Home dashboard.
    expect(desk).not.toContain("DeskTab");
    expect(desk).not.toContain("LeadsDashboard");
    expect(desk).not.toContain('activePane === "home"');
    expect(desk).toContain("leads-title");
    expect(desk).toContain("leads-open-book");
    expect(desk).toContain("leads-run-sequences");
    expect(desk).toContain("leads-hunt");
    expect(desk).toContain("leads-open-setup");
    expect(desk.indexOf("leads-title")).toBeLessThan(desk.indexOf("leads-open-book"));
    expect(desk.indexOf("leads-hunt")).toBeLessThan(desk.indexOf("leads-open-setup"));
    // Cards, buckets and quick filters instead of the table.
    expect(desk).toContain("leads-filter-");
    expect(desk).not.toContain("<table");
    expect(desk).not.toContain("leads-book-table");
    expect(desk).toContain("leads-book-list");
    expect(desk).toContain('data-testid="leads-card"');
    expect(desk).toContain("leads-result-count");
    expect(desk).toContain("<LeadFilters");
    expect(desk).toContain("<LeadsActivity");
    expect(desk).toContain("countryDisplayName");
    expect(desk).toContain("leads-row-crm");
    expect(desk).toContain("leads-row-delete");
    expect(desk).toContain("leads-row-menu");
    expect(desk).toContain("leads-page-status");
    expect(desk).toContain("usePagedRows");
    expect(desk).toContain("LEADS_PAGE_SIZE");
    expect(desk).toContain("leads-outreach-hints");
    expect(desk).toContain("TradingLoopSchedulePanel");
    expect(desk).toContain("setInterval");
    const activity = readFileSync(
      join(dirname(fileURLToPath(import.meta.url)), "LeadsActivity.tsx"),
      "utf8",
    );
    expect(activity).toContain("leads-activity");
    expect(activity).toContain("<details");
    expect(activity).toContain("leads-loop-card");
    expect(activity).toContain("leads-rescore");
    expect(activity).toContain("formatNextDue");
    expect(activity).toContain("LeadsScene");
    expect(activity).not.toContain("window.open");
    const filters = readFileSync(
      join(dirname(fileURLToPath(import.meta.url)), "LeadFilters.tsx"),
      "utf8",
    );
    expect(filters).toContain("lead-filters-toolbar");
    expect(filters).toContain("lead-quick-filters");
    expect(filters).toContain("lead-chip-country");
    expect(filters).toContain("lead-chip-sector");
    expect(filters).toContain("lead-chip-source");
    expect(filters).toContain("lead-chip-stage");
    expect(filters).toContain("lead-chip-score");
    expect(filters).toContain("lead-chip-email");
    expect(filters).toContain("countryDropdownOptions");
    const start = readFileSync(
      join(dirname(fileURLToPath(import.meta.url)), "LeadsStart.tsx"),
      "utf8",
    );
    expect(start).toContain("SearchableSelect");
    expect(start).toContain("leads-start-countries");
    expect(start).toContain("countryOptions");
    expect(start).not.toContain('"FR", "BE", "CH"');
  });
});
