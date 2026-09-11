// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import {
  STUDIO_MODULE_IDS,
  isStudioModuleId,
  moveStudioModule,
  placeStudioModule,
  studioModuleAtY,
  normalizeStudioHidden,
  normalizeStudioOrder,
  studioLayoutIsDefault,
  toggleStudioHidden,
  visibleStudioModules,
} from "@/lib/studio-modules";

describe("studio module layout", () => {
  it("keeps the catalog order and drops unknown ids", () => {
    expect(normalizeStudioOrder(["career", "ghost", "career", "tenders"])).toEqual([
      "career",
      "tenders",
      ...STUDIO_MODULE_IDS.filter((id) => id !== "career" && id !== "tenders"),
    ]);
    expect(isStudioModuleId("scraping")).toBe(true);
    expect(isStudioModuleId("ghost")).toBe(false);
  });

  it("hides modules in the sidebar, including the one already open", () => {
    const order = normalizeStudioOrder([]);
    const hidden = normalizeStudioHidden(["trading", "ads", "ghost"]);
    expect(hidden).toEqual(["trading", "ads"]);
    expect(visibleStudioModules(order, hidden)).not.toContain("trading");
    expect(visibleStudioModules(order, hidden)).not.toContain("ads");
    expect(visibleStudioModules(["tenders"], ["tenders"])).toEqual([]);
  });

  it("moves a row and toggles visibility", () => {
    const start = normalizeStudioOrder(["tenders", "career"]);
    expect(moveStudioModule(start, "tenders", 1)[0]).toBe("career");
    expect(moveStudioModule(start, "tenders", -1)[0]).toBe("tenders");
    expect(placeStudioModule(start, "tenders", "career")[0]).toBe("career");
    expect(placeStudioModule(start, "career", "tenders")[0]).toBe("career");
    expect(toggleStudioHidden([], "seo", true)).toEqual(["seo"]);
    expect(toggleStudioHidden(["seo", "ads"], "seo", false)).toEqual(["ads"]);
    expect(studioLayoutIsDefault(STUDIO_MODULE_IDS, [])).toBe(true);
    expect(studioLayoutIsDefault(moveStudioModule(STUDIO_MODULE_IDS, "tenders", 1), [])).toBe(false);
    expect(studioModuleAtY(
      [
        { id: "tenders", top: 0, bottom: 40 },
        { id: "career", top: 40, bottom: 80 },
      ],
      55,
    )).toBe("career");
    expect(studioModuleAtY(
      [
        { id: "tenders", top: 0, bottom: 40 },
        { id: "career", top: 40, bottom: 80 },
      ],
      -10,
    )).toBe("tenders");
  });
});
