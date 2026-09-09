// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import type { CareerOpportunity } from "@/lib/career-api";
import { applyOfferFilter, emptyOfferFilter } from "@/lib/career-filters";
import { CAREER_PAGE_SIZE, paginateOffers } from "@/lib/career-list";

import {
  buildCareerExportFile,
  careerExportStem,
  tableFromOffers,
} from "./career-export";

const tx = (_key: string, fallback: string) => fallback;

function offer(partial: Partial<CareerOpportunity> & { id: string }): CareerOpportunity {
  return {
    source: "remotive",
    title: partial.title || partial.id,
    stage: "discovered",
    ...partial,
  };
}

function u32(bytes: Uint8Array, offset: number): number {
  return bytes[offset] | (bytes[offset + 1] << 8) | (bytes[offset + 2] << 16) | (bytes[offset + 3] << 24);
}

function u16(bytes: Uint8Array, offset: number): number {
  return bytes[offset] | (bytes[offset + 1] << 8);
}

function unzipStore(bytes: Uint8Array): Record<string, string> {
  const decoder = new TextDecoder();
  const files: Record<string, string> = {};
  let offset = 0;
  while (offset + 4 <= bytes.length) {
    const sig = u32(bytes, offset);
    if (sig === 0x02014b50 || sig === 0x06054b50) break;
    if (sig !== 0x04034b50) throw new Error(`bad zip signature ${sig.toString(16)} at ${offset}`);
    const method = u16(bytes, offset + 8);
    const comp = u32(bytes, offset + 18);
    const nameLen = u16(bytes, offset + 26);
    const extraLen = u16(bytes, offset + 28);
    const name = decoder.decode(bytes.subarray(offset + 30, offset + 30 + nameLen));
    const start = offset + 30 + nameLen + extraLen;
    if (method !== 0) throw new Error(`expected store, got ${method}`);
    files[name] = decoder.decode(bytes.subarray(start, start + comp));
    offset = start + comp;
  }
  return files;
}

const rows: CareerOpportunity[] = [
  offer({
    id: "a",
    title: 'Data; "Spark" & <AI>',
    company: "Navin & Co",
    country: "FR",
    location: "Paris",
    url: "https://jobs.example/a",
    posted_at: "2026-08-01",
    match_score: 88,
    compensation: 650,
    currency: "EUR",
    remote: "remote",
    stack: ["Python", "Spark"],
    languages: ["fr", "en"],
    favorite: true,
  }),
  offer({
    id: "b",
    title: "Android",
    country: "AE",
    match_score: 40,
  }),
];

describe("career export", () => {
  it("exports every filtered offer, not a page slice", () => {
    const many = Array.from({ length: 23 }, (_, index) =>
      offer({
        id: `job-${String(index).padStart(2, "0")}`,
        title: `Mission ${index}`,
        country: index % 2 === 0 ? "FR" : "AE",
        match_score: 70,
      }),
    );
    const facet = emptyOfferFilter();
    facet.countries = ["FR"];
    const filtered = applyOfferFilter(many, facet);
    expect(filtered).toHaveLength(12);
    expect(paginateOffers(filtered, 1)).toHaveLength(CAREER_PAGE_SIZE);
    const file = buildCareerExportFile(filtered, "csv", { locale: "fr-FR", tx });
    expect(file).not.toBeNull();
    const text = new TextDecoder().decode(file!.bytes);
    expect(text.match(/job-\d+/g)?.length).toBe(12);
    expect(text).not.toContain("job-01");
  });

  it("returns null when the filtered list is empty", () => {
    expect(buildCareerExportFile([], "csv", { tx })).toBeNull();
    expect(buildCareerExportFile([], "xlsx", { tx })).toBeNull();
  });

  it("uses a BOM and the locale separator, and quotes dangerous cells", () => {
    const fr = buildCareerExportFile([rows[0]], "csv", { locale: "fr-FR", tx });
    const en = buildCareerExportFile([rows[0]], "csv", { locale: "en-US", tx });
    expect(fr!.bytes[0]).toBe(0xef);
    expect(fr!.bytes[1]).toBe(0xbb);
    expect(fr!.bytes[2]).toBe(0xbf);
    const frText = new TextDecoder().decode(fr!.bytes);
    expect(frText).toContain(";");
    expect(frText).toContain('"Data; ""Spark"" & <AI>"');
    expect(new TextDecoder().decode(en!.bytes)).toContain(",");
    expect(fr!.filename).toMatch(/^career-offres-\d{8}\.csv$/);
  });

  it("builds an unzippable xlsx with escaped XML cells", () => {
    const file = buildCareerExportFile(
      [offer({ id: "x", title: 'CEO <C-level>\u0000 & "ok"', company: "Navin & Co" })],
      "xlsx",
      { locale: "fr-FR", tx },
    );
    expect(file!.bytes[0]).toBe(0x50);
    const sheet = unzipStore(file!.bytes)["xl/worksheets/sheet1.xml"];
    expect(sheet).toContain("CEO &lt;C-level&gt; &amp; &quot;ok&quot;");
    expect(sheet).not.toContain("\u0000");
    expect(unzipStore(file!.bytes)["xl/workbook.xml"]).toContain("Offres");
  });

  it("drops NaN pay and stems the calendar day", () => {
    const table = tableFromOffers(
      [offer({ id: "n", match_score: Number.NaN, compensation: Number.POSITIVE_INFINITY })],
      { tx },
    );
    expect(table.values[0][table.headers.indexOf("Score")]).toBe("");
    expect(table.values[0][table.headers.indexOf("Remuneration")]).toBe("");
    expect(careerExportStem(new Date("2026-08-31T10:00:00"))).toBe("career-offres-20260831");
  });

  it("keeps every export i18n key in French and English", () => {
    const keys = [
      "export",
      "exportCsv",
      "exportExcel",
      "exportEmpty",
      "exportHint",
      "exportColId",
      "exportColTitle",
      "exportColLocation",
      "exportColUrl",
      "exportColPosted",
      "exportColScore",
      "exportColPay",
      "exportYes",
      "pageStatus",
    ];
    for (const locale of ["fr", "en"] as const) {
      const json = JSON.parse(
        readFileSync(resolve(__dirname, `../i18n/locales/${locale}/common.json`), "utf8"),
      ) as { studio: { career: Record<string, unknown> } };
      const block = json.studio.career;
      for (const key of keys) {
        expect(block[key], `${locale} missing ${key}`).toEqual(expect.any(String));
        expect(String(block[key])).not.toMatch(/[\u2013\u2014]/);
      }
      expect(String(block.pageStatus)).toContain("{{size}}");
    }
  });
});
