import { describe, expect, it } from "vitest";
import catalog from "../../../navin/career/role_catalog.json";
import { CAREER_ROLE_MATRIX } from "./career-role-matrix";
import { initializeCompanyCriteria, updateCompanyCriteria } from "./career-company-autofill";
import { emptyProspecting } from "./career-prospecting";

describe("company filter settings", () => {
  it("keeps the packaged matching vocabulary aligned with selectable bilingual roles and skills", () => {
    expect(catalog).toEqual(CAREER_ROLE_MATRIX);
  });

  it("migrates the old floor once and preserves independent floors, including zero", () => {
    const migrated = initializeCompanyCriteria({ ...structuredClone(emptyProspecting.criteria), sale_rate: 650, min_rate: 700 }, "fr");
    expect(migrated.sale_rate_remote).toBe(700);
    expect(migrated.sale_rate_onsite).toBe(700);
    const changed = updateCompanyCriteria(migrated, "sale_rate_remote", 300, "fr");
    expect(changed.sale_rate_remote).toBe(300);
    expect(changed.sale_rate_onsite).toBe(700);
    const saved = JSON.parse(JSON.stringify(updateCompanyCriteria(changed, "sale_rate_remote", 0, "fr")));
    const reloaded = initializeCompanyCriteria(saved, "en");
    expect(reloaded.sale_rate_remote).toBe(0);
    expect(reloaded.sale_rate_onsite).toBe(700);
  });
});
