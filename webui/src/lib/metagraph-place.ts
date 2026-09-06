/**
 * Place metagraph nodes for the canvas.
 *
 * Server positions describe the full project. Once the user filters, reusing
 * that box makes the remaining files look like dust in empty space - filters
 * appear broken. Crop to the visible bounds, or re-layout when the subset is
 * small enough to settle quickly.
 */

import { layoutGraph } from "@/lib/force-layout";
import type { MetagraphEdge, MetagraphNode } from "@/lib/types";

export interface PlacedMetagraphNode extends MetagraphNode {
  x: number;
  y: number;
}

export interface PlacedMetagraphEdge extends MetagraphEdge {
  from: { x: number; y: number };
  to: { x: number; y: number };
}

export interface PlaceResult {
  nodes: PlacedMetagraphNode[];
  edges: PlacedMetagraphEdge[];
  width: number;
  height: number;
}

/** Above this, keep server coords and crop - force layout would hitch. */
export const RELAYOUT_MAX_NODES = 350;

const CROP_PADDING = 48;

export function filtersActive(options: {
  hidden: ReadonlySet<string>;
  query: string;
  connectedOnly: boolean;
  packageFocus: string | null;
}): boolean {
  return (
    options.hidden.size > 0 ||
    options.query.trim() !== "" ||
    options.connectedOnly ||
    options.packageFocus != null
  );
}

export function placeMetagraph(
  visible: readonly MetagraphNode[],
  links: readonly MetagraphEdge[],
  options: {
    aspect: number;
    serverPositions?: Record<string, { x: number; y: number }> | null;
    layoutWidth?: number;
    layoutHeight?: number;
    filtered: boolean;
  },
): PlaceResult {
  const server = options.serverPositions;
  const hasServer = Boolean(server && Object.keys(server).length > 0);

  if (hasServer && server) {
    const placed = visible
      .map((node) => {
        const point = server[node.id];
        if (!point) return null;
        return { ...node, x: point.x, y: point.y };
      })
      .filter((node): node is PlacedMetagraphNode => node != null);

    // Missing coords for a large share of the filter → client layout instead.
    if (placed.length >= Math.max(1, Math.floor(visible.length * 0.85))) {
      if (!options.filtered) {
        return {
          nodes: placed,
          edges: linkEdges(links, new Map(placed.map((n) => [n.id, n]))),
          width: options.layoutWidth || 1,
          height: options.layoutHeight || 1,
        };
      }

      if (placed.length > RELAYOUT_MAX_NODES) {
        return cropToBounds(placed, links);
      }
      // Fall through to client re-layout for a compact filtered view.
    }
  }

  const layout = layoutGraph(visible, links, { aspect: options.aspect });
  const byId = layout.positions;
  return {
    nodes: visible.map((node) => ({ ...node, ...byId.get(node.id)! })),
    edges: links.map((edge) => ({
      ...edge,
      from: byId.get(edge.source)!,
      to: byId.get(edge.target)!,
    })),
    width: layout.width,
    height: layout.height,
  };
}

function cropToBounds(
  placed: readonly PlacedMetagraphNode[],
  links: readonly MetagraphEdge[],
): PlaceResult {
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const node of placed) {
    minX = Math.min(minX, node.x);
    minY = Math.min(minY, node.y);
    maxX = Math.max(maxX, node.x);
    maxY = Math.max(maxY, node.y);
  }
  if (!Number.isFinite(minX)) {
    return { nodes: [], edges: [], width: 1, height: 1 };
  }

  const nodes = placed.map((node) => ({
    ...node,
    x: node.x - minX + CROP_PADDING,
    y: node.y - minY + CROP_PADDING,
  }));
  const byId = new Map(nodes.map((node) => [node.id, node]));
  return {
    nodes,
    edges: linkEdges(links, byId),
    width: Math.max(1, maxX - minX + CROP_PADDING * 2),
    height: Math.max(1, maxY - minY + CROP_PADDING * 2),
  };
}

function linkEdges(
  links: readonly MetagraphEdge[],
  byId: Map<string, PlacedMetagraphNode>,
): PlacedMetagraphEdge[] {
  const edges: PlacedMetagraphEdge[] = [];
  for (const edge of links) {
    const from = byId.get(edge.source);
    const to = byId.get(edge.target);
    if (!from || !to) continue;
    edges.push({ ...edge, from: { x: from.x, y: from.y }, to: { x: to.x, y: to.y } });
  }
  return edges;
}
