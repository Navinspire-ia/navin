import { describe, expect, it } from "vitest";

import { filterWikilinkTargets, wikilinkQueryAt } from "./wikilink-extension";

describe("wikilinkQueryAt", () => {
  it("finds an incomplete wikilink at the caret", () => {
    expect(wikilinkQueryAt("Voir [[Proj", 11)).toEqual({
      query: "Proj",
      from: 5,
      to: 11,
    });
  });

  it("ignores completed links", () => {
    expect(wikilinkQueryAt("[[Projet]]", 10)).toBeNull();
  });
});

describe("filterWikilinkTargets", () => {
  const targets = [
    { id: "1", title: "Roadmap", aliases: ["Plan produit"] },
    { id: "2", title: "Réunion", aliases: ["Daily"] },
  ];

  it("matches titles and aliases", () => {
    expect(filterWikilinkTargets(targets, "plan")).toEqual([targets[0]]);
    expect(filterWikilinkTargets(targets, "daily")).toEqual([targets[1]]);
  });
});
