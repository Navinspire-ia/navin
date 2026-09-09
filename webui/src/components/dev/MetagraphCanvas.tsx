// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Maximize2, Minus, Plus } from "lucide-react";

import { labelSeparation } from "@/lib/force-layout";
import type { MetagraphNode } from "@/lib/types";
import { cn } from "@/lib/utils";

export interface PlacedNode extends MetagraphNode {
  x: number;
  y: number;
}

export interface PlacedEdge {
  source: string;
  target: string;
  from: { x: number; y: number };
  to: { x: number; y: number };
}

interface View {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** Screen sizes, kept constant while zooming by dividing by the current scale. */
const NODE_RADIUS = 4;
const MAX_NODE_RADIUS = 9;
const LABEL_GAP = 6;
const FONT_SIZE = 11.5;

/**
 * Size a node by how many files it touches.
 *
 * One radius for every file made the module half the project imports look like
 * the note nobody reads, and which files a project turns on is the first thing
 * an import graph should show.
 */
function radiusOf(node: PlacedNode): number {
  const degree = node.in_degree + node.out_degree;
  return Math.min(MAX_NODE_RADIUS, NODE_RADIUS + Math.sqrt(degree) * 1.1);
}

const MIN_ZOOM = 0.2;
const MAX_ZOOM = 8;

/** Average width of a character at FONT_SIZE, used to fit a name in its gap. */
const CHAR_WIDTH = 6.2;
/** Under this much room a name is down to two letters, so only hubs keep one. */
const MIN_LABEL_WIDTH = 44;
/** Shortest a name may be cut to before showing it stops being worth the ink. */
const MIN_CHARS = 4;
/** Neighbours of the node being read keep this much name at any zoom. */
const MIN_RELATED_CHARS = 8;
const HUB_LABELS = 14;
/** Past this many edges, overview draws none until hover/selection. */
const EDGE_OVERVIEW_CAP = 700;
/** Past this many nodes, only hubs keep labels when zoomed out. */
const DENSE_NODE_COUNT = 250;

function truncate(name: string, chars: number): string {
  if (name.length <= chars) return name;
  return `${name.slice(0, Math.max(1, chars - 1))}…`;
}

export function MetagraphCanvas({
  nodes,
  edges,
  width,
  height,
  hovered,
  selected,
  colorOf,
  emptyLabel,
  zoomInLabel,
  zoomOutLabel,
  fitLabel,
  onHover,
  onSelect,
  onOpen,
  onAspect,
  highlight,
}: {
  nodes: readonly PlacedNode[];
  edges: readonly PlacedEdge[];
  width: number;
  height: number;
  hovered: string | null;
  selected: string | null;
  colorOf: (kind: string) => string;
  emptyLabel: string;
  zoomInLabel: string;
  zoomOutLabel: string;
  fitLabel: string;
  onHover: (id: string | null) => void;
  onSelect: (id: string | null) => void;
  onOpen: (node: PlacedNode) => void;
  /** Shape of the room available, so the layout can be packed to fill it. */
  onAspect?: (aspect: number) => void;
  /** When set, only these nodes/edges stay bright (path / impact cone). */
  highlight?: ReadonlySet<string> | null;
}) {
  const frameRef = useRef<HTMLDivElement | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [frame, setFrame] = useState({ width: 0, height: 0 });
  const [view, setView] = useState<View>({ x: 0, y: 0, width: 1, height: 1 });

  useLayoutEffect(() => {
    const element = frameRef.current;
    if (!element) return;
    const measure = () => {
      const width = element.clientWidth;
      const height = element.clientHeight;
      setFrame({ width, height });
      // Rounded, because the layout is recomputed when this changes and a few
      // pixels of resize are not a new shape.
      if (height > 0) onAspect?.(Math.round((width / height) * 10) / 10);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [onAspect]);

  const fit = useCallback(() => {
    setView({ x: 0, y: 0, width: Math.max(1, width), height: Math.max(1, height) });
  }, [width, height]);

  // A new set of filters is a new graph, and keeping the old window would leave
  // the user looking at empty space where their nodes used to be.
  useEffect(() => {
    fit();
  }, [fit]);

  // Pixels per graph unit. preserveAspectRatio fits the whole box, so the
  // smaller of the two ratios is what the browser actually applied.
  const scale =
    frame.width > 0 && frame.height > 0
      ? Math.min(frame.width / view.width, frame.height / view.height)
      : 1;
  const px = useCallback((value: number) => value / scale, [scale]);

  const zoomBy = useCallback(
    (factor: number, anchor?: { x: number; y: number }) => {
      setView((current) => {
        const baseWidth = Math.max(1, width);
        const nextWidth = clamp(
          current.width / factor,
          baseWidth / MAX_ZOOM,
          baseWidth / MIN_ZOOM,
        );
        const ratio = nextWidth / current.width;
        const nextHeight = current.height * ratio;
        const point = anchor ?? {
          x: current.x + current.width / 2,
          y: current.y + current.height / 2,
        };
        return {
          // Keep whatever sits under the pointer under the pointer.
          x: point.x - (point.x - current.x) * ratio,
          y: point.y - (point.y - current.y) * ratio,
          width: nextWidth,
          height: nextHeight,
        };
      });
    },
    [width],
  );

  const toGraph = useCallback(
    (event: { clientX: number; clientY: number }) => {
      const element = svgRef.current;
      if (!element) return null;
      const box = element.getBoundingClientRect();
      // The rendered box can be wider than the drawing: with a uniform scale the
      // rest is empty margin, so subtract it before mapping.
      const drawn = { width: view.width * scale, height: view.height * scale };
      const offsetX = (box.width - drawn.width) / 2;
      const offsetY = (box.height - drawn.height) / 2;
      return {
        x: view.x + (event.clientX - box.left - offsetX) / scale,
        y: view.y + (event.clientY - box.top - offsetY) / scale,
      };
    },
    [view, scale],
  );

  // React attaches wheel handlers passively, which cannot stop the page from
  // scrolling behind the graph, so this one is bound by hand.
  useEffect(() => {
    const element = frameRef.current;
    if (!element) return;
    const onWheel = (event: WheelEvent) => {
      if (event.ctrlKey) return;
      event.preventDefault();
      const factor = Math.exp(-event.deltaY * 0.0016);
      zoomBy(factor, toGraph(event) ?? undefined);
    };
    element.addEventListener("wheel", onWheel, { passive: false });
    return () => element.removeEventListener("wheel", onWheel);
  }, [zoomBy, toGraph]);

  const dragRef = useRef<{ x: number; y: number; moved: boolean } | null>(null);
  /** Set when a drag actually moved, so the click it ends with selects nothing. */
  const draggedRef = useRef(false);
  const [panning, setPanning] = useState(false);

  const onPointerDown = (event: React.PointerEvent<SVGSVGElement>) => {
    if (event.button !== 0) return;
    // Cleared here rather than by the click that follows a drag: a drag released
    // outside the canvas gets no click, and the flag would eat the next one.
    draggedRef.current = false;
    dragRef.current = { x: event.clientX, y: event.clientY, moved: false };
    event.currentTarget.setPointerCapture(event.pointerId);
  };

  const onPointerMove = (event: React.PointerEvent<SVGSVGElement>) => {
    const drag = dragRef.current;
    if (!drag) return;
    const dx = event.clientX - drag.x;
    const dy = event.clientY - drag.y;
    if (!drag.moved && Math.hypot(dx, dy) < 3) return;
    drag.moved = true;
    setPanning(true);
    drag.x = event.clientX;
    drag.y = event.clientY;
    setView((current) => ({ ...current, x: current.x - dx / scale, y: current.y - dy / scale }));
  };

  const endDrag = (event: React.PointerEvent<SVGSVGElement>) => {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    setPanning(false);
    const drag = dragRef.current;
    dragRef.current = null;
    if (drag?.moved) draggedRef.current = true;
  };

  const neighbours = useMemo(() => {
    const map = new Map<string, Set<string>>();
    for (const edge of edges) {
      if (!map.has(edge.source)) map.set(edge.source, new Set());
      if (!map.has(edge.target)) map.set(edge.target, new Set());
      map.get(edge.source)!.add(edge.target);
      map.get(edge.target)!.add(edge.source);
    }
    return map;
  }, [edges]);

  // The busiest files keep their name at any zoom: they are the landmarks the
  // rest of the map is read against.
  const hubs = useMemo(() => {
    const hubCap = nodes.length > DENSE_NODE_COUNT ? 10 : HUB_LABELS;
    const ranked = [...nodes]
      .filter((node) => node.in_degree + node.out_degree > 0)
      .sort((a, b) => b.in_degree + b.out_degree - (a.in_degree + a.out_degree))
      .slice(0, hubCap);
    return new Set(ranked.map((node) => node.id));
  }, [nodes]);

  const active = hovered ?? selected;
  const activeNeighbours = active ? neighbours.get(active) : undefined;
  const hasHighlight = Boolean(highlight && highlight.size > 0);
  // Dimming answers "what does this touch". On a file nothing imports there is
  // no answer, so fading the whole graph would cost contrast and tell nothing.
  const dimOthers = hasHighlight || (activeNeighbours?.size ?? 0) > 0;
  // The layout keeps nodes a fixed distance apart in its own units; on screen
  // that distance is whatever the zoom makes of it, and that is what a name has
  // to fit into. Zoomed out, only the landmarks keep a name.
  const labelRoom = labelSeparation() * scale;
  const dense = nodes.length > DENSE_NODE_COUNT;
  const showEveryLabel = !dense && labelRoom >= MIN_LABEL_WIDTH;

  // Hairball of 2k edges hides the clusters. Overview keeps nodes; links appear
  // on hover / selection / path highlight.
  const drawnEdges = useMemo(() => {
    if (hasHighlight && highlight) {
      return edges.filter(
        (edge) => highlight.has(edge.source) && highlight.has(edge.target),
      );
    }
    if (active) {
      return edges.filter((edge) => edge.source === active || edge.target === active);
    }
    if (edges.length > EDGE_OVERVIEW_CAP) {
      return edges.filter(
        (edge) => hubs.has(edge.source) && hubs.has(edge.target),
      );
    }
    return edges;
  }, [edges, active, hasHighlight, highlight, hubs]);

  // SVG paints in order, so the node being read has to come last or a neighbour's
  // label lands on top of the full name we just chose to spell out.
  const ordered = active
    ? [...nodes.filter((node) => node.id !== active), ...nodes.filter((node) => node.id === active)]
    : nodes;

  if (nodes.length === 0) {
    return (
      <div className="flex min-h-0 flex-1 items-center justify-center text-[13px] text-muted-foreground">
        {emptyLabel}
      </div>
    );
  }

  return (
    <div ref={frameRef} className="relative min-h-0 flex-1 overflow-hidden bg-background">
      <svg
        ref={svgRef}
        className={cn("h-full w-full text-foreground", panning ? "cursor-grabbing" : "cursor-grab")}
        viewBox={`${view.x} ${view.y} ${view.width} ${view.height}`}
        preserveAspectRatio="xMidYMid meet"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onClick={() => {
          if (draggedRef.current) {
            draggedRef.current = false;
            return;
          }
          onSelect(null);
        }}
      >
        {drawnEdges.map((edge, index) => {
          const outgoing = active === edge.source;
          const incoming = active === edge.target;
          const onPath =
            hasHighlight &&
            Boolean(highlight?.has(edge.source) && highlight?.has(edge.target));
          const touched = hasHighlight ? onPath : outgoing || incoming;
          return (
            <path
              key={`${edge.source}->${edge.target}:${index}`}
              d={curve(edge)}
              fill="none"
              // currentColor, not a hex: fixed dark edges vanish on the dark theme.
              stroke="currentColor"
              strokeWidth={px(touched ? 1.5 : 0.7)}
              // Solid out, dashed in: which way a dependency runs is the question
              // an import graph exists to answer.
              strokeDasharray={incoming && !hasHighlight ? `${px(4)} ${px(3)}` : undefined}
              opacity={!dimOthers ? 0.22 : touched ? 0.9 : 0.05}
            />
          );
        })}

        {ordered.map((node) => {
          const isActive = active === node.id;
          const related = hasHighlight
            ? Boolean(highlight?.has(node.id))
            : Boolean(activeNeighbours?.has(node.id));
          const dimmed = dimOthers && !isActive && !related;
          const radius = px(radiusOf(node));
          const name = node.id.split("/").pop() ?? node.id;
          // Cut to the room the layout reserved, not to a round number of
          // letters: a name is shortened because the next node is there.
          const fits = Math.floor((labelRoom - LABEL_GAP - radiusOf(node)) / CHAR_WIDTH);
          // Landmarks and the node being read are spelled out whatever the zoom.
          // "in…" is not a name, and the few files that carry a graph are worth
          // the risk of a collision; the rest is dimmed behind them anyway.
          const spelled = isActive || hubs.has(node.id);
          const label = spelled
            ? name
            : truncate(name, related ? Math.max(MIN_RELATED_CHARS, fits) : fits);
          const labelled = spelled || related || (showEveryLabel && fits >= MIN_CHARS);
          return (
            <g
              key={node.id}
              transform={`translate(${node.x}, ${node.y})`}
              className="cursor-pointer"
              opacity={dimmed ? 0.2 : 1}
              onMouseEnter={() => onHover(node.id)}
              onMouseLeave={() => onHover(null)}
              onClick={(event) => {
                event.stopPropagation();
                if (draggedRef.current) {
                  draggedRef.current = false;
                  return;
                }
                onSelect(selected === node.id ? null : node.id);
              }}
              onDoubleClick={(event) => {
                event.stopPropagation();
                onOpen(node);
              }}
            >
              <circle
                r={radius}
                fill={colorOf(node.kind)}
                // Order matters: a stale role is still a described file, and it
                // also has a role, so amber has to win over the plain ring.
                stroke={
                  selected === node.id
                    ? "currentColor"
                    : node.role_stale
                      ? "#f59e0b"
                      : node.role
                        ? "currentColor"
                        : "none"
                }
                strokeWidth={px(selected === node.id ? 2.5 : node.role_stale ? 1.75 : node.role ? 1 : 0)}
              />
              {labelled ? (
                <text
                  x={radius + px(LABEL_GAP)}
                  y={px(FONT_SIZE * 0.35)}
                  className="pointer-events-none select-none"
                  fontSize={px(FONT_SIZE)}
                  fill="currentColor"
                  fontWeight={isActive ? 600 : 400}
                  opacity={isActive || related ? 1 : 0.75}
                >
                  {label}
                </text>
              ) : null}
            </g>
          );
        })}
      </svg>

      <div className="absolute right-2 top-2 flex flex-col gap-1">
        {[
          { icon: Plus, label: zoomInLabel, run: () => zoomBy(1.3) },
          { icon: Minus, label: zoomOutLabel, run: () => zoomBy(1 / 1.3) },
          { icon: Maximize2, label: fitLabel, run: fit },
        ].map(({ icon: Icon, label, run }) => (
          <button
            key={label}
            type="button"
            onClick={run}
            aria-label={label}
            title={label}
            className="rounded-md border border-border/60 bg-background/85 p-1.5 text-muted-foreground shadow-sm backdrop-blur transition-colors hover:bg-muted hover:text-foreground"
          >
            <Icon className="h-3.5 w-3.5" aria-hidden />
          </button>
        ))}
      </div>
    </div>
  );
}

/**
 * Bow each edge slightly.
 *
 * Straight lines make two files that import each other draw one line, and a bend
 * that follows the direction of travel splits them into a readable pair.
 */
function curve(edge: PlacedEdge): string {
  const { from, to } = edge;
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const length = Math.hypot(dx, dy);
  if (length < 0.5) return `M ${from.x} ${from.y} L ${to.x} ${to.y}`;
  const bow = Math.min(28, length * 0.1);
  const midX = (from.x + to.x) / 2 - (dy / length) * bow;
  const midY = (from.y + to.y) / 2 + (dx / length) * bow;
  return `M ${from.x} ${from.y} Q ${midX} ${midY} ${to.x} ${to.y}`;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

export default MetagraphCanvas;
