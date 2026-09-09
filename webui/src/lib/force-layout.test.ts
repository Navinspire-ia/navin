// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import { layoutGraph } from "./force-layout";
import type { LayoutEdge, LayoutNode, Point } from "./force-layout";

function nodes(...ids: string[]): LayoutNode[] {
  return ids.map((id) => ({ id }));
}

function edge(source: string, target: string): LayoutEdge {
  return { source, target };
}

function distance(a: Point, b: Point): number {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

function at(positions: Map<string, Point>, id: string): Point {
  const point = positions.get(id);
  expect(point, `${id} was not placed`).toBeDefined();
  return point!;
}

describe("layoutGraph", () => {
  it("places every node it is given", () => {
    const { positions } = layoutGraph(nodes("a", "b", "c", "d"), [edge("a", "b")]);

    expect([...positions.keys()].sort()).toEqual(["a", "b", "c", "d"]);
  });

  it("returns nothing to draw for an empty graph", () => {
    const result = layoutGraph([], []);

    expect(result.positions.size).toBe(0);
    expect(result.width).toBe(0);
    expect(result.height).toBe(0);
  });

  it("puts the same graph in the same place twice", () => {
    // A graph that lands somewhere new on every reload cannot be recognised,
    // which is why the layout starts from a spiral and never from random.
    const graph = [nodes("a", "b", "c", "d", "e"), [edge("a", "b"), edge("b", "c"), edge("d", "e")]] as const;

    const first = layoutGraph(...graph);
    const second = layoutGraph(...graph);

    for (const [id, point] of first.positions) {
      expect(at(second.positions, id)).toEqual(point);
    }
    expect(second.width).toBe(first.width);
  });

  it("keeps neighbours closer than unrelated nodes", () => {
    const { positions } = layoutGraph(
      nodes("core", "near", "far1", "far2", "far3", "far4"),
      [edge("core", "near"), edge("far1", "far2"), edge("far2", "far3"), edge("far3", "far4")],
    );

    expect(distance(at(positions, "core"), at(positions, "near"))).toBeLessThan(
      distance(at(positions, "core"), at(positions, "far3")),
    );
  });

  it("keeps a hub nearer the middle of its cluster than its leaves", () => {
    const leaves = ["l1", "l2", "l3", "l4", "l5", "l6"];
    const { positions } = layoutGraph(
      nodes("hub", ...leaves),
      leaves.map((leaf) => edge("hub", leaf)),
    );

    const points = [...positions.values()];
    const centre = {
      x: points.reduce((sum, point) => sum + point.x, 0) / points.length,
      y: points.reduce((sum, point) => sum + point.y, 0) / points.length,
    };
    const hubToCentre = distance(at(positions, "hub"), centre);
    for (const leaf of leaves) {
      expect(hubToCentre).toBeLessThan(distance(at(positions, leaf), centre));
    }
  });

  it("never stacks two nodes on the same spot", () => {
    const ids = Array.from({ length: 24 }, (_, index) => `n${index}`);
    const { positions } = layoutGraph(nodes(...ids), []);

    const points = [...positions.values()];
    for (let i = 0; i < points.length; i += 1) {
      for (let j = i + 1; j < points.length; j += 1) {
        expect(distance(points[i], points[j])).toBeGreaterThan(1);
      }
    }
  });

  it("respects the minimum distance a label needs", () => {
    // Overlapping nodes are readable as dots but their labels become one smear.
    const ids = Array.from({ length: 30 }, (_, index) => `n${index}`);
    const { positions } = layoutGraph(
      nodes(...ids),
      ids.slice(1).map((id) => edge("n0", id)),
      { minDistance: 34 },
    );

    const points = [...positions.values()];
    for (let i = 0; i < points.length; i += 1) {
      for (let j = i + 1; j < points.length; j += 1) {
        expect(distance(points[i], points[j])).toBeGreaterThanOrEqual(33);
      }
    }
  });

  it("keeps two names from printing over each other", () => {
    // A label is wide and short, so nodes far enough apart as dots can still
    // collide as text, which is the overlap a reader actually notices.
    const ids = Array.from({ length: 26 }, (_, index) => `n${index}`);
    const { positions } = layoutGraph(
      nodes(...ids),
      ids.slice(1).map((id) => edge("n0", id)),
    );

    const points = [...positions.values()];
    for (let i = 0; i < points.length; i += 1) {
      for (let j = i + 1; j < points.length; j += 1) {
        const sideways = Math.abs(points[i].x - points[j].x);
        const apart = Math.abs(points[i].y - points[j].y);
        expect(sideways < 90 && apart < 18).toBe(false);
      }
    }
  });

  it("separates two clusters that share no edge", () => {
    const links = [edge("a1", "a2"), edge("a2", "a3"), edge("b1", "b2"), edge("b2", "b3")];
    const { positions } = layoutGraph(nodes("a1", "a2", "a3", "b1", "b2", "b3"), links);

    // Reading as two groups means a linked pair is closer than any unlinked one,
    // not that the gap exceeds a whole cluster: a wide cluster would then demand
    // a gap wider than the screen.
    const longestLink = Math.max(
      ...links.map((link) => distance(at(positions, link.source), at(positions, link.target))),
    );
    const closest = Math.min(
      ...["a1", "a2", "a3"].flatMap((left) =>
        ["b1", "b2", "b3"].map((right) => distance(at(positions, left), at(positions, right))),
      ),
    );

    expect(closest).toBeGreaterThan(longestLink);
  });

  it("reports bounds that contain every node", () => {
    const ids = Array.from({ length: 40 }, (_, index) => `n${index}`);
    const { positions, width, height } = layoutGraph(
      nodes(...ids),
      ids.slice(1).map((id, index) => edge(ids[index], id)),
    );

    for (const point of positions.values()) {
      expect(point.x).toBeGreaterThanOrEqual(0);
      expect(point.y).toBeGreaterThanOrEqual(0);
      expect(point.x).toBeLessThanOrEqual(width);
      expect(point.y).toBeLessThanOrEqual(height);
    }
  });

  it("wraps many single files into rows rather than one long line", () => {
    const ids = Array.from({ length: 60 }, (_, index) => `n${index}`);
    const { width, height } = layoutGraph(nodes(...ids), []);

    expect(height).toBeGreaterThan(0);
    expect(width / height).toBeLessThan(6);
  });

  it("packs to the shape of the room it was given", () => {
    // A graph packed for a wide strip leaves bands of nothing in a tall panel.
    const ids = Array.from({ length: 48 }, (_, index) => `n${index}`);
    const links = ids.slice(1).map((id, index) => edge(ids[index], id));

    const wide = layoutGraph(nodes(...ids), links, { aspect: 3 });
    const tall = layoutGraph(nodes(...ids), links, { aspect: 0.6 });

    expect(wide.width / wide.height).toBeGreaterThan(tall.width / tall.height);
  });

  it("ignores edges pointing at files the filters removed", () => {
    const { positions } = layoutGraph(nodes("a", "b"), [
      edge("a", "gone"),
      edge("gone", "b"),
      edge("a", "a"),
    ]);

    expect([...positions.keys()].sort()).toEqual(["a", "b"]);
  });

  it("lines unconnected files up on shared rows", () => {
    const orphans = Array.from({ length: 12 }, (_, index) => `n${index}`);
    const { positions } = layoutGraph(nodes(...orphans), []);

    const rows = new Set([...positions.values()].map((point) => point.y));
    expect(rows.size).toBeLessThan(orphans.length);
    // Whatever the row count, a row holds files at one height and even spacing.
    for (const row of rows) {
      const columns = [...positions.values()]
        .filter((point) => point.y === row)
        .map((point) => point.x)
        .sort((left, right) => left - right);
      const steps = new Set(columns.slice(1).map((x, index) => x - columns[index]));
      expect(steps.size).toBeLessThanOrEqual(1);
    }
  });

  it("keeps unconnected files clear of the cluster that has structure", () => {
    const cluster = ["c1", "c2", "c3", "c4"];
    const { positions } = layoutGraph(
      nodes(...cluster, "orphan"),
      [edge("c1", "c2"), edge("c2", "c3"), edge("c3", "c4"), edge("c4", "c1")],
    );

    const orphan = at(positions, "orphan");
    const widest = Math.max(
      ...cluster.flatMap((left) =>
        cluster.map((right) => distance(at(positions, left), at(positions, right))),
      ),
    );
    const nearest = Math.min(...cluster.map((id) => distance(at(positions, id), orphan)));
    expect(nearest).toBeGreaterThan(widest);
  });

  it("lays out a few hundred files quickly enough to run while rendering", () => {
    const ids = Array.from({ length: 400 }, (_, index) => `n${index}`);
    const links = ids.map((id, index) => edge(id, ids[(index * 7 + 3) % ids.length]));

    const started = performance.now();
    const { positions } = layoutGraph(nodes(...ids), links);

    expect(positions.size).toBe(ids.length);
    // Guards the cost, not the speed of this machine: a pass per node pair per
    // iteration is fine, a pass per triple is not.
    expect(performance.now() - started).toBeLessThan(2000);
  });

  it("places a lone file without needing a simulation", () => {
    const { positions, width, height } = layoutGraph(nodes("only"), [], { padding: 20 });

    expect(at(positions, "only")).toEqual({ x: 20, y: 20 });
    expect(width).toBeGreaterThan(0);
    expect(height).toBeGreaterThan(0);
  });
});
