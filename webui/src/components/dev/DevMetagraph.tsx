// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Loader2, RefreshCw, Search, Waypoints } from "lucide-react";
import { useTranslation } from "react-i18next";

import { MetagraphCanvas } from "@/components/dev/MetagraphCanvas";
import type { PlacedEdge, PlacedNode } from "@/components/dev/MetagraphCanvas";
import { fetchMetagraph } from "@/lib/api";
import { impactDependents, impactIds } from "@/lib/metagraph-impact";
import { filtersActive, placeMetagraph } from "@/lib/metagraph-place";
import type {
  MetagraphDiff,
  MetagraphEdge,
  MetagraphNode,
  MetagraphPayload,
} from "@/lib/types";
import { cn } from "@/lib/utils";
import { useClient } from "@/providers/ClientProvider";

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
const POLL_MS = 20_000;
/** Switch to package clusters once - a flat file soup is unreadable past this. */
const AUTO_PACKAGES_AT = 400;

function colorOf(kind: string): string {
  return KIND_COLORS[kind] ?? KIND_COLORS.other;
}

function applyDiff(payload: MetagraphPayload, diff: MetagraphDiff): MetagraphPayload {
  const byId = new Map(payload.nodes.map((node) => [node.id, node]));
  for (const id of diff.removed_nodes ?? []) byId.delete(id);
  for (const node of diff.updated_nodes ?? []) byId.set(node.id, node);
  for (const node of diff.added_nodes ?? []) byId.set(node.id, node);

  const edgeKey = (edge: MetagraphEdge) => `${edge.source}\0${edge.target}`;
  const edges = new Map(payload.edges.map((edge) => [edgeKey(edge), edge]));
  for (const edge of diff.removed_edges ?? []) edges.delete(edgeKey(edge));
  for (const edge of diff.added_edges ?? []) edges.set(edgeKey(edge), edge);

  const kinds: Record<string, number> = {};
  for (const node of byId.values()) {
    kinds[node.kind] = (kinds[node.kind] ?? 0) + 1;
  }

  return {
    ...payload,
    generation: diff.generation ?? (payload.generation ?? 0) + 1,
    nodes: [...byId.values()],
    edges: [...edges.values()],
    kinds,
    positions: diff.positions ?? payload.positions,
    layout_width: diff.layout_width ?? payload.layout_width,
    layout_height: diff.layout_height ?? payload.layout_height,
  };
}

function shortestPath(
  payload: MetagraphPayload,
  from: string,
  to: string,
): string[] | null {
  if (from === to) return [from];
  const adj = new Map<string, string[]>();
  for (const edge of payload.edges) {
    const list = adj.get(edge.source) ?? [];
    list.push(edge.target);
    adj.set(edge.source, list);
  }
  const prev = new Map<string, string | null>([[from, null]]);
  const queue = [from];
  for (let i = 0; i < queue.length; i += 1) {
    const current = queue[i]!;
    if (current === to) break;
    for (const next of adj.get(current) ?? []) {
      if (prev.has(next)) continue;
      prev.set(next, current);
      queue.push(next);
    }
  }
  if (!prev.has(to)) return null;
  const path = [to];
  let cur: string | null = to;
  while (cur && cur !== from) {
    cur = prev.get(cur) ?? null;
    if (cur) path.push(cur);
  }
  path.reverse();
  return path[0] === from ? path : null;
}

export function DevMetagraph({
  sessionKey,
  onOpenFile,
  onRunAction,
}: {
  sessionKey: string | null;
  onOpenFile?: (absolutePath: string) => void;
  onRunAction?: (text: string) => void;
}) {
  const { token, client } = useClient();
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
  const [selected, setSelected] = useState<string | null>(null);
  const [aspect, setAspect] = useState(16 / 9);
  const [viewMode, setViewMode] = useState<"files" | "packages">("files");
  const [highlightMode, setHighlightMode] = useState<"none" | "impact" | "path">("none");
  const [pathTarget, setPathTarget] = useState<string | null>(null);
  const [packageFocus, setPackageFocus] = useState<string | null>(null);
  const payloadRef = useRef<MetagraphPayload | null>(null);
  const aspectRef = useRef(aspect);
  const seededFiltersRef = useRef(false);
  const autoPackagesRef = useRef(false);
  payloadRef.current = payload;
  aspectRef.current = aspect;

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const data = await fetchMetagraph(token, treeKey, {
        view: viewMode,
        aspect: aspectRef.current,
      });
      setPayload(data);
      // Seed once: polling / aspect must not fight the user's checkbox.
      if (!seededFiltersRef.current) {
        seededFiltersRef.current = true;
        setConnectedOnly(data.nodes.length > 200);
      }
      // Flat file view of a large repo is noise - open packages once, then
      // leave the Files toggle alone if the user switches back.
      if (
        !autoPackagesRef.current &&
        viewMode === "files" &&
        data.nodes.length >= AUTO_PACKAGES_AT
      ) {
        autoPackagesRef.current = true;
        setViewMode("packages");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [token, treeKey, viewMode]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!client) return;
    const unsubscribe = client.onMetagraphUpdate((update) => {
      const current = payloadRef.current;
      if (
        current?.project_path &&
        update.projectPath &&
        update.projectPath !== current.project_path
      ) {
        return;
      }
      if (update.view && update.view !== viewMode) return;

      const currentGen = current?.generation ?? 0;
      if (
        update.diff &&
        current &&
        update.generation > 0 &&
        update.generation <= currentGen + 1
      ) {
        setPayload(applyDiff(current, update.diff));
        return;
      }
      void load();
    });
    return unsubscribe;
  }, [client, load, viewMode]);

  useEffect(() => {
    if (!token) return;
    const id = window.setInterval(() => {
      void load();
    }, POLL_MS);
    return () => window.clearInterval(id);
  }, [token, load]);

  useEffect(() => {
    setHighlightMode("none");
    setPathTarget(null);
    setPackageFocus(null);
    setSelected(null);
  }, [viewMode]);

  const highlight = useMemo(() => {
    if (!payload || !selected || highlightMode === "none") return null;
    if (highlightMode === "impact") return impactIds(payload, selected);
    if (highlightMode === "path" && pathTarget) {
      const path = shortestPath(payload, selected, pathTarget);
      return path ? new Set(path) : new Set([selected, pathTarget]);
    }
    return null;
  }, [payload, selected, highlightMode, pathTarget]);

  const impactList = useMemo(() => {
    if (!payload || !selected || highlightMode !== "impact") return [];
    return impactDependents(payload, selected);
  }, [payload, selected, highlightMode]);

  const { nodes, edges, width, height } = useMemo(() => {
    if (!payload) {
      return { nodes: [] as PlacedNode[], edges: [] as PlacedEdge[], width: 0, height: 0 };
    }
    const q = query.trim().toLowerCase();
    let visible = payload.nodes.filter((node) => {
      if (hidden.has(node.kind)) return false;
      if (connectedOnly && node.in_degree === 0 && node.out_degree === 0) return false;
      if (q && !node.id.toLowerCase().includes(q)) return false;
      return true;
    });

    if (viewMode === "files" && packageFocus) {
      const prefix = packageFocus === "(root)" ? null : `${packageFocus}/`;
      visible = visible.filter((node) =>
        packageFocus === "(root)"
          ? !node.id.includes("/")
          : node.id === packageFocus || (prefix != null && node.id.startsWith(prefix)),
      );
    }

    const byId = new Set(visible.map((node) => node.id));
    const links = payload.edges.filter(
      (edge) => byId.has(edge.source) && byId.has(edge.target),
    );

    const filtered = filtersActive({
      hidden,
      query,
      connectedOnly,
      packageFocus,
    });

    return placeMetagraph(visible, links, {
      aspect,
      filtered,
      serverPositions: payload.positions,
      layoutWidth: payload.layout_width,
      layoutHeight: payload.layout_height,
    });
  }, [payload, hidden, query, connectedOnly, aspect, viewMode, packageFocus]);

  const detail = useMemo(() => {
    const id = selected ?? hovered;
    if (!id || !payload) return null;
    const node = payload.nodes.find((candidate) => candidate.id === id);
    if (!node) return null;
    return {
      node,
      imports: payload.edges.filter((e) => e.source === id).map((e) => e.target),
      importedBy: payload.edges.filter((e) => e.target === id).map((e) => e.source),
    };
  }, [selected, hovered, payload]);

  const toggleKind = useCallback((kind: string) => {
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(kind)) next.delete(kind);
      else next.add(kind);
      return next;
    });
  }, []);

  /** Double-click a chip: show only that kind (the usual "filter" expectation). */
  const soloKind = useCallback(
    (kind: string) => {
      if (!payload) return;
      const present = KIND_ORDER.filter((candidate) => (payload.kinds[candidate] ?? 0) > 0);
      setHidden(new Set(present.filter((candidate) => candidate !== kind)));
    },
    [payload],
  );

  const visibleKindCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const node of nodes) {
      counts[node.kind] = (counts[node.kind] ?? 0) + 1;
    }
    return counts;
  }, [nodes]);

  const openNode = useCallback(
    (node: MetagraphNode) => {
      if (!payload || !onOpenFile) return;
      if (viewMode === "packages") {
        setViewMode("files");
        setPackageFocus(node.id);
        setSelected(null);
        return;
      }
      const base = payload.project_path.replace(/\/+$/, "");
      onOpenFile(`${base}/${node.id}`);
    },
    [payload, onOpenFile, viewMode],
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
            placeholder={
              viewMode === "packages"
                ? tx("dev.graph.searchPackages", "Filter packages…")
                : tx("dev.graph.search", "Filter files…")
            }
            className="h-7 w-44 rounded-md border border-border/60 bg-background pl-7 pr-2 text-[12px] text-foreground outline-none placeholder:text-muted-foreground/70 focus:border-foreground/50"
          />
        </div>

        <div className="flex items-center rounded-md border border-border/60 p-0.5 text-[11px]">
          {(
            [
              ["files", "dev.graph.viewFiles", "Files"],
              ["packages", "dev.graph.viewPackages", "Packages"],
            ] as const
          ).map(([mode, key, fallback]) => (
            <button
              key={mode}
              type="button"
              onClick={() => setViewMode(mode)}
              className={cn(
                "rounded px-2 py-0.5 font-medium transition-colors",
                viewMode === mode
                  ? "bg-foreground text-background"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {tx(key, fallback)}
            </button>
          ))}
        </div>

        {packageFocus ? (
          <button
            type="button"
            onClick={() => setPackageFocus(null)}
            className="rounded-md border border-border/60 px-2 py-0.5 text-[11px] text-muted-foreground hover:text-foreground"
          >
            {tx("dev.graph.clearPackage", "All files")} · {packageFocus}
          </button>
        ) : null}

        <div className="flex flex-wrap items-center gap-1">
          {KIND_ORDER.filter((kind) => (payload.kinds[kind] ?? 0) > 0).map((kind) => {
            const total = payload.kinds[kind] ?? 0;
            const shown = hidden.has(kind) ? 0 : (visibleKindCounts[kind] ?? 0);
            return (
              <button
                key={kind}
                type="button"
                onClick={() => toggleKind(kind)}
                onDoubleClick={(event) => {
                  event.preventDefault();
                  soloKind(kind);
                }}
                title={tx(
                  "dev.graph.kindChipHint",
                  "Click to hide/show · double-click to show only this kind",
                )}
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
                <span className="text-muted-foreground">
                  {shown === total ? total : `${shown}/${total}`}
                </span>
              </button>
            );
          })}
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
          {payload.annotated_stale ? (
            <span
              className="text-[11px] text-amber-500"
              title={tx(
                "dev.graph.staleHint",
                "These files changed after their role was recorded, so the role may describe code that is gone. The agent refreshes them as it works; /atlas refresh does the backlog.",
              )}
            >
              {tx("dev.graph.stale", "stale")}: {payload.annotated_stale}
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
                ? tx("dev.graph.refreshMetadata", "Refresh .navin/metadata")
                : tx("dev.graph.generateMetadata", "Generate .navin/metadata")}
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
            "No .navin/metadata/index.json yet - the graph shows parsed imports and roles read from docstrings. Generate it once and the agent keeps it current as it works.",
          )}
        </div>
      ) : null}

      <div className="flex min-h-0 flex-1">
        <MetagraphCanvas
          nodes={nodes}
          edges={edges}
          width={width}
          height={height}
          hovered={hovered}
          selected={selected}
          colorOf={colorOf}
          emptyLabel={tx("dev.graph.empty", "No files match the current filters.")}
          zoomInLabel={tx("dev.graph.zoomIn", "Zoom in")}
          zoomOutLabel={tx("dev.graph.zoomOut", "Zoom out")}
          fitLabel={tx("dev.graph.fit", "Fit to view")}
          onHover={setHovered}
          onSelect={setSelected}
          onOpen={openNode}
          onAspect={setAspect}
          highlight={highlight}
        />
        <aside className="flex w-64 shrink-0 flex-col gap-2.5 overflow-y-auto border-l border-border/50 bg-muted/10 px-3 py-2.5">
          {detail ? (
            <>
              <div className="flex items-start gap-2">
                <span
                  className="mt-1 h-2.5 w-2.5 shrink-0 rounded-full"
                  style={{ backgroundColor: KIND_COLORS[detail.node.kind] ?? KIND_COLORS.other }}
                  aria-hidden
                />
                <div className="min-w-0">
                  <p className="break-all text-[12.5px] font-medium leading-snug text-foreground">
                    {detail.node.id.split("/").pop()}
                  </p>
                  {detail.node.id.includes("/") ? (
                    <p className="break-all text-[11px] leading-snug text-muted-foreground">
                      {detail.node.id.slice(0, detail.node.id.lastIndexOf("/"))}
                    </p>
                  ) : null}
                </div>
              </div>

              {detail.node.role ? (
                <div>
                  <p className="text-[12px] leading-snug text-foreground">{detail.node.role}</p>
                  <p className="mt-0.5 text-[10.5px] text-muted-foreground">
                    {detail.node.role_source === "manual"
                      ? tx("dev.graph.roleManual", "from .navin/metadata")
                      : tx("dev.graph.roleAuto", "read from the docstring")}
                    {detail.node.role_stale
                      ? ` · ${tx("dev.graph.staleNode", "recorded before the file changed")}`
                      : ""}
                  </p>
                </div>
              ) : (
                <p className="text-[11.5px] italic text-muted-foreground">
                  {tx("dev.graph.noRole", "No role recorded yet.")}
                </p>
              )}

              <dl className="grid grid-cols-2 gap-x-2 gap-y-1 text-[11px]">
                <dt className="text-muted-foreground">{tx("dev.graph.kind", "Kind")}</dt>
                <dd className="text-foreground">{detail.node.kind}</dd>
                {detail.node.symbols != null ? (
                  <>
                    <dt className="text-muted-foreground">{tx("dev.graph.symbols", "Symbols")}</dt>
                    <dd className="text-foreground">{detail.node.symbols}</dd>
                  </>
                ) : null}
                <dt className="text-muted-foreground">{tx("dev.graph.lines", "Lines")}</dt>
                <dd className="text-foreground">{detail.node.size}</dd>
              </dl>

              {viewMode === "files" && selected ? (
                <div className="flex flex-col gap-1">
                  <button
                    type="button"
                    onClick={() =>
                      setHighlightMode((mode) => (mode === "impact" ? "none" : "impact"))
                    }
                    className={cn(
                      "rounded-md border px-2 py-1 text-[11.5px] font-medium transition-colors",
                      highlightMode === "impact"
                        ? "border-foreground bg-foreground text-background"
                        : "border-border/60 text-muted-foreground hover:bg-muted hover:text-foreground",
                    )}
                  >
                    {tx("dev.graph.showImpact", "Show impact")}
                  </button>
                  {highlightMode === "impact" ? (
                    <div className="rounded-md border border-border/50 bg-muted/20 px-2 py-1.5">
                      <p className="mb-1 text-[10.5px] font-medium uppercase tracking-wide text-muted-foreground">
                        {tx("dev.graph.whoBreaks", "Who breaks")} · {impactList.length}
                      </p>
                      {impactList.length === 0 ? (
                        <p className="text-[11px] text-muted-foreground">
                          {tx(
                            "dev.graph.noImpact",
                            "Nothing else depends on this file.",
                          )}
                        </p>
                      ) : (
                        <ul className="max-h-40 space-y-0.5 overflow-y-auto">
                          {impactList.slice(0, 40).map((id) => (
                            <li key={id}>
                              <button
                                type="button"
                                onClick={() => setSelected(id)}
                                className="block w-full truncate text-left text-[11px] text-muted-foreground hover:text-foreground hover:underline"
                                title={id}
                              >
                                {id}
                              </button>
                            </li>
                          ))}
                          {impactList.length > 40 ? (
                            <li className="text-[10.5px] text-muted-foreground/70">
                              {tx("dev.graph.andMore", "+{{count}} more").replace(
                                "{{count}}",
                                String(impactList.length - 40),
                              )}
                            </li>
                          ) : null}
                        </ul>
                      )}
                    </div>
                  ) : null}
                  <button
                    type="button"
                    onClick={() => {
                      if (highlightMode === "path") {
                        setHighlightMode("none");
                        setPathTarget(null);
                        return;
                      }
                      setHighlightMode("path");
                      setPathTarget(null);
                    }}
                    className={cn(
                      "rounded-md border px-2 py-1 text-[11.5px] font-medium transition-colors",
                      highlightMode === "path"
                        ? "border-foreground bg-foreground text-background"
                        : "border-border/60 text-muted-foreground hover:bg-muted hover:text-foreground",
                    )}
                  >
                    {tx("dev.graph.pathTo", "Path to…")}
                  </button>
                  {highlightMode === "path" ? (
                    <p className="text-[10.5px] text-muted-foreground">
                      {pathTarget
                        ? tx("dev.graph.pathActive", "Path highlighted on the graph.")
                        : tx(
                            "dev.graph.pathPick",
                            "Click another file to highlight the shortest dependency path.",
                          )}
                    </p>
                  ) : null}
                </div>
              ) : null}

              {detail.imports.length + detail.importedBy.length > 0 ? (
                <p className="text-[10.5px] leading-snug text-muted-foreground/80">
                  {tx(
                    "dev.graph.edgeLegend",
                    "On the graph: solid lines leave this file, dashed lines arrive.",
                  )}
                </p>
              ) : null}

              {(
                [
                  ["dev.graph.dependsOn", "Imports", detail.imports],
                  ["dev.graph.usedBy", "Imported by", detail.importedBy],
                ] as const
              ).map(([key, fallback, list]) =>
                list.length > 0 ? (
                  <div key={key}>
                    <p className="mb-1 text-[10.5px] font-medium uppercase tracking-wide text-muted-foreground">
                      {tx(key, fallback)} · {list.length}
                    </p>
                    <ul className="space-y-0.5">
                      {list.slice(0, 8).map((id) => (
                        <li key={id}>
                          <button
                            type="button"
                            onClick={() => {
                              if (highlightMode === "path" && selected && selected !== id) {
                                setPathTarget(id);
                              }
                              setSelected(id);
                            }}
                            className="block w-full truncate text-left text-[11px] text-muted-foreground hover:text-foreground hover:underline"
                            title={id}
                          >
                            {id.split("/").pop()}
                          </button>
                        </li>
                      ))}
                      {list.length > 8 ? (
                        <li className="text-[10.5px] text-muted-foreground/70">
                          {tx("dev.graph.andMore", "+{{count}} more").replace(
                            "{{count}}",
                            String(list.length - 8),
                          )}
                        </li>
                      ) : null}
                    </ul>
                  </div>
                ) : null,
              )}

              {onOpenFile || viewMode === "packages" ? (
                <button
                  type="button"
                  onClick={() => openNode(detail.node)}
                  className="rounded-md border border-border/60 px-2 py-1 text-[11.5px] font-medium text-muted-foreground transition-colors hover:bg-foreground hover:text-background"
                >
                  {viewMode === "packages"
                    ? tx("dev.graph.openPackage", "Open package files")
                    : tx("dev.graph.openFile", "Open file")}
                </button>
              ) : null}
            </>
          ) : (
            <p className="text-[11.5px] leading-snug text-muted-foreground">
              {tx(
                "dev.graph.pickHint",
                "Point at a file for its role and its dependencies. Click to keep it, double-click to open it.",
              )}
            </p>
          )}
        </aside>
      </div>

      {payload.truncated ? (
        <div className="border-t border-border/40 px-3 py-1 text-[11px] text-muted-foreground">
          {tx("dev.graph.truncated", "Large project: only the first files are shown.")}
        </div>
      ) : null}

      <div
        className="truncate border-t border-border/40 px-3 py-1 text-[11px] text-muted-foreground"
        title={payload.project_path}
      >
        {payload.project_path}
        {payload.discovery === "walk"
          ? ` · ${tx("dev.graph.discoveryWalk", "walked the tree, gitignored files included")}`
          : payload.discovery === "git"
            ? ` · ${tx("dev.graph.discoveryGit", "via git, .gitignore honored")}`
            : ""}
        {payload.engine === "native" ? ` · ${tx("dev.graph.engineNative", "native engine")}` : ""}
      </div>
    </div>
  );
}

export default DevMetagraph;
