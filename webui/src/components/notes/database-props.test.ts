/** Cell text <-> note property conversions used by the database views. */

import { describe, expect, it } from "vitest";

import { propToText, textToProp } from "./database-props";

describe("propToText", () => {
  it("renders scalars and lists as editable text", () => {
    expect(propToText(undefined)).toBe("");
    expect(propToText("En cours")).toBe("En cours");
    expect(propToText(3)).toBe("3");
    expect(propToText(true)).toBe("true");
    expect(propToText(["a", "b"])).toBe("a, b");
  });
});

describe("textToProp", () => {
  it("empty text deletes the property", () => {
    expect(textToProp("")).toBeNull();
    expect(textToProp("   ")).toBeNull();
  });

  it("parses booleans and numbers", () => {
    expect(textToProp("true")).toBe(true);
    expect(textToProp("false")).toBe(false);
    expect(textToProp("42")).toBe(42);
    expect(textToProp("-3.5")).toBe(-3.5);
  });

  it("comma-separated text becomes a list", () => {
    expect(textToProp("a, b, c")).toEqual(["a", "b", "c"]);
  });

  it("plain text stays text, including single trailing comma", () => {
    expect(textToProp("En cours")).toBe("En cours");
    expect(textToProp("seul,")).toBe("seul,");
  });

  it("round-trips through propToText", () => {
    for (const value of ["texte", 7, true, ["x", "y"]] as const) {
      expect(textToProp(propToText(value as never))).toEqual(value);
    }
  });
});
