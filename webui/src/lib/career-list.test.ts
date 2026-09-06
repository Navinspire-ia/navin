import { describe, expect, it } from "vitest";

import type { CareerOpportunity } from "@/lib/career-api";
import {
  CAREER_PAGE_SIZE,
  DEFAULT_ARCHIVE_AFTER_DAYS,
  DEFAULT_DELETE_AFTER_DAYS,
  activeOffers,
  archivedOffers,
  favoriteOffers,
  offerPageCount,
  offerPageOf,
  paginateOffers,
  retentionDays,
} from "@/lib/career-list";

function offer(partial: Partial<CareerOpportunity> & { id: string }): CareerOpportunity {
  return {
    source: "remotive",
    title: partial.title || partial.id,
    stage: "discovered",
    ...partial,
  };
}

describe("career offer lists", () => {
  it("keeps archived offers out of inbox and favorites", () => {
    const mixed = [
      offer({ id: "live", favorite: true }),
      offer({ id: "star", favorite: true, archived: true }),
      offer({ id: "old", archived: true }),
    ];
    expect(activeOffers(mixed).map((row) => row.id)).toEqual(["live"]);
    expect(favoriteOffers(mixed).map((row) => row.id)).toEqual(["live"]);
    expect(archivedOffers(mixed).map((row) => row.id)).toEqual(["star", "old"]);
  });

  it("paginates ten offers per page", () => {
    const many = Array.from({ length: 23 }, (_, index) => offer({ id: `job-${index}` }));
    expect(CAREER_PAGE_SIZE).toBe(10);
    expect(offerPageCount(many.length)).toBe(3);
    expect(paginateOffers(many, 1).map((row) => row.id)).toEqual(
      Array.from({ length: 10 }, (_, index) => `job-${index}`),
    );
    expect(paginateOffers(many, 3)).toHaveLength(3);
    expect(paginateOffers(many, 9)).toHaveLength(3);
    expect(offerPageOf(0)).toBe(1);
    expect(offerPageOf(10)).toBe(2);
    expect(offerPageOf(-1)).toBe(1);
  });

  it("defaults retention to 45 then 60 days", () => {
    expect(retentionDays({})).toEqual({
      archive_after_days: DEFAULT_ARCHIVE_AFTER_DAYS,
      delete_after_days: DEFAULT_DELETE_AFTER_DAYS,
    });
    expect(retentionDays({ archive_after_days: 90, delete_after_days: 30 })).toEqual({
      archive_after_days: 90,
      delete_after_days: 90,
    });
  });
});
