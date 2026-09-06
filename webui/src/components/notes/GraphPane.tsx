/**
 * Knowledge graph over the notes: wikilinks and tags as an explorable map.
 *
 * Reuses the Dev module's force-layout and SVG canvas rather than pulling a
 * graph library: same look, same interactions (pan/zoom/hover), zero new
 * dependencies. Note ids are hex, so canvas labels (last "/" segment of the
 * id) are carried by composing ids as "<id>/<title>".
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Hash, Loader2, RefreshCw, Waypoints } from "lucide-react";
import { useTranslation } from "react-i18next";

import { MetagraphCanvas } from "@/components/dev/MetagraphCanvas";
import type { PlacedEdge, PlacedNode } from "@/components/dev/MetagraphCanvas";
import { layoutGraph } from "@/lib/force-layout";
import {
  getNotesGraph,
  type NotesGraph,
} from "@/lib/notes-api";
import { cn } from "@/lib/utils";

const COLORS: Record<string, string> = {
  note: "#3b82f6",
  pinned: "#f59e0b",
  tag: "#10b981",
};

function colorOf(kind: string): string {
  return COLORS[kind] ?? COLORS.note;
}

/** Titles may contain "/", which would truncate the canvas label. */
function labelSafe(title: string): string {
  return (title || "Untitled").replaceAll("/", "-");
}

export function GraphPane({
  token,
  onOpenNote,
  onOpenTag,
}: {
  token: string;
  onOpenNote: (id: string) => void;
  onOpenTag: (tag: string) => void;
}) {
  const { t } = useTranslation();
  const [graph, setGraph] = useState<NotesGraph | null>(null);
  const [loading, setLoading] = useState(false);
  const [showTags, setShowTags] = useState(true);
  const [hovered, setHovered] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [aspect, setAspect] = useState(16 / 9);

  const refresh = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    try {
      setGraph(await getNotesGraph(token));
    } catch {
      setGraph({ nodes: [], edges: [] });
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const placed = useMemo(() => {
    if (!graph) {
      return { nodes: [] as PlacedNode[], edges: [] as PlacedEdge[], width: 0, height: 0 };
    }
    const visible = graph.nodes.filter(
      (node) => showTags || node.kind !== "tag",
    );
    // "<raw id>|<title>" would still show the raw id; the canvas keeps the last
    // "/" segment, so the readable part must come after a slash.
    const canvasId = new Map<string, string>();
    for (const node of visible) {
      canvasId.set(node.id, `${node.id}/${labelSafe(node.title)}`);
    }
    const edges = graph.edges
      .filter((edge) => canvasId.has(edge.source) && canvasId.has(edge.target))
      .map((edge) => ({
        source: canvasId.get(edge.source)!,
        target: canvasId.get(edge.target)!,
      }));
    const degrees = new Map<string, { in: number; out: number }>();
    for (const edge of edges) {
      const from = degrees.get(edge.source) ?? { in: 0, out: 0 };
      from.out += 1;
      degrees.set(edge.source, from);
      const to = degrees.get(edge.target) ?? { in: 0, out: 0 };
      to.in += 1;
      degrees.set(edge.target, to);
    }
    const layoutNodes = visible.map((node) => ({ id: canvasId.get(node.id)! }));
    const layout = layoutGraph(layoutNodes, edges, { aspect });
    const nodes: PlacedNode[] = [];
    for (const node of visible) {
      const id = canvasId.get(node.id)!;
      const point = layout.positions.get(id);
      if (!point) continue;
      const degree = degrees.get(id) ?? { in: 0, out: 0 };
      nodes.push({
        id,
        kind: node.kind === "tag" ? "tag" : node.pinned ? "pinned" : "note",
        size: 1,
        in_degree: degree.in,
        out_degree: degree.out,
        x: point.x,
        y: point.y,
      });
    }
    const byId = new Map(nodes.map((node) => [node.id, node]));
    const placedEdges: PlacedEdge[] = [];
    for (const edge of edges) {
      const from = byId.get(edge.source);
      const to = byId.get(edge.target);
      if (!from || !to) continue;
      placedEdges.push({
        source: edge.source,
        target: edge.target,
        from: { x: from.x, y: from.y },
        to: { x: to.x, y: to.y },
      });
    }
    return { nodes, edges: placedEdges, width: layout.width, height: layout.height };
  }, [aspect, graph, showTags]);

  const openNode = useCallback(
    (node: PlacedNode) => {
      const rawId = node.id.slice(0, node.id.indexOf("/"));
      if (rawId.startsWith("tag:")) {
        onOpenTag(rawId.slice("tag:".length));
      } else {
        onOpenNote(rawId);
      }
    },
    [onOpenNote, onOpenTag],
  );

  if (loading && !graph) {
    return (
      <div className="flex flex-1 items-center justify-center text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 items-center gap-2 border-b border-border/60 px-4 py-1.5">
        <Waypoints className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />
        <span className="text-[12.5px] font-medium">
          {t("notes.graph.title", { defaultValue: "Knowledge graph" })}
        </span>
        <span className="text-[11px] text-muted-foreground">
          {t("notes.graph.counts", {
            notes: graph?.nodes.filter((n) => n.kind === "note").length ?? 0,
            links:
              graph?.edges.filter((e) => e.kind === "link").length ?? 0,
            defaultValue: "{{notes}} note(s), {{links}} lien(s)",
          })}
        </span>
        <div className="ml-auto flex items-center gap-1">
          <button
            type="button"
            onClick={() => setShowTags((current) => !current)}
            className={cn(
              "flex h-7 items-center gap-1 rounded-md px-2 text-[11.5px] font-medium transition-colors",
              showTags
                ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
                : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
            )}
            aria-pressed={showTags}
          >
            <Hash className="h-3 w-3" aria-hidden />
            {t("notes.graph.tags", { defaultValue: "Tags" })}
          </button>
          <button
            type="button"
            onClick={() => void refresh()}
            className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
            aria-label={t("notes.graph.refresh", { defaultValue: "Refresh" })}
            title={t("notes.graph.refresh", { defaultValue: "Refresh" })}
          >
            <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} aria-hidden />
          </button>
        </div>
      </div>

      <div className="flex min-h-0 flex-1">
        <MetagraphCanvas
          nodes={placed.nodes}
          edges={placed.edges}
          width={placed.width}
          height={placed.height}
          hovered={hovered}
          selected={selected}
          colorOf={colorOf}
          emptyLabel={t("notes.graph.empty", {
            defaultValue:
              "No connections yet. Link notes with [[wikilinks]] or tags to see the graph.",
          })}
          zoomInLabel={t("notes.graph.zoomIn", { defaultValue: "Zoom in" })}
          zoomOutLabel={t("notes.graph.zoomOut", { defaultValue: "Zoom out" })}
          fitLabel={t("notes.graph.fit", { defaultValue: "Fit to view" })}
          onHover={setHovered}
          onSelect={setSelected}
          onOpen={openNode}
          onAspect={setAspect}
        />
      </div>
    </div>
  );
}
