// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import {
  authorizedPortals,
  emptyCareerDesk,
  FALLBACK_CATALOG,
  FALLBACK_STACK,
  officialCareerHref,
} from "@/lib/career-api";
import catalogUrls from "@/lib/career-catalog-urls.json";

describe("career sources fallback", () => {
  it("always exposes V1 boards and working search URLs", () => {
    const ids = FALLBACK_CATALOG.map((row) => row.id);
    expect(ids).toContain("france-travail");
    expect(ids).toContain("remotive");
    expect(ids).toContain("linkedin");
    const portals = authorizedPortals("Data Engineer freelance", ["FR", "US"]);
    expect(portals.some((row) => row.id === "li-FR")).toBe(true);
    expect(portals.some((row) => row.url.includes("francetravail.fr"))).toBe(true);
    expect(portals.some((row) => row.url.includes("linkedin.com/jobs/search"))).toBe(true);
    const desk = emptyCareerDesk();
    expect(desk.profile.wizard_complete).toBe(false);
    expect(ids).toContain("malt");
    expect(desk.catalog).toHaveLength(FALLBACK_CATALOG.length);
    expect(desk.stack?.tool).toBe("career");
    expect(desk.stack?.skills).toContain("career-agent");
    expect(FALLBACK_STACK.mcp.map((row) => row.id)).toEqual(["linkedin", "notion", "github", "exa"]);
    expect(FALLBACK_STACK.mcp[0]?.recommended).toBe(true);
    expect(FALLBACK_STACK.connectors.find((row) => row.id === "linkedin")?.ingest).toBe("public_listing");
    expect(ids).toContain("collective");
    expect(FALLBACK_STACK.connectors.find((row) => row.id === "collective")?.ingest).toBe("public_listing");
    expect(FALLBACK_STACK.connectors.find((row) => row.id === "collective")?.live).toBe(true);
    expect(FALLBACK_STACK.connectors.find((row) => row.id === "employers")?.live).toBe(true);
    expect(ids).toContain("employers");
    expect(officialCareerHref("#/career")).toBeNull();
    expect(officialCareerHref("#/tools")).toBeNull();
    expect(officialCareerHref("tauri://localhost/#/career")).toBeNull();
    expect(officialCareerHref("https://www.linkedin.com/jobs/view/1")).toContain("linkedin.com");
    expect(FALLBACK_STACK.connectors.find((row) => row.id === "web-job-search")?.live).toBe(true);
    const catalogIds = new Set(catalogUrls.map((row) => row.id));
    for (const row of FALLBACK_CATALOG) {
      expect(catalogIds.has(row.id) || catalogUrls.some((item) => item.url === row.url)).toBe(true);
    }
    expect(desk.opportunities).toEqual([]);
    expect(desk.loop?.enabled).toBe(false);
    expect(desk.loop?.schedule?.kind).toBe("daily");
    expect(desk.journal).toEqual([]);
  });
});
