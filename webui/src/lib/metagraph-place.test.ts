// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import { filtersActive, placeMetagraph, RELAYOUT_MAX_NODES } from "@/lib/metagraph-place";
import type { MetagraphNode } from "@/lib/types";

function node(id: string, kind = "front"): MetagraphNode {
  return {
    id,
    kind,
    size: 10,
    symbols: 1,
    in_degree: 1,
    out_degree: 1,
  };
}

describe("filtersActive", () => {
  it("is false with defaults", () => {
    expect(
      filtersActive({
        hidden: new Set(),
        query: "",
        connectedOnly: false,
        packageFocus: null,
      }),
    ).toBe(false);
  });

  it("is true for any active filter", () => {
    expect(
      filtersActive({
        hidden: new Set(["docs"]),
        query: "",
        connectedOnly: false,
        packageFocus: null,
      }),
    ).toBe(true);
    expect(
      filtersActive({
        hidden: new Set(),
        query: "foo",
        connectedOnly: false,
        packageFocus: null,
      }),
    ).toBe(true);
    expect(
      filtersActive({
        hidden: new Set(),
        query: "",
        connectedOnly: true,
        packageFocus: null,
      }),
    ).toBe(true);
  });
});

describe("placeMetagraph", () => {
  it("keeps full server bounds when nothing is filtered", () => {
    const a = node("a.ts");
    const b = node("b.ts");
    const result = placeMetagraph([a, b], [{ source: "a.ts", target: "b.ts" }], {
      aspect: 1.6,
      filtered: false,
      serverPositions: { "a.ts": { x: 10, y: 20 }, "b.ts": { x: 110, y: 20 } },
      layoutWidth: 400,
      layoutHeight: 300,
    });
    expect(result.width).toBe(400);
    expect(result.height).toBe(300);
    expect(result.nodes).toHaveLength(2);
  });

  it("crops to visible bounds when a large filtered set keeps server coords", () => {
    const nodes = Array.from({ length: RELAYOUT_MAX_NODES + 20 }, (_, i) =>
      node(`f${i}.ts`),
    );
    const positions: Record<string, { x: number; y: number }> = {};
    for (let i = 0; i < nodes.length; i += 1) {
      positions[nodes[i]!.id] = { x: 1000 + i, y: 2000 };
    }
    const result = placeMetagraph(nodes, [], {
      aspect: 1.6,
      filtered: true,
      serverPositions: positions,
      layoutWidth: 50_000,
      layoutHeight: 50_000,
    });
    // Must not keep the full-project box - that made filters look broken.
    expect(result.width).toBeLessThan(50_000);
    expect(result.height).toBeLessThan(50_000);
    expect(result.nodes.length).toBe(nodes.length);
    // Cropped nodes sit near the origin, not at x=1000+ in a huge viewBox.
    expect(Math.min(...result.nodes.map((n) => n.x))).toBeLessThan(100);
  });

  it("re-layouts a small filtered subset instead of leaving holes", () => {
    const a = node("webui/a.ts");
    const b = node("webui/b.ts");
    const result = placeMetagraph([a, b], [{ source: "webui/a.ts", target: "webui/b.ts" }], {
      aspect: 1.6,
      filtered: true,
      serverPositions: {
        "webui/a.ts": { x: 10, y: 10 },
        "webui/b.ts": { x: 9000, y: 9000 },
      },
      layoutWidth: 20_000,
      layoutHeight: 20_000,
    });
    const dx = Math.abs(result.nodes[0]!.x - result.nodes[1]!.x);
    const dy = Math.abs(result.nodes[0]!.y - result.nodes[1]!.y);
    // Force layout pulls neighbours together - not 9000 units apart.
    expect(Math.hypot(dx, dy)).toBeLessThan(2000);
  });
});
