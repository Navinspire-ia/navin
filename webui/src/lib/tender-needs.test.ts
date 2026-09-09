// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import {
  addNeedToken,
  needLabel,
  needSelectOptions,
  tokenAlreadyPicked,
  type NeedOption,
} from "@/lib/tender-needs";

const catalog: NeedOption[] = [
  {
    id: "ai",
    label: "AI",
    label_fr: "Intelligence artificielle",
    keywords: "ai llm",
    cpv: "72000000",
  },
  {
    id: "it-services",
    label: "IT services",
    label_fr: "Services informatiques",
    keywords: "informatique",
    cpv: "72000000",
  },
];

describe("tender need tokens", () => {
  it("stores the locale label and dedups catalog aliases", () => {
    expect(needLabel(catalog[0], "fr-FR")).toBe("Intelligence artificielle");
    const first = addNeedToken("AI", [], catalog);
    expect(addNeedToken("Intelligence artificielle", first, catalog)).toEqual(["AI"]);
    expect(tokenAlreadyPicked("ai", first, catalog)).toBe(true);
  });

  it("keeps free-typed values that are not in the catalog", () => {
    expect(addNeedToken("Ponts", ["Cloud"], catalog)).toEqual(["Cloud", "Ponts"]);
  });

  it("hides already picked catalog rows from the select", () => {
    const options = needSelectOptions(catalog, "en", ["AI"]);
    expect(options.map((row) => row.value)).toEqual(["it-services"]);
    expect(options[0]?.hint).toBe("72000000");
  });
});
