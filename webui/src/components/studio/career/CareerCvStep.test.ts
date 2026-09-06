import { describe, expect, it } from "vitest";

import { composeMasterCv } from "@/components/studio/career/CareerCvStep";

describe("composeMasterCv", () => {
  it("builds a factual dossier without inventing lines", () => {
    const text = composeMasterCv({
      name: "Aymen",
      headline: "Data Engineer",
      experiences: [
        { title: "Data Engineer", company: "Acme", period: "2022 - 2026", facts: "Spark on AWS" },
      ],
      education: [{ diploma: "MSc", school: "INSA", year: "2016" }],
      strengths: ["Python", "Spark"],
    });
    expect(text).toContain("Aymen");
    expect(text).toContain("Data Engineer - Acme (2022 - 2026)");
    expect(text).toContain("Spark on AWS");
    expect(text).toContain("MSc - INSA (2016)");
    expect(text).toContain("Python · Spark");
  });
});
