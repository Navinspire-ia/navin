import { describe, expect, it } from "vitest";

import { firstName } from "./useAccount";

describe("firstName", () => {
  it("keeps only a real first name, capitalized", () => {
    expect(firstName("aymen ghad")).toBe("Aymen");
    expect(firstName("aymen.ghad")).toBe("Aymen");
    expect(firstName("Léa")).toBe("Léa");
    expect(firstName("أيمن")).toBe("أيمن");
    expect(firstName("")).toBe("");
    expect(firstName("x")).toBe("");
    expect(firstName("user123")).toBe("");
  });
});
