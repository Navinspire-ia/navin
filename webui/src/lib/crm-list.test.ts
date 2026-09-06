import { describe, expect, it } from "vitest";

import { EMPTY_CRM_FACETS } from "@/store/crm-ui";

import {
  applyCrmFilters,
  countLabel,
  pageRange,
  paginateRows,
  recordHaystack,
  visiblePages,
} from "./crm-list";

const facets = { ...EMPTY_CRM_FACETS };

describe("crm list filters", () => {
  it("matches name, email, phone, company and title", () => {
    const row = {
      id: "c1",
      firstName: "Aymen",
      lastName: "Khelifi",
      email: "aymen@navin.ai",
      phone: "+331234",
      title: "CEO",
      company: "Navin",
    };
    expect(recordHaystack(row)).toContain("aymen");
    expect(applyCrmFilters([row], "navin", facets)).toHaveLength(1);
    expect(applyCrmFilters([row], "ceo", facets)).toHaveLength(1);
    expect(applyCrmFilters([row], "zzzz", facets)).toHaveLength(0);
  });

  it("filters status, owner, country and source", () => {
    const rows = [
      { id: "1", status: "actif", owner: "ada@x.com", country: "FR", source: "linkedin" },
      { id: "2", status: "inactif", owner: "ada@x.com", country: "DE", source: "website" },
    ];
    expect(applyCrmFilters(rows, "", { ...facets, status: "actif" })).toEqual([rows[0]]);
    expect(applyCrmFilters(rows, "", { ...facets, country: "DE" })).toEqual([rows[1]]);
    expect(applyCrmFilters(rows, "", { ...facets, source: "linkedin" })).toEqual([rows[0]]);
    expect(
      applyCrmFilters(rows, "", { ...facets, owner: "ada@x.com" }, {}, [
        { id: "m1", identity: "ada@x.com", email: "ada@x.com", handle: "ada", userId: "", displayName: "Ada", role: "owner", createdAt: 0 },
      ]),
    ).toHaveLength(2);
  });

  it("filters opportunity stage via statusKeys", () => {
    const rows = [
      { id: "1", stage: "gagne", name: "Deal A" },
      { id: "2", stage: "nouveau", name: "Deal B" },
    ];
    expect(
      applyCrmFilters(rows, "", { ...facets, status: "gagne" }, { statusKeys: ["stage"] }),
    ).toEqual([rows[0]]);
  });
});

describe("crm pagination helpers", () => {
  it("slices a page and formats the range", () => {
    const rows = Array.from({ length: 84 }, (_, index) => index + 1);
    expect(paginateRows(rows, 1, 20)).toEqual(rows.slice(0, 20));
    expect(pageRange(1, 20, 84)).toEqual({ from: 1, to: 20 });
    expect(pageRange(5, 20, 84)).toEqual({ from: 81, to: 84 });
    expect(countLabel(12, "contact", "contacts")).toBe("12 contacts");
    expect(countLabel(1, "contact", "contacts")).toBe("1 contact");
  });

  it("compresses long page lists", () => {
    expect(visiblePages(1, 4)).toEqual([1, 2, 3, 4]);
    expect(visiblePages(5, 12)).toContain("ellipsis");
    expect(visiblePages(5, 12)[0]).toBe(1);
    expect(visiblePages(5, 12).at(-1)).toBe(12);
  });
});
