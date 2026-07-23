import { useCallback, useEffect, useMemo, useState } from "react";
import { Loader2, RefreshCw, Search, Waypoints } from "lucide-react";
import { useTranslation } from "react-i18next";

import { fetchMetagraph } from "@/lib/api";
import type { MetagraphNode, MetagraphPayload } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useClient } from "@/providers/ClientProvider";

// Data-viz palette: node colors encode the file nature (front/back/sql/...).
const KIND_COLORS: Record<string, string> = {
  front: "#3b82f6",
  back: "#10b981",
  sql: "#f59e0b",
  config: "#8b5cf6",
  test: "#ec4899",
  docs: "#9ca3af",
  asset: "#64748b",
  other: "#6b7280",
};

const KIND_ORDER = ["front", "back", "sql", "config", "test", "docs", "asset", "other"];

const COLUMN_WIDTH = 280;
const ROW_HEIGHT = 24;
const PADDING_X = 24;
const PADDING_Y = 44;

type LaidNode = MetagraphNode & { x: number; y: number };

export function DevMetagraph({
  sessionKey,
  onOpenFile,
  onRunAction,
}: {
  sessionKey: string | null;
  onOpenFile?: (absolutePath: string) => void;
  onRunAction?: (text: string) => void;
}) {
  const { token } = useClient();
  const { t } = useTranslation();
  const tx = useCallback(
    (key: string, fallback: string) => t(key, { defaultValue: fallback }),
    [t],
  );

  const treeKey = sessionKey ?? "websocket:webui-dev";
  const [payload, setPayload] = useState<MetagraphPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hidden, setHidden] = useState<Set<string>>(() => new Set());
  const [query, setQuery] = useState("");
  const [connectedOnly, setConnectedOnly] = useState(false);
  const [hovered, setHovered] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const data = await fetchMetagraph(token, treeKey);
      setPayload(data);
      setConnectedOnly(data.nodes.length > 200);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [token, treeKey]);

  useEffect(() => {
    void load();
  }, [load]);

  const { nodes, edges, width, height } = useMemo(() => {
    if (!payload) return { nodes: [] as LaidNode[], edges: [], width: 0, height: 0 };
    const q = query.trim().toLowerCase();
    const visible = payload.nodes.filter((node) => {
      if (hidden.has(node.kind)) return false;
      if (connectedOnly && node.in_degree === 0 && node.out_degree === 0) return false;
      if (q && !node.id.toLowerCase().includes(q)) return false;
      return true;
    });
    const byId = new Map(visible.map((node) => [node.id, node]));
    const kinds = KIND_ORDER.filter((kind) => visible.some((node) => node.kind === kind));
    const laid: LaidNode[] = [];
    const positions = new Map<string, { x: number; y: number }>();
    kinds.forEach((kind, col) => {
      const inKind = visible
        .filter((node) => node.kind === kind)
        .sort((a, b) => b.in_degree + b.out_degree - (a.in_degree + a.out_degree));
      inKind.forEach((node, row) => {
        const x = PADDING_X + col * COLUMN_WIDTH;
        const y = PADDING_Y + row * ROW_HEIGHT;
        positions.set(node.id, { x, y });
        laid.push({ ...node, x, y });
      });
    });
    const visibleEdges = payload.edges
      .filter((edge) => byId.has(edge.source) && byId.has(edge.target))
      .map((edge) => ({
        ...edge,
        from: positions.get(edge.source)!,
        to: positions.get(edge.target)!,
      }));
    const maxRows = Math.max(1, ...kinds.map(
      (kind) => visible.filter((node) => node.kind === kind).length,
    ));
    return {
      nodes: laid,
      edges: visibleEdges,
      width: PADDING_X * 2 + Math.max(1, kinds.length) * COLUMN_WIDTH,
      height: PADDING_Y * 2 + maxRows * ROW_HEIGHT,
    };
  }, [payload, hidden, query, connectedOnly]);

  const toggleKind = useCallback((kind: string) => {
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(kind)) next.delete(kind);
      else next.add(kind);
      return next;
    });
  }, []);

  const openNode = useCallback(
    (node: MetagraphNode) => {
      if (!payload || !onOpenFile) return;
      const base = payload.project_path.replace(/\/+$/, "");
      onOpenFile(`${base}/${node.id}`);
    },
    [payload, onOpenFile],
  );

  if (loading && !payload) {
    return (
      <div className="flex flex-1 items-center justify-center gap-2 text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
        <span className="text-[13px]">{tx("dev.graph.loading", "Building the metagraph…")}</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-2 px-6 text-center">
        <p className="text-[13px] text-destructive">{error}</p>
        <button
          type="button"
          onClick={() => void load()}
          className="rounded-md border border-border/60 px-2.5 py-1 text-[12px] text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          {tx("dev.graph.retry", "Retry")}
        </button>
      </div>
    );
  }

  if (!payload) return null;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2 border-b border-border/50 px-3 py-2">
        <div className="relative">
          <Search
            className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground/70"
            aria-hidden
          />
          <input
            type="text"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={tx("dev.graph.search", "Filter files…")}
            className="h-7 w-44 rounded-md border border-border/60 bg-background pl-7 pr-2 text-[12px] text-foreground outline-none placeholder:text-muted-foreground/70 focus:border-foreground/50"
          />
        </div>
        <div className="flex flex-wrap items-center gap-1">
          {KIND_ORDER.filter((kind) => (payload.kinds[kind] ?? 0) > 0).map((kind) => (
            <button
              key={kind}
              type="button"
              onClick={() => toggleKind(kind)}
              className={cn(
                "flex items-center gap-1.5 rounded-md border px-1.5 py-0.5 text-[11px] font-medium transition-colors",
                hidden.has(kind)
                  ? "border-border/40 text-muted-foreground/50"
                  : "border-border/60 text-foreground",
              )}
            >
              <span
                className="h-2 w-2 rounded-full"
                style={{
                  backgroundColor: hidden.has(kind) ? "#d1d5db" : KIND_COLORS[kind],
                }}
                aria-hidden
              />
              {kind}
              <span className="text-muted-foreground">{payload.kinds[kind]}</span>
            </button>
          ))}
        </div>
        <label className="flex items-center gap-1.5 text-[11.5px] text-muted-foreground">
          <input
            type="checkbox"
            checked={connectedOnly}
            onChange={(event) => setConnectedOnly(event.target.checked)}
            className="h-3.5 w-3.5 accent-foreground"
          />
          {tx("dev.graph.connectedOnly", "Connected only")}
        </label>
        <div className="ml-auto flex items-center gap-1.5">
          {payload.has_metadata ? (
            <span className="text-[11px] text-muted-foreground">
              {tx("dev.graph.annotated", "annotated")}: {payload.annotated}
            </span>
          ) : null}
          {onRunAction ? (
            <button
              type="button"
              onClick={() =>
                onRunAction(payload.has_metadata ? "/atlas refresh" : "/atlas init")
              }
              className="flex items-center gap-1.5 rounded-md border border-border/60 px-2 py-1 text-[11.5px] font-medium text-muted-foreground transition-colors hover:bg-foreground hover:text-background"
            >
              <Waypoints className="h-3.5 w-3.5" aria-hidden />
              {payload.has_metadata
                ? tx("dev.graph.refreshMetadata", "Refresh .metadata")
                : tx("dev.graph.generateMetadata", "Generate .metadata")}
            </button>
          ) : null}
          <button
            type="button"
            onClick={() => void load()}
            aria-label={tx("dev.graph.reload", "Reload graph")}
            title={tx("dev.graph.reload", "Reload graph")}
            className="rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} aria-hidden />
          </button>
        </div>
      </div>

      {!payload.has_metadata ? (
        <div className="border-b border-border/40 bg-muted/20 px-3 py-1.5 text-[11.5px] text-muted-foreground">
          {tx(
            "dev.graph.noMetadata",
            "No .metadata/index.json yet — the graph shows parsed imports only. Generate it so the agent annotates every file's role.",
          )}
        </div>
      ) : null}

      {/* Graph canvas */}
      <div className="min-h-0 flex-1 overflow-auto bg-background">
        {nodes.length === 0 ? (
          <div className="flex h-full items-center justify-center text-[13px] text-muted-foreground">
            {tx("dev.graph.empty", "No files match the current filters.")}
          </div>
        ) : (
          <svg width={width} height={height} className="block">
            {edges.map((edge, index) => {
              const active =
                hovered != null && (edge.source === hovered || edge.target === hovered);
              const x1 = edge.from.x + 8;
              const y1 = edge.from.y;
              const x2 = edge.to.x - 8;
              const y2 = edge.to.y;
              const dx = Math.max(40, Math.abs(x2 - x1) / 2);
              return (
                <path
                  key={index}
                  d={`M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`}
                  fill="none"
                  stroke={active ? "#111827" : "#d1d5db"}
                  strokeWidth={active ? 1.6 : 0.8}
                  opacity={hovered == null ? 0.55 : active ? 0.95 : 0.12}
                />
              );
            })}
            {nodes.map((node) => {
              const dimmed =
                hovered != null &&
                hovered !== node.id &&
                !edges.some(
                  (edge) =>
                    (edge.source === hovered && edge.target === node.id) ||
                    (edge.target === hovered && edge.source === node.id),
                );
              const name = node.id.split("/").pop() ?? node.id;
              return (
                <g
                  key={node.id}
                  transform={`translate(${node.x}, ${node.y})`}
                  className="cursor-pointer"
                  opacity={dimmed ? 0.25 : 1}
                  onMouseEnter={() => setHovered(node.id)}
                  onMouseLeave={() => setHovered(null)}
                  onClick={() => openNode(node)}
                >
                  <title>
                    {node.id}
                    {node.role ? `\n${node.role}` : ""}
                    {`\n→ ${node.out_degree} · ← ${node.in_degree}`}
                  </title>
                  <circle
                    r={node.role ? 6 : 4.5}
                    fill={KIND_COLORS[node.kind] ?? KIND_COLORS.other}
                    stroke={node.role ? "#111827" : "none"}
                    strokeWidth={node.role ? 1 : 0}
                  />
                  <text
                    x={10}
                    y={4}
                    className="select-none"
                    fontSize={11}
                    fill="currentColor"
                  >
                    {name.length > 30 ? `${name.slice(0, 29)}…` : name}
                  </text>
                </g>
              );
            })}
          </svg>
        )}
      </div>

      {payload.truncated ? (
        <div className="border-t border-border/40 px-3 py-1 text-[11px] text-muted-foreground">
          {tx("dev.graph.truncated", "Large project: only the first files are shown.")}
        </div>
      ) : null}
    </div>
  );
}

export default DevMetagraph;
