// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import type { TenderNotice } from "@/lib/tenders-api";
import {
  TENDERS_PAGE_SIZE,
  archivedNotices,
  favoriteNotices,
  filterNotices,
  noticeGoMark,
  noticePageCount,
  paginateNotices,
  pipelineCounts,
  sortNotices,
} from "@/components/studio/tenders/pipeline";
import { applyFacetFilter, emptyFacetFilter } from "@/components/studio/tenders/notice-filters";
import { buildTendersExportFile, readyOfferNotices, tableFromNotices } from "@/lib/tenders-export";

function notice(partial: Partial<TenderNotice>): TenderNotice {
  return {
    id: partial.id || "tn-1",
    source_id: "ted",
    country: partial.country || "FR",
    title: partial.title || "Cloud platform",
    stage: partial.stage || "analysed",
    ...partial,
  };
}

describe("tenders pipeline", () => {
  const rows = [
    notice({
      id: "a",
      title: "Landscaping",
      go: false,
      stage: "no-go",
      score: 40,
    }),
    notice({
      id: "b",
      title: "Cloud hosting",
      go: true,
      stage: "go",
      score: 82,
      score_breakdown: { days_left: 12 },
    }),
    notice({
      id: "c",
      title: "Data platform",
      go: true,
      stage: "drafting",
      score: 75,
      score_breakdown: { days_left: 3 },
    }),
  ];

  it("counts in-play separately from no-go", () => {
    const counts = pipelineCounts(rows);
    expect(counts.play).toBe(2);
    expect(counts.go).toBe(2);
    expect(counts.urgent).toBe(1);
    expect(counts.draft).toBe(1);
    expect(counts.nogo).toBe(1);
    expect(counts.all).toBe(3);
  });

  it("finds a notice by id, reference or description, not only the title", () => {
    const extra = [
      notice({
        id: "tn-hidden",
        title: "Lot 3",
        reference: "AO-2026-77",
        description: "Plateforme analytics pour la DINUM",
        go: false,
        stage: "no-go",
      }),
    ];
    expect(filterNotices(extra, "all", "tn-hidden").map((row) => row.id)).toEqual(["tn-hidden"]);
    expect(filterNotices(extra, "all", "AO-2026").map((row) => row.id)).toEqual(["tn-hidden"]);
    expect(filterNotices(extra, "all", "analytics").map((row) => row.id)).toEqual(["tn-hidden"]);
    expect(
      filterNotices(
        [notice({ id: "tn-note", title: "Lot 9", go_reason: "ISO 27001 on file", stage: "go", go: true })],
        "all",
        "27001",
      ).map((row) => row.id),
    ).toEqual(["tn-note"]);
    expect(
      filterNotices(
        [
          notice({
            id: "tn-analysis",
            title: "Lot 4",
            score: 88,
            analysis: { gaps: "need a named DPO" },
            response: { letter: "Madame la commission" },
          }),
        ],
        "all",
        "DPO",
      ).map((row) => row.id),
    ).toEqual(["tn-analysis"]);
    expect(
      filterNotices(
        [
          notice({
            id: "tn-analysis",
            title: "Lot 4",
            score: 88,
            analysis: { gaps: "need a named DPO" },
            response: { letter: "Madame la commission" },
          }),
        ],
        "all",
        "88",
      ).map((row) => row.id),
    ).toEqual(["tn-analysis"]);
  });

  it("defaults the desk to work, not a no-go dump", () => {
    const play = filterNotices(rows, "play");
    expect(play.map((row) => row.id)).toEqual(["b", "c"]);
    expect(filterNotices(rows, "nogo")).toHaveLength(1);
    expect(filterNotices(rows, "play", "data").map((row) => row.id)).toEqual(["c"]);
  });

  it("sorts GO and score before weak notices", () => {
    const sorted = sortNotices(rows);
    expect(sorted.map((row) => row.id)).toEqual(["b", "c", "a"]);
  });

  it("keeps archived notices out of play, go and favorites", () => {
    const mixed = [
      notice({ id: "live", go: true, stage: "go", favorite: true }),
      notice({ id: "old", go: true, stage: "go", favorite: true, archived: true }),
    ];
    const counts = pipelineCounts(mixed);
    expect(counts.play).toBe(1);
    expect(counts.go).toBe(1);
    expect(counts.all).toBe(2);
    expect(favoriteNotices(mixed).map((row) => row.id)).toEqual(["live"]);
    expect(archivedNotices(mixed).map((row) => row.id)).toEqual(["old"]);
    expect(filterNotices(mixed, "play").map((row) => row.id)).toEqual(["live"]);
  });

  it("pages results by ten", () => {
    const many = Array.from({ length: 23 }, (_, index) => notice({ id: `tn-${index}` }));
    expect(TENDERS_PAGE_SIZE).toBe(10);
    expect(noticePageCount(23)).toBe(3);
    expect(paginateNotices(many, 1).map((row) => row.id)).toEqual(
      many.slice(0, 10).map((row) => row.id),
    );
    expect(paginateNotices(many, 3)).toHaveLength(3);
    expect(paginateNotices(many, 9)).toHaveLength(3);
  });

  it("exports every filtered notice, not the current page", () => {
    const tx = (_key: string, fallback: string) => fallback;
    const many = Array.from({ length: 23 }, (_, index) =>
      notice({
        id: `tn-${String(index).padStart(2, "0")}`,
        title: `Lot ${String(index).padStart(2, "0")}`,
        country: index % 2 === 0 ? "FR" : "BE",
        go: true,
        stage: "go",
        score: 70,
        response: index < 4 ? { letter: "Madame", pack_ready: true } : undefined,
      }),
    );
    const facet = emptyFacetFilter();
    facet.countries = ["FR"];
    const visible = sortNotices(filterNotices(applyFacetFilter(many, facet), "go", ""));
    expect(visible).toHaveLength(12);
    expect(paginateNotices(visible, 1)).toHaveLength(10);
    expect(noticePageCount(visible.length)).toBe(2);
    const file = buildTendersExportFile(visible, "csv", { locale: "fr-FR", tx });
    expect(file).not.toBeNull();
    const text = new TextDecoder().decode(file!.bytes);
    expect(text.match(/tn-\d+/g)?.length).toBe(12);
    expect(text).not.toContain("tn-01");
    expect(readyOfferNotices(visible)).toHaveLength(2);
    expect(tableFromNotices(visible, { tx }).values).toHaveLength(12);
  });

  it("keeps a notice you decided to draft out of the no-go bucket", () => {
    const forced = [notice({ id: "d", go: false, stage: "drafting", score: 51 })];
    const counts = pipelineCounts(forced);
    expect(counts.nogo).toBe(0);
    expect(counts.play).toBe(1);
    expect(counts.draft).toBe(1);
  });

  it("marks GO and No-go from the stored decision", () => {
    expect(noticeGoMark(notice({ go: true, stage: "go" }))).toBe("go");
    expect(noticeGoMark(notice({ go: false, stage: "no-go" }))).toBe("nogo");
    expect(noticeGoMark(notice({ stage: "matched" }))).toBeNull();
    expect(noticeGoMark(notice({ go: true, stage: "drafting" }))).toBe("go");
    expect(noticeGoMark(notice({ go: false, stage: "go" }))).toBe("go");
    expect(noticeGoMark(notice({ go: true, stage: "no-go" }))).toBe("nogo");
  });

  it("does not count a manual GO in the no-go chip", () => {
    const forced = [notice({ id: "g", go: false, stage: "go", score: 40 })];
    const counts = pipelineCounts(forced);
    expect(counts.go).toBe(1);
    expect(counts.nogo).toBe(0);
    expect(counts.play).toBe(1);
  });
});
