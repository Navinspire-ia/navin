// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import type { CrmRecord } from "@/lib/api";
import { EMPTY_CRM_FACETS } from "@/store/crm-ui";

import { applyCrmFilters, CRM_PAGE_SIZE, paginateRows } from "./crm-list";
import {
  buildCsv,
  buildExportFile,
  buildXlsxBytes,
  csvEscape,
  exportFileStem,
  sanitizeXmlText,
  tableFromRows,
} from "./crm-export";

const tx = (key: string, fallback: string) => (key.includes("statuses.actif") ? "Actif" : fallback);

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

const companies: CrmRecord[] = [{ id: "cmp-1", name: "Navin & Co" }];
const contacts: CrmRecord[] = [
  {
    id: "1",
    firstName: "Ada",
    lastName: "Lovelace",
    email: "ada@x.com",
    status: "actif",
    companyId: "cmp-1",
    country: "FR",
    source: "linkedin",
    owner: "ada@x.com",
    tags: ["vip", "ia"],
    phone: "+331",
  },
  {
    id: "2",
    firstName: "Alan",
    lastName: "Turing",
    email: "alan@x.com",
    status: "inactif",
    country: "DE",
    source: "website",
    owner: "alan@x.com",
  },
];
const leads: CrmRecord[] = [
  { id: "l1", name: "Lead A", status: "nouveau", company: "Acme; SAS", email: "a@acme.com", source: "linkedin" },
  { id: "l2", name: "Lead B", status: "converti", company: "Beta", email: "b@beta.com", source: "website" },
];
const opps: CrmRecord[] = [
  {
    id: "o1",
    name: "Deal A <win>",
    stage: "gagne",
    amount: 1200,
    companyId: "cmp-1",
    contactIds: ["1"],
    owner: "ada@x.com",
  },
  { id: "o2", name: "Deal B", stage: "nouveau", amount: 100, owner: "alan@x.com" },
];

describe("crm export", () => {
  it("quotes csv cells that contain the separator, quotes or newlines", () => {
    expect(csvEscape("Navin, SAS", ",")).toBe('"Navin, SAS"');
    expect(csvEscape("Acme; SAS", ";")).toBe('"Acme; SAS"');
    expect(csvEscape('dit "ok"', ",")).toBe('"dit ""ok"""');
    expect(csvEscape("ligne\ndessous", ",")).toBe('"ligne\ndessous"');
  });

  it("uses a BOM and the locale separator", () => {
    const fr = buildExportFile("leads", [leads[0]], "csv", { locale: "fr-FR", tx });
    const en = buildExportFile("leads", [leads[0]], "csv", { locale: "en-US", tx });
    expect(fr).not.toBeNull();
    expect(en).not.toBeNull();
    const frText = new TextDecoder().decode(fr!.bytes);
    const enText = new TextDecoder().decode(en!.bytes);
    expect(fr!.bytes[0]).toBe(0xef);
    expect(fr!.bytes[1]).toBe(0xbb);
    expect(fr!.bytes[2]).toBe(0xbf);
    expect(frText).toContain(";");
    expect(frText).toContain('"Acme; SAS"');
    expect(enText).toContain(",");
    expect(en!.filename).toMatch(/^crm-leads-\d{8}\.csv$/);
  });

  it("returns null when there is nothing to export", () => {
    expect(buildExportFile("contacts", [], "csv", { tx })).toBeNull();
    expect(buildExportFile("leads", [], "xlsx", { tx })).toBeNull();
  });

  it("exports only contacts matching search, status, country, source, owner and tags", () => {
    expect(applyCrmFilters(contacts, "ada", EMPTY_CRM_FACETS)).toEqual([contacts[0]]);
    expect(applyCrmFilters(contacts, "", { ...EMPTY_CRM_FACETS, status: "actif" })).toEqual([contacts[0]]);
    expect(applyCrmFilters(contacts, "", { ...EMPTY_CRM_FACETS, country: "DE" })).toEqual([contacts[1]]);
    expect(applyCrmFilters(contacts, "", { ...EMPTY_CRM_FACETS, source: "linkedin" })).toEqual([contacts[0]]);
    expect(applyCrmFilters(contacts, "", { ...EMPTY_CRM_FACETS, owner: "ada@x.com" })).toEqual([contacts[0]]);
    expect(applyCrmFilters(contacts, "", { ...EMPTY_CRM_FACETS, tag: "vip" })).toEqual([contacts[0]]);
    const table = tableFromRows(
      "contacts",
      applyCrmFilters(contacts, "navin", EMPTY_CRM_FACETS, {
        extraHaystack: (row) => String(companies.find((item) => item.id === row.companyId)?.name || ""),
      }),
      {
        companies,
        locale: "fr-FR",
        tx,
      },
    );
    expect(table.values).toHaveLength(1);
    expect(table.headers).toHaveLength(14);
    expect(table.values[0]).toContain("Ada");
    expect(table.values[0]).toContain("Navin & Co");
    expect(table.values[0]).toContain("Actif");
    expect(table.values[0]).toContain("vip, ia");
    expect(table.values.flat().join(" ")).not.toContain("Alan");
  });

  it("exports every filtered contact, not a page slice", () => {
    const many = Array.from({ length: 45 }, (_, index) => ({
      id: `c${index}`,
      firstName: index % 2 === 0 ? "Ada" : "Alan",
      lastName: `P${index}`,
      country: index % 2 === 0 ? "FR" : "DE",
      status: "actif",
    }));
    const filtered = applyCrmFilters(many, "", { ...EMPTY_CRM_FACETS, country: "FR" });
    expect(filtered).toHaveLength(23);
    expect(paginateRows(filtered, 1, CRM_PAGE_SIZE)).toHaveLength(CRM_PAGE_SIZE);
    const file = buildExportFile("contacts", filtered, "csv", { locale: "fr-FR", tx });
    expect(file).not.toBeNull();
    const text = new TextDecoder().decode(file!.bytes);
    expect(text.match(/Ada/g)?.length).toBe(23);
    expect(text).not.toContain("Alan");
  });

  it("exports filtered leads and opportunities with linked names", () => {
    const leadTable = tableFromRows(
      "leads",
      applyCrmFilters(leads, "", { ...EMPTY_CRM_FACETS, status: "nouveau" }),
      { locale: "fr-FR", tx },
    );
    expect(leadTable.headers).toHaveLength(11);
    expect(leadTable.values).toHaveLength(1);
    expect(leadTable.values[0][0]).toBe("Lead A");

    const oppTable = tableFromRows(
      "opportunities",
      applyCrmFilters(opps, "", { ...EMPTY_CRM_FACETS, status: "gagne" }, { statusKeys: ["stage"] }),
      { companies, contacts, locale: "fr-FR", tx },
    );
    expect(oppTable.headers).toHaveLength(13);
    expect(oppTable.values).toHaveLength(1);
    expect(oppTable.values[0].join(" ")).toContain("Deal A <win>");
    expect(oppTable.values[0].join(" ")).toContain("Navin & Co");
    expect(oppTable.values[0].join(" ")).toContain("Ada Lovelace");
  });

  it("keeps empty status blank instead of a broken i18n key", () => {
    const table = tableFromRows("contacts", [{ id: "x", firstName: "No", lastName: "Status" }], { tx });
    const statusIndex = table.headers.indexOf("Statut");
    expect(table.values[0][statusIndex]).toBe("");
  });

  it("strips illegal XML control characters", () => {
    expect(sanitizeXmlText("ok\u0000bad\u0007")).toBe("okbad");
  });

  it("builds an unzippable xlsx that Excel can read, with escaped cells", () => {
    const dirty: CrmRecord = {
      id: "3",
      firstName: "Zoé",
      lastName: 'O"Hara',
      email: "z@x.com",
      companyId: "cmp-1",
      title: "CEO <C-level>\u0000",
    };
    const file = buildExportFile("contacts", [dirty], "xlsx", { companies, locale: "fr-FR", tx });
    expect(file).not.toBeNull();
    expect(file!.filename.endsWith(".xlsx")).toBe(true);
    expect(file!.bytes[0]).toBe(0x50);
    expect(file!.bytes[1]).toBe(0x4b);
    const zip = unzipStore(file!.bytes);
    expect(zip["[Content_Types].xml"]).toContain("worksheet");
    expect(zip["xl/workbook.xml"]).toContain("Contacts");
    const sheet = zip["xl/worksheets/sheet1.xml"];
    expect(sheet).toContain("Zoé");
    expect(sheet).toContain("Navin &amp; Co");
    expect(sheet).toContain("CEO &lt;C-level&gt;");
    expect(sheet).not.toContain("\u0000");
    expect(sheet).toContain('t="inlineStr"');
  });

  it("builds csv with a BOM and xlsx zip bytes", () => {
    const csv = buildCsv(["Nom"], [["Ada"]], ";");
    expect(csv.startsWith("\uFEFF")).toBe(true);
    expect(csv).toContain("Nom");
    const xlsx = buildXlsxBytes("Contacts", ["Nom"], [["Ada"]]);
    expect(Object.keys(unzipStore(xlsx))).toContain("xl/worksheets/sheet1.xml");
    expect(exportFileStem("contacts", new Date("2026-08-31T10:00:00"))).toBe("crm-contacts-20260831");
  });

  it("keeps CRM pager and export i18n keys in French and English", () => {
    const keys = ["pageRange", "export", "exportCsv", "exportExcel", "exportEmpty", "exportHint"];
    for (const locale of ["fr", "en"] as const) {
      const json = JSON.parse(
        readFileSync(resolve(__dirname, `../i18n/locales/${locale}/common.json`), "utf8"),
      ) as { crm: Record<string, unknown> };
      for (const key of keys) {
        expect(json.crm[key], `${locale} missing ${key}`).toEqual(expect.any(String));
        expect(String(json.crm[key])).not.toMatch(/[\u2013\u2014]/);
      }
      expect(String(json.crm.pageRange)).toContain("{{size}}");
      expect(String(json.crm.exportHint)).toContain(locale === "fr" ? "cette page" : "this page");
    }
  });
});
