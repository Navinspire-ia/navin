import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { HomePane, NoticesPane } from "@/components/studio/tenders/TendersDesk";
import type { TenderDesk } from "@/lib/tenders-api";

const tx = (_key: string, fallback: string, values?: Record<string, string | number>) =>
  fallback.replace(/\{\{(\w+)\}\}/g, (_, name: string) => String(values?.[name] ?? ""));

function desk(partial: Partial<TenderDesk> = {}): TenderDesk {
  return {
    profile: { send_mode: "approval", countries: ["FR"], crafts: ["Cloud"], currency: "EUR", name: "Atelier Demo" },
    tenders: [],
    kpis: { weighted_value: 0, deadline_7d: 0, open: 0 },
    sources: [],
    catalog: [],
    queries: [],
    discoveries: [],
    journal: [],
    stages: [],
    send_modes: [],
    ...partial,
  };
}

describe("tenders money-first home", () => {
  it("treats a full no-go book as time saved, not as a failed desk", () => {
    const html = renderToStaticMarkup(
      createElement(HomePane, {
        desk: desk({
          tenders: [
            {
              id: "a",
              source_id: "ted",
              country: "FR",
              title: "Espaces verts",
              stage: "no-go",
              go: false,
            },
          ],
        }),
        kpis: [],
        tx,
        busy: "",
        token: "tok",
        onCollect: () => {},
        onOpen: () => {},
        onSetup: () => {},
        onFollow: () => {},
        onCrmSync: () => {},
        onDiscoverAccept: () => {},
        onOpenBook: () => {},
      }),
    );
    expect(html).toContain("1 notices read. Your time stays on deals you can win.");
    expect(html).toContain("text-balance");
    expect(html).not.toContain("whitespace-nowrap");
    expect(html).toContain("tenders-tile-grid");
    expect(html).toContain("Review no-gos");
    expect(html).toContain("Open the tender list");
    expect(html).toContain("Espaces verts");
    expect(html).toContain("Bidding as");
    expect(html).toContain("Atelier Demo");
    expect(html).not.toContain("Search a notice");
    expect(html).not.toContain("Result filters");
    expect(html).toContain("overflow-x-hidden");
    expect(html).toContain("break-words");
    expect(html).toContain("minmax(min(100%,16rem),1fr)");
  });

  it("keeps the money hero CTAs wrap-ready when three actions sit in a row", () => {
    const html = renderToStaticMarkup(
      createElement(HomePane, {
        desk: desk({
          profile: {
            send_mode: "approval",
            countries: ["FR", "CH", "BE", "LU", "AE", "QA"],
            crafts: ["AI", "Data", "Cloud", "Digital"],
            currency: "EUR",
            name: "Candidat Navinspire",
            specialty: "AI, Data, Cloud, Digital, Dve",
          },
          kpis: { weighted_value: 142_400_000, deadline_7d: 1, open: 12 },
          tenders: [
            {
              id: "go-1",
              source_id: "ted",
              country: "FR",
              title: "Cloud data platform",
              stage: "go",
              go: true,
              score: 82,
            },
          ],
        }),
        kpis: [],
        tx,
        busy: "",
        token: "tok",
        onCollect: () => {},
        onOpen: () => {},
        onSetup: () => {},
        onFollow: () => {},
        onCrmSync: () => {},
        onDiscoverAccept: () => {},
        onOpenBook: () => {},
      }),
    );
    expect(html).toContain("142.4 M EUR");
    expect(html).toContain("Open the tender list");
    expect(html).toContain("Open the next notice");
    expect(html).toContain("Find official notices");
    expect(html).toContain("Candidat Navinspire");
    expect(html).toContain("break-words");
    expect(html).toContain("minmax(min(100%,16rem),1fr)");
    expect(html).not.toContain("whitespace-nowrap");
  });

  it("puts the full book and result filters on the Tender page", () => {
    const html = renderToStaticMarkup(
      createElement(NoticesPane, {
        desk: desk({
          tenders: [
            {
              id: "a",
              source_id: "ted",
              country: "FR",
              title: "Espaces verts",
              stage: "no-go",
              go: false,
            },
          ],
        }),
        tx,
        busy: "",
        listView: "pipeline",
        picked: "all",
        onListView: () => {},
        onPicked: () => {},
        onOpen: () => {},
        onRowAction: () => {},
      }),
    );
    expect(html).toContain("Espaces verts");
    expect(html).toContain("Search a notice");
    expect(html).not.toContain("Your notices");
    expect(html).not.toContain("The chat still sees the full book.");
    expect(html).not.toContain("Result filters");
    expect(html).not.toContain("Country, domain, dates, score, budget, buyer and source.");
    expect(html).toContain("Show filters");
    expect(html).not.toContain("Home dashboard");
    expect(html).not.toContain("Rescore");
    expect(html).toContain("Export");
    expect(html).toContain("Download offers");
    expect(html).toContain('data-testid="tenders-notice-list"');
    expect(html).not.toContain('data-testid="tenders-notice-grid"');
    expect(html).not.toContain('data-testid="tenders-layout"');
    expect(html).not.toContain("Cards");
    expect(html).toContain('data-testid="tenders-notice-chips"');
    expect(html).toContain("flex-nowrap");
    expect(html.indexOf("All ·")).toBeLessThan(html.indexOf("Favorites ·"));
    expect(html.indexOf("Favorites ·")).toBeLessThan(html.indexOf("In play ·"));
    expect(html).not.toContain("Notices ·");
    expect(html).toContain("10 per page");
    expect(html).not.toContain("Min budget");
    expect(html).not.toContain("GO / NO-GO");
    expect(html).not.toContain("Language");
    expect(html).not.toContain('data-testid="tenders-notice-go"');
    expect(html).toContain("tenders-notice-table");
  });

  it("opens the book on All even when notices are still in play", () => {
    const html = renderToStaticMarkup(
      createElement(NoticesPane, {
        desk: desk({
          tenders: [
            {
              id: "live",
              source_id: "ted",
              country: "FR",
              title: "Live notice",
              stage: "new",
            },
          ],
        }),
        tx,
        busy: "",
        listView: "pipeline",
        picked: null,
        onListView: () => {},
        onPicked: () => {},
        onOpen: () => {},
        onRowAction: () => {},
      }),
    );
    const allBtn = html.match(/<button[^>]*>All · 1<\/button>/);
    expect(allBtn?.[0]).toContain('aria-selected="true"');
    expect(html).toContain("Live notice");
  });

  it("pages ten cards and still offers export of the whole filtered book", () => {
    const tenders = Array.from({ length: 11 }, (_, index) => ({
      id: `tn-${String(index).padStart(2, "0")}`,
      source_id: "ted",
      country: "FR",
      title: `Notice ${String(index).padStart(2, "0")}`,
      stage: "go" as const,
      go: true,
      score: 80,
      source_url: "https://ted.europa.eu/notice/x",
      response: index === 0 ? { letter: "Madame", pack_ready: true } : undefined,
    }));
    const html = renderToStaticMarkup(
      createElement(NoticesPane, {
        desk: desk({ tenders }),
        tx,
        busy: "",
        listView: "pipeline",
        picked: "all",
        token: "tok",
        onListView: () => {},
        onPicked: () => {},
        onOpen: () => {},
        onRowAction: () => {},
      }),
    );
    expect(html).toContain("Page 1 / 2 · 11 notice(s) · 10 per page");
    expect(html).toContain("Notice 00");
    expect(html).toContain("Notice 09");
    expect(html).not.toContain("Notice 10");
    expect(html).toContain("Next");
    expect(html).toContain('data-testid="tenders-notice-list"');
    expect(html).toContain("Export");
  });
});
