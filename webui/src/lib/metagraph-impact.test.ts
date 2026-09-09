// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import { impactDependents, impactIds } from "@/lib/metagraph-impact";
import type { MetagraphPayload } from "@/lib/types";

function graph(edges: Array<[string, string]>): MetagraphPayload {
  const ids = new Set<string>();
  for (const [source, target] of edges) {
    ids.add(source);
    ids.add(target);
  }
  return {
    project_path: "/tmp",
    nodes: [...ids].map((id) => ({
      id,
      kind: "other",
      size: 1,
      in_degree: 0,
      out_degree: 0,
    })),
    edges: edges.map(([source, target]) => ({ source, target })),
    kinds: {},
    has_metadata: false,
    annotated: 0,
    truncated: false,
  };
}

describe("metagraph impact", () => {
  it("walks reverse edges for who breaks", () => {
    const payload = graph([
      ["app.ts", "lib.ts"],
      ["test.ts", "lib.ts"],
      ["lib.ts", "util.ts"],
    ]);
    const ids = impactIds(payload, "util.ts");
    expect(ids.has("util.ts")).toBe(true);
    expect(ids.has("lib.ts")).toBe(true);
    expect(ids.has("app.ts")).toBe(true);
    expect(ids.has("test.ts")).toBe(true);
  });

  it("lists dependents without the root", () => {
    const payload = graph([
      ["a.ts", "b.ts"],
      ["c.ts", "b.ts"],
    ]);
    expect(impactDependents(payload, "b.ts")).toEqual(["a.ts", "c.ts"]);
  });
});
