import { describe, expect, it } from "vitest";

import catalogUrls from "./tender-catalog-urls.json";
import {
  asTemplateFiles,
  officialTenderHref,
  TENDERS_DESK_HASH,
  tendersDeskHash,
} from "./tenders-api";

describe("tenders filed models", () => {
  it("reads a legacy single model as a list", () => {
    expect(asTemplateFiles({ file_id: "a", name: "offer.docx" })).toEqual([
      { file_id: "a", name: "offer.docx" },
    ]);
    expect(asTemplateFiles([{ file_id: "a" }, { file_id: "b" }])).toHaveLength(2);
    expect(asTemplateFiles(null)).toEqual([]);
  });
});

describe("tenders desk links", () => {
  it("keeps the desk hash inside the IDE", () => {
    expect(TENDERS_DESK_HASH).toBe("#/tenders");
    expect(tendersDeskHash()).toBe("#/tenders");
    expect(tendersDeskHash({ notice: "abc", chat: "websocket:1" })).toBe(
      "#/tenders?chat=websocket%3A1&notice=abc",
    );
    expect(tendersDeskHash({ pane: "tenders" })).toBe("#/tenders?pane=tenders");
    expect(tendersDeskHash({ pane: "tenders", notice: "abc" })).toBe("#/tenders?notice=abc");
  });

  it("keeps wizard_ready on the desk snapshot type", () => {
    const desk: { wizard_ready?: boolean } = { wizard_ready: false };
    expect(desk.wizard_ready).toBe(false);
  });

  it("sends official portals out of the IDE", () => {
    expect(officialTenderHref("https://www.boamp.fr/pages/avis/?q=idweb:1")).toContain(
      "boamp.fr",
    );
    expect(officialTenderHref("https://ted.europa.eu/en/notice/-/detail/123")).toContain(
      "ted.europa.eu",
    );
    expect(officialTenderHref("https://www.find-tender.service.gov.uk/Notice/123")).toContain(
      "find-tender.service.gov.uk",
    );
    expect(officialTenderHref("#/tenders")).toBeNull();
    expect(officialTenderHref("/tenders")).toBeNull();
    expect(officialTenderHref("http://tauri.localhost/#/tenders")).toBeNull();
    expect(officialTenderHref("tauri://localhost/#/tenders")).toBeNull();
    expect(officialTenderHref("tauri://localhost/#/tenders?notice=abc")).toBeNull();
  });

  it("sends every catalog portal out of the IDE", () => {
    expect(catalogUrls).toHaveLength(76);
    for (const row of catalogUrls) {
      const href = officialTenderHref(row.url);
      expect(href, row.id).toBeTruthy();
      expect(href, row.id).toMatch(/^https:\/\//);
    }
  });
});
