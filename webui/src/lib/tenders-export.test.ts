// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import type { TenderNotice } from "@/lib/tenders-api";

import {
  buildTendersExportFile,
  noticeHasOfferPack,
  readyOfferNotices,
  tableFromNotices,
  tendersExportStem,
} from "./tenders-export";

const tx = (key: string, fallback: string) => {
  if (key === "stage.go") return "GO";
  if (key === "stage.no-go") return "No-go";
  if (key === "goYes") return "GO";
  if (key === "goNo") return "NO-GO";
  return fallback;
};

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

const rows: TenderNotice[] = [
  notice({
    id: "a",
    title: 'Lot 3; "Cloud" & <IA>',
    buyer: "DINUM",
    reference: "AO-2026-77",
    source_url: "https://ted.europa.eu/notice/a",
    publication_date: "2026-08-01",
    deadline: "2026-09-15",
    stage: "go",
    score: 82,
    go: true,
    go_pct: 81,
    go_reason: "ISO 27001 on file",
    budget: 120000,
    currency: "EUR",
    sector: "cloud",
    cpv: "72000000",
    favorite: true,
    response: { letter: "Madame la commission", pack_ready: true },
  }),
  notice({
    id: "b",
    title: "Espaces verts",
    country: "BE",
    stage: "no-go",
    go: false,
    score: 31,
  }),
];

describe("tenders export", () => {
  it("exports every filtered notice, not a page slice", () => {
    const table = tableFromNotices(rows, { locale: "fr-FR", tx });
    expect(table.values).toHaveLength(2);
    expect(table.headers).toHaveLength(20);
    expect(table.values[0]).toContain("Lot 3; \"Cloud\" & <IA>");
    expect(table.values[0]).toContain("https://ted.europa.eu/notice/a");
    expect(table.values[0]).toContain("GO");
    expect(table.values[0]).toContain("oui");
    expect(table.values[1]).toContain("Espaces verts");
    expect(table.values[1]).toContain("NO-GO");
  });

  it("returns null when the filtered list is empty", () => {
    expect(buildTendersExportFile([], "csv", { tx })).toBeNull();
    expect(buildTendersExportFile([], "xlsx", { tx })).toBeNull();
  });

  it("uses a BOM and the locale separator, and quotes dangerous cells", () => {
    const fr = buildTendersExportFile([rows[0]], "csv", { locale: "fr-FR", tx });
    const en = buildTendersExportFile([rows[0]], "csv", { locale: "en-US", tx });
    expect(fr).not.toBeNull();
    expect(en).not.toBeNull();
    expect(fr!.bytes[0]).toBe(0xef);
    expect(fr!.bytes[1]).toBe(0xbb);
    expect(fr!.bytes[2]).toBe(0xbf);
    const frText = new TextDecoder().decode(fr!.bytes);
    const enText = new TextDecoder().decode(en!.bytes);
    expect(frText).toContain(";");
    expect(frText).toContain('"Lot 3; ""Cloud"" & <IA>"');
    expect(enText).toContain(",");
    expect(fr!.filename).toMatch(/^tenders-avis-\d{8}\.csv$/);
  });

  it("builds an unzippable xlsx with escaped XML cells", () => {
    const file = buildTendersExportFile(
      [notice({ title: 'CEO <C-level>\u0000 & "ok"', buyer: "Navin & Co", go: true, stage: "go" })],
      "xlsx",
      { locale: "fr-FR", tx },
    );
    expect(file).not.toBeNull();
    expect(file!.bytes[0]).toBe(0x50);
    expect(file!.bytes[1]).toBe(0x4b);
    const zip = unzipStore(file!.bytes);
    const sheet = zip["xl/worksheets/sheet1.xml"];
    expect(sheet).toContain("CEO &lt;C-level&gt; &amp; &quot;ok&quot;");
    expect(sheet).not.toContain("\u0000");
    expect(sheet).toContain('t="inlineStr"');
    expect(zip["xl/workbook.xml"]).toContain("Avis");
  });

  it("stems the file with the calendar day", () => {
    expect(tendersExportStem(new Date("2026-08-31T10:00:00"))).toBe("tenders-avis-20260831");
  });

  it("detects written offers without inventing a pack", () => {
    expect(noticeHasOfferPack(rows[0])).toBe(true);
    expect(noticeHasOfferPack(rows[1])).toBe(false);
    expect(noticeHasOfferPack(notice({ response: { letter: "  " } }))).toBe(false);
    expect(noticeHasOfferPack(notice({ response: { exports: { docx: { file_id: "f1" } } } }))).toBe(true);
    expect(readyOfferNotices(rows).map((row) => row.id)).toEqual(["a"]);
  });

  it("leaves empty GO blank instead of a broken i18n key", () => {
    const table = tableFromNotices([notice({ title: "Lot 9" })], { tx });
    const goIndex = table.headers.indexOf("GO / NO-GO");
    expect(table.values[0][goIndex]).toBe("");
  });

  it("drops NaN and Infinity instead of writing them as text", () => {
    const table = tableFromNotices(
      [notice({ score: Number.NaN, budget: Number.POSITIVE_INFINITY, go_pct: Number.NEGATIVE_INFINITY })],
      { tx },
    );
    const scoreIndex = table.headers.indexOf("Score");
    const budgetIndex = table.headers.indexOf("Budget");
    const pctIndex = table.headers.indexOf("GO %");
    expect(table.values[0][scoreIndex]).toBe("");
    expect(table.values[0][budgetIndex]).toBe("");
    expect(table.values[0][pctIndex]).toBe("");
  });

  it("keeps every export i18n key in French and English", () => {
    const keys = [
      "export",
      "exportCsv",
      "exportExcel",
      "exportEmpty",
      "exportHint",
      "downloadOffers",
      "downloadOffersHint",
      "downloadOffersEmpty",
      "downloadOffersBusy",
      "downloadOffersPartial",
      "downloadOfferFailed",
      "rowOfficial",
      "rowDownloadWord",
      "pageStatus",
    ];
    for (const locale of ["fr", "en"] as const) {
      const json = JSON.parse(
        readFileSync(resolve(__dirname, `../i18n/locales/${locale}/common.json`), "utf8"),
      ) as { studio: { tenders: Record<string, unknown> } };
      const block = json.studio.tenders;
      for (const key of keys) {
        expect(block[key], `${locale} missing ${key}`).toEqual(expect.any(String));
        expect(String(block[key])).not.toMatch(/[\u2013\u2014]/);
      }
      expect(String(block.pageStatus)).toContain("{{size}}");
    }
  });
});
