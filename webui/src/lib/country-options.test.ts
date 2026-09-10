// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { SelectableOptionMenuItemType } from "@fluentui/react";
import { describe, expect, it } from "vitest";

import { countryDisplayName, countryDropdownOptions, countryIso } from "@/lib/country-options";

const COPY = { noneKey: "__none__", none: "Not set", inResults: "In the results", all: "All countries" };

describe("country facet options", () => {
  it("prints full names and keeps non ISO values readable", () => {
    expect(countryDisplayName("FR", "en")).toBe("France");
    expect(countryDisplayName("fr", "fr")).toBe("France");
    expect(countryDisplayName("AE", "fr")).toBe("Émirats arabes unis");
    expect(countryDisplayName("REMOTE", "en")).toBe("Remote");
    expect(countryDisplayName("INTL", "en")).toBe("INTL");
  });

  it("resolves what portals store: shouting names, alpha-3 and 16-character cuts", () => {
    expect(countryIso("HAITI")).toBe("HT");
    expect(countryIso("UKR")).toBe("UA");
    expect(countryIso("TURKIYE")).toBe("TR");
    expect(countryIso("KYRGYZ REPUBLIC")).toBe("KG");
    expect(countryIso("CENTRAL AFRICAN")).toBe("CF");
    expect(countryIso("CONGO, DEMOCRATI")).toBe("CD");
    expect(countryIso("WESTERN AND CENT")).toBe("");
    expect(countryDisplayName("CONGO, DEMOCRATI", "en")).toBe("DR Congo");
    expect(countryDisplayName("CENTRAL AFRICAN", "fr")).toBe("République centrafricaine");
    expect(countryDisplayName("WESTERN AND CENT", "en")).toBe("Western and Central Africa");
    expect(countryDisplayName("EASTERN AND SOUT", "fr")).toBe("Afrique de l'Est et australe");
    expect(countryDisplayName("SOME PORTAL VALUE", "en")).toBe("Some portal value");
  });

  it("lists the countries of the results first, then the whole world", () => {
    const options = countryDropdownOptions(["AE", "FR", "__none__", "REMOTE"], "en", COPY);
    expect(options[0]).toMatchObject({ text: "In the results", itemType: SelectableOptionMenuItemType.Header });
    const top = options.slice(1, 5).map((option) => option.text);
    expect(top).toEqual(["France", "Remote", "United Arab Emirates", "Not set"]);
    expect(options[5].itemType).toBe(SelectableOptionMenuItemType.Divider);
    expect(options[6]).toMatchObject({ text: "All countries", itemType: SelectableOptionMenuItemType.Header });
    const rest = options.slice(7);
    expect(rest.length).toBeGreaterThan(200);
    expect(rest.some((option) => option.key === "FR")).toBe(false);
    expect(rest.some((option) => option.text === "Germany")).toBe(true);
    const names = rest.map((option) => String(option.text));
    expect([...names].sort((a, b) => a.localeCompare(b, "en"))).toEqual(names);
  });

  it("skips the results block when the list is empty", () => {
    const options = countryDropdownOptions([], "fr", COPY);
    expect(options[0]).toMatchObject({ text: "All countries" });
    expect(options.some((option) => option.text === "Allemagne")).toBe(true);
  });
});
