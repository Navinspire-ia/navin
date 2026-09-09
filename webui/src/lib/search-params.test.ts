// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import { searchParams } from "@/lib/api";

describe("searchParams", () => {
  it("sets semantic=1 for embedding search", () => {
    const params = searchParams("auth middleware", { semantic: true });
    expect(params.get("q")).toBe("auth middleware");
    expect(params.get("semantic")).toBe("1");
    expect(params.get("regex")).toBeNull();
    expect(params.get("case")).toBeNull();
  });

  it("keeps lexical flags when semantic is off", () => {
    const params = searchParams("foo", {
      regex: true,
      caseSensitive: true,
      include: "src/**",
      exclude: "dist/**",
    });
    expect(params.get("semantic")).toBeNull();
    expect(params.get("regex")).toBe("1");
    expect(params.get("case")).toBe("1");
    expect(params.get("include")).toBe("src/**");
    expect(params.get("exclude")).toBe("dist/**");
  });
});
