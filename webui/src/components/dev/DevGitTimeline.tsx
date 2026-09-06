import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import {
  ArrowUp,
  Check,
  Copy,
  GitBranch,
  GitCommitHorizontal,
  GitMerge,
  Loader2,
  RefreshCw,
  Search,
  Tag,
  X,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { fetchGitCommitDetail, fetchGitLog } from "@/lib/api";
import { copyTextOrNotify } from "@/lib/clipboard";
import { compactRelativeTime, fmtDateTime } from "@/lib/format";
import type {
  GitCommitDetailPayload,
  GitLogCommit,
  GitLogPayload,
} from "@/lib/types";
import { cn } from "@/lib/utils";

const LANE_W = 12;
const ROW_H = 42;
const MAX_VISIBLE_LANES = 8;
const PAGE_SIZE = 200;

/** Lane palette, cycled; picked to stay readable on both themes. */
const LANE_COLORS = [
  "#3b82f6", "#a855f7", "#22c55e", "#f59e0b", "#ec4899",
  "#14b8a6", "#f97316", "#8b5cf6", "#06b6d4", "#84cc16",
];

const laneColor = (index: number) => LANE_COLORS[index % LANE_COLORS.length];

interface LaneEdge {
  column: number;
  color: number;
}

interface GraphRow {
  commit: GitLogCommit;
  column: number;
  color: number;
  /** A child commit connects into this node from above. */
  hasTop: boolean;
  /** The first parent continues below this node. */
  hasBottom: boolean;
  /** Extra child lanes curving into this node (branch tips being merged). */
  joins: LaneEdge[];
  /** Extra parent lanes curving out of this node (merge sources). */
  forks: LaneEdge[];
  /** Unrelated lanes passing straight through this row. */
  passes: LaneEdge[];
}

/**
 * Classic git-graph lane assignment: walk the topo-ordered commits, keep one
 * slot per in-flight branch line, and record for every row which lanes join,
 * fork, or pass through so the SVG cell can be drawn independently per row.
 */
function buildGraph(commits: GitLogCommit[]): { rows: GraphRow[]; laneCount: number } {
  const lanes: ({ hash: string; color: number } | null)[] = [];
  let nextColor = 0;
  let laneCount = 1;
  const rows: GraphRow[] = [];

  for (const commit of commits) {
    const before = lanes.map((lane) => (lane ? { ...lane } : null));
    const waiting: number[] = [];
    before.forEach((lane, i) => {
      if (lane && lane.hash === commit.hash) waiting.push(i);
    });

    let column: number;
    let color: number;
    if (waiting.length > 0) {
      column = waiting[0];
      color = before[column]!.color;
    } else {
      column = lanes.findIndex((lane) => lane === null);
      if (column === -1) {
        column = lanes.length;
        lanes.push(null);
      }
      color = nextColor;
      nextColor += 1;
    }

    const joins = waiting.slice(1).map((i) => ({ column: i, color: before[i]!.color }));
    for (const join of joins) lanes[join.column] = null;

    const [firstParent, ...otherParents] = commit.parents;
    lanes[column] = firstParent ? { hash: firstParent, color } : null;

    const forks: LaneEdge[] = [];
    for (const parent of otherParents) {
      const existing = lanes.findIndex((lane) => lane !== null && lane.hash === parent);
      if (existing !== -1) {
        forks.push({ column: existing, color: lanes[existing]!.color });
        continue;
      }
      let idx = lanes.findIndex((lane) => lane === null);
      if (idx === -1) {
        idx = lanes.length;
        lanes.push(null);
      }
      lanes[idx] = { hash: parent, color: nextColor };
      forks.push({ column: idx, color: nextColor });
      nextColor += 1;
    }

    const passes: LaneEdge[] = [];
    before.forEach((lane, i) => {
      if (lane && i !== column && !waiting.includes(i)) {
        passes.push({ column: i, color: lane.color });
      }
    });

    rows.push({
      commit,
      column,
      color,
      hasTop: waiting.length > 0,
      hasBottom: Boolean(firstParent),
      joins,
      forks,
      passes,
    });
    laneCount = Math.max(laneCount, lanes.length);
  }

  return { rows, laneCount };
}

function GraphCell({
  row,
  laneCount,
  isHead,
}: {
  row: GraphRow;
  laneCount: number;
  isHead: boolean;
}) {
  const visible = Math.min(laneCount, MAX_VISIBLE_LANES);
  const width = visible * LANE_W;
  const cx = (col: number) => col * LANE_W + LANE_W / 2;
  const cy = ROW_H / 2;
  const xNode = cx(row.column);
  const color = laneColor(row.color);
  const isMerge = row.commit.parents.length >= 2;

  return (
    <svg
      width={width}
      height={ROW_H}
      className="shrink-0"
      style={{ overflow: "hidden" }}
      aria-hidden
    >
      {row.passes.map((lane) => (
        <line
          key={`p${lane.column}`}
          x1={cx(lane.column)}
          y1={0}
          x2={cx(lane.column)}
          y2={ROW_H}
          stroke={laneColor(lane.color)}
          strokeWidth={1.5}
          opacity={0.85}
        />
      ))}
      {row.joins.map((lane) => (
        <path
          key={`j${lane.column}`}
          d={`M ${cx(lane.column)} 0 C ${cx(lane.column)} ${cy} ${xNode} 0 ${xNode} ${cy}`}
          fill="none"
          stroke={laneColor(lane.color)}
          strokeWidth={1.5}
          opacity={0.85}
        />
      ))}
      {row.forks.map((lane) => (
        <path
          key={`f${lane.column}`}
          d={`M ${xNode} ${cy} C ${xNode} ${ROW_H} ${cx(lane.column)} ${cy} ${cx(lane.column)} ${ROW_H}`}
          fill="none"
          stroke={laneColor(lane.color)}
          strokeWidth={1.5}
          opacity={0.85}
        />
      ))}
      {row.hasTop ? (
        <line x1={xNode} y1={0} x2={xNode} y2={cy} stroke={color} strokeWidth={1.5} />
      ) : null}
      {row.hasBottom ? (
        <line x1={xNode} y1={cy} x2={xNode} y2={ROW_H} stroke={color} strokeWidth={1.5} />
      ) : null}
      {isHead ? (
        <circle cx={xNode} cy={cy} r={6.5} fill="none" stroke={color} strokeWidth={1} opacity={0.45} />
      ) : null}
      {isMerge ? (
        <circle cx={xNode} cy={cy} r={3.5} fill="hsl(var(--background))" stroke={color} strokeWidth={2} />
      ) : (
        <circle cx={xNode} cy={cy} r={4} fill={color} />
      )}
    </svg>
  );
}

function authorHue(name: string): number {
  let h = 0;
  for (let i = 0; i < name.length; i += 1) {
    h = (h * 31 + name.charCodeAt(i)) >>> 0;
  }
  return h % 360;
}

function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return "?";
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[words.length - 1][0]).toUpperCase();
}

function AuthorAvatar({
  name,
  size = 16,
  ringed = false,
}: {
  name: string;
  size?: number;
  /** Adds a background-colored ring so stacked avatars stay distinct. */
  ringed?: boolean;
}) {
  return (
    <span
      className={cn(
        "flex shrink-0 items-center justify-center rounded-full font-semibold text-white",
        ringed && "ring-2 ring-background",
      )}
      style={{
        width: size,
        height: size,
        fontSize: Math.max(7, Math.round(size * 0.42)),
        backgroundColor: `hsl(${authorHue(name)} 55% 45%)`,
      }}
      title={name}
      aria-hidden
    >
      {initials(name)}
    </span>
  );
}

type DetailState = GitCommitDetailPayload | "loading" | "error";

export function DevGitTimeline({
  token,
  sessionKey,
  refreshSignal = 0,
}: {
  token: string;
  sessionKey: string;
  /** Bumped by the parent after a commit/push so the graph refetches. */
  refreshSignal?: number;
}) {
  const { t } = useTranslation();
  const tx = useCallback(
    (key: string, fallback: string) => t(key, { defaultValue: fallback }),
    [t],
  );
  const reducedMotion = useReducedMotion();

  const [payload, setPayload] = useState<GitLogPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [limit, setLimit] = useState(PAGE_SIZE);
  const [filter, setFilter] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [details, setDetails] = useState<Record<string, DetailState>>({});
  const [flash, setFlash] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const rowRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const refresh = useCallback(
    (silent = false) => {
      if (!silent) {
        setLoading(true);
        setError(null);
      }
      fetchGitLog(token, sessionKey, limit)
        .then((next) => {
          if (!mountedRef.current) return;
          setPayload(next);
          setError(null);
        })
        .catch((err) => {
          if (!mountedRef.current) return;
          if (!silent) setError(err instanceof Error ? err.message : String(err));
        })
        .finally(() => {
          if (mountedRef.current) setLoading(false);
        });
    },
    [limit, sessionKey, token],
  );

  useEffect(() => {
    refresh();
    const timer = window.setInterval(() => refresh(true), 30_000);
    return () => window.clearInterval(timer);
  }, [refresh, refreshSignal]);

  const commits = useMemo(() => payload?.commits ?? [], [payload]);
  const { rows, laneCount } = useMemo(() => buildGraph(commits), [commits]);

  const tipColors = useMemo(() => {
    const map = new Map<string, number>();
    for (const row of rows) map.set(row.commit.hash, row.color);
    return map;
  }, [rows]);

  const contributors = useMemo(() => {
    const map = new Map<string, number>();
    for (const commit of commits) {
      map.set(commit.author, (map.get(commit.author) ?? 0) + 1);
    }
    return [...map.entries()]
      .map(([name, count]) => ({ name, count }))
      .sort((a, b) => b.count - a.count);
  }, [commits]);

  const query = filter.trim().toLowerCase();
  const matches = useCallback(
    (commit: GitLogCommit) => {
      if (!query) return true;
      return (
        commit.subject.toLowerCase().includes(query) ||
        commit.author.toLowerCase().includes(query) ||
        commit.email.toLowerCase().includes(query) ||
        commit.short.startsWith(query) ||
        commit.refs.some((ref) => ref.toLowerCase().includes(query))
      );
    },
    [query],
  );

  const toggleExpand = useCallback(
    (hash: string) => {
      setExpanded((current) => (current === hash ? null : hash));
      setDetails((current) => {
        if (current[hash]) return current;
        fetchGitCommitDetail(token, sessionKey, hash)
          .then((detail) => {
            if (mountedRef.current) {
              setDetails((prev) => ({ ...prev, [hash]: detail }));
            }
          })
          .catch(() => {
            if (mountedRef.current) {
              setDetails((prev) => ({ ...prev, [hash]: "error" }));
            }
          });
        return { ...current, [hash]: "loading" };
      });
    },
    [sessionKey, token],
  );

  const jumpToCommit = useCallback((hash: string) => {
    const node = rowRefs.current[hash];
    if (!node) return;
    node.scrollIntoView({ behavior: "smooth", block: "center" });
    setFlash(hash);
    window.setTimeout(() => {
      if (mountedRef.current) setFlash((f) => (f === hash ? null : f));
    }, 1600);
  }, []);

  const copyHash = useCallback((hash: string) => {
    void copyTextOrNotify(hash).then((ok) => {
      if (!ok) return;
      if (!mountedRef.current) return;
      setCopied(hash);
      window.setTimeout(() => {
        if (mountedRef.current) setCopied((c) => (c === hash ? null : c));
      }, 1500);
    });
  }, []);

  const isRepo = Boolean(payload?.is_repo);
  const branches = payload?.branches ?? [];
  const localBranches = branches.filter((branch) => !branch.remote);
  const remoteBranches = branches.filter((branch) => branch.remote);
  const total = payload?.total ?? commits.length;
  const canLoadMore = isRepo && total > commits.length;
  const spring = reducedMotion
    ? { duration: 0 }
    : ({ type: "spring", duration: 0.3, bounce: 0 } as const);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="shrink-0 space-y-1.5 px-2 pb-1.5 pt-1">
        <div className="flex items-center gap-1">
          <div className="relative flex-1">
            <Search
              className="pointer-events-none absolute left-1.5 top-1/2 h-3 w-3 -translate-y-1/2 text-muted-foreground/70"
              aria-hidden
            />
            <input
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
              placeholder={tx("dev.git.historyFilter", "Filter by author, message, branch")}
              className="w-full rounded-md border border-border/60 bg-background py-1 pl-6 pr-6 text-[11px] text-foreground placeholder:text-muted-foreground/70 focus:outline-none focus:ring-1 focus:ring-ring"
            />
            {filter ? (
              <button
                type="button"
                onClick={() => setFilter("")}
                className="absolute right-1 top-1/2 -translate-y-1/2 rounded p-0.5 text-muted-foreground hover:text-foreground"
                aria-label={tx("dev.git.clearFilter", "Clear filter")}
              >
                <X className="h-3 w-3" aria-hidden />
              </button>
            ) : null}
          </div>
          <button
            type="button"
            onClick={() => refresh()}
            className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
            aria-label={tx("dev.git.refreshHistory", "Refresh history")}
          >
            {loading ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
            ) : (
              <RefreshCw className="h-3.5 w-3.5" aria-hidden />
            )}
          </button>
        </div>

        {isRepo ? (
          <div className="flex items-center justify-between gap-2">
            <span className="flex min-w-0 items-center gap-1 truncate text-[10.5px] text-muted-foreground">
              <GitCommitHorizontal className="h-3 w-3 shrink-0" aria-hidden />
              {t("dev.git.historyStats", {
                defaultValue: "{{commits}} commits · {{branches}} branches",
                commits: total,
                branches: branches.length,
              })}
            </span>
            {contributors.length > 0 ? (
              <span className="flex shrink-0 items-center -space-x-1.5">
                {contributors.slice(0, 5).map((person) => (
                  <span key={person.name} title={`${person.name} · ${person.count}`}>
                    <AuthorAvatar name={person.name} size={26} ringed />
                  </span>
                ))}
                {contributors.length > 5 ? (
                  <span className="pl-2 text-[11px] font-medium text-muted-foreground">
                    +{contributors.length - 5}
                  </span>
                ) : null}
              </span>
            ) : null}
          </div>
        ) : null}

        {isRepo && branches.length > 0 ? (
          <div className="flex gap-1 overflow-x-auto pb-0.5 [scrollbar-width:thin]">
            {[...localBranches, ...remoteBranches].map((branch) => {
              const colorIdx = tipColors.get(branch.tip);
              const isCurrent = !branch.remote && branch.name === payload?.branch;
              return (
                <button
                  key={`${branch.remote ? "r" : "l"}:${branch.name}`}
                  type="button"
                  onClick={() => jumpToCommit(branch.tip)}
                  title={`${branch.name} · ${branch.author}`}
                  className={cn(
                    "flex shrink-0 items-center gap-1 rounded-md border px-1.5 py-0.5 font-mono text-[10px] transition-colors",
                    isCurrent
                      ? "border-primary/40 bg-primary/10 text-foreground"
                      : branch.remote
                        ? "border-border/40 text-muted-foreground/80 hover:bg-muted/60"
                        : "border-border/60 text-muted-foreground hover:bg-muted/60",
                  )}
                >
                  <span
                    className="h-1.5 w-1.5 shrink-0 rounded-full"
                    style={{
                      backgroundColor:
                        colorIdx !== undefined ? laneColor(colorIdx) : "hsl(var(--muted-foreground))",
                    }}
                    aria-hidden
                  />
                  <span className="max-w-[120px] truncate">{branch.name}</span>
                </button>
              );
            })}
          </div>
        ) : null}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto border-t border-border/50 pb-2">
        {error ? (
          <p className="px-3 py-2 text-[12px] text-destructive">{error}</p>
        ) : null}

        {!error && payload && !payload.is_repo ? (
          <div className="flex flex-col items-center gap-2 px-3 py-6 text-center text-muted-foreground">
            <GitBranch className="h-6 w-6 opacity-40" aria-hidden />
            <p className="text-[12px] leading-5">
              {tx("dev.git.notRepo", "This project is not a git repository.")}
            </p>
          </div>
        ) : null}

        {!error && !payload && loading ? (
          <div className="space-y-2 px-3 py-3">
            {[0, 1, 2, 3, 4].map((i) => (
              <div key={i} className="flex items-center gap-2">
                <span className="h-2 w-2 animate-pulse rounded-full bg-muted" />
                <span
                  className="h-2.5 animate-pulse rounded bg-muted"
                  style={{ width: `${70 - i * 8}%` }}
                />
              </div>
            ))}
          </div>
        ) : null}

        {!error && payload?.is_repo && commits.length === 0 ? (
          <p className="px-3 py-2 text-[12px] text-muted-foreground">
            {tx("dev.git.noCommits", "No commits yet.")}
          </p>
        ) : null}

        {rows.map((row, index) => {
          const commit = row.commit;
          const isHead = payload?.head === commit.hash;
          const isMerge = commit.parents.length >= 2;
          const dimmed = query.length > 0 && !matches(commit);
          const isExpanded = expanded === commit.hash;
          const detail = details[commit.hash];
          const shownRefs = commit.refs.slice(0, 2);
          const extraRefs = commit.refs.length - shownRefs.length;

          return (
            <div
              key={commit.hash}
              ref={(node) => {
                rowRefs.current[commit.hash] = node;
              }}
              className={cn(
                "transition-opacity",
                dimmed ? "opacity-25" : "opacity-100",
                flash === commit.hash ? "rounded-md ring-1 ring-primary/60" : "",
              )}
            >
              <motion.button
                type="button"
                onClick={() => toggleExpand(commit.hash)}
                initial={reducedMotion || index > 24 ? false : { opacity: 0, y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ ...spring, delay: reducedMotion ? 0 : Math.min(index, 24) * 0.018 }}
                className={cn(
                  "flex w-full cursor-pointer items-stretch text-left transition-colors hover:bg-muted/50 active:scale-[0.995]",
                  isExpanded ? "bg-muted/40" : "",
                )}
                style={{ minHeight: ROW_H }}
              >
                <GraphCell row={row} laneCount={laneCount} isHead={isHead} />
                <span className="flex min-w-0 flex-1 flex-col justify-center gap-0.5 py-1 pr-2">
                  <span className="flex min-w-0 items-center gap-1">
                    {shownRefs.map((ref) => {
                      const isTag = ref.startsWith("tag: ");
                      const label = isTag ? ref.slice(5) : ref;
                      const isRemote = !isTag && /^[^/\s]+\//.test(label);
                      return (
                        <span
                          key={ref}
                          className={cn(
                            "flex max-w-[90px] shrink-0 items-center gap-0.5 rounded border px-1 py-px font-mono text-[9px] leading-3",
                            isTag
                              ? "border-amber-500/40 bg-amber-500/10 text-amber-600 dark:text-amber-400"
                              : isRemote
                                ? "border-border/50 bg-muted/60 text-muted-foreground"
                                : "bg-transparent",
                          )}
                          style={
                            !isTag && !isRemote
                              ? {
                                  borderColor: `${laneColor(row.color)}66`,
                                  color: laneColor(row.color),
                                  backgroundColor: `${laneColor(row.color)}14`,
                                }
                              : undefined
                          }
                        >
                          {isTag ? (
                            <Tag className="h-2 w-2 shrink-0" aria-hidden />
                          ) : (
                            <GitBranch className="h-2 w-2 shrink-0" aria-hidden />
                          )}
                          <span className="truncate">{label}</span>
                        </span>
                      );
                    })}
                    {extraRefs > 0 ? (
                      <span className="shrink-0 text-[9px] text-muted-foreground">+{extraRefs}</span>
                    ) : null}
                    <span className="min-w-0 truncate text-[12px] leading-4 text-foreground">
                      {commit.subject}
                    </span>
                  </span>
                  <span className="flex min-w-0 items-center gap-1.5 text-[10.5px] text-muted-foreground">
                    <AuthorAvatar name={commit.author} size={13} />
                    <span className="min-w-0 max-w-[95px] truncate">{commit.author}</span>
                    <span className="shrink-0 tabular-nums opacity-80">
                      {compactRelativeTime(commit.date)}
                    </span>
                    <span className="shrink-0 font-mono opacity-70">{commit.short}</span>
                    {isMerge ? (
                      <GitMerge
                        className="h-3 w-3 shrink-0 text-violet-500"
                        aria-label={tx("dev.git.mergeCommit", "Merge commit")}
                      />
                    ) : null}
                    {!commit.pushed ? (
                      <span
                        className="flex shrink-0 items-center text-amber-500"
                        title={tx("dev.git.notPushed", "Not on any remote yet")}
                      >
                        <ArrowUp className="h-3 w-3" aria-hidden />
                      </span>
                    ) : null}
                  </span>
                </span>
              </motion.button>

              <AnimatePresence initial={false}>
                {isExpanded ? (
                  <motion.div
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: "auto", opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                    transition={spring}
                    className="overflow-hidden"
                  >
                    <div className="mx-2 mb-1.5 rounded-md border border-border/60 bg-background/80 p-2 text-[11px]">
                      {detail === "loading" || detail === undefined ? (
                        <span className="flex items-center gap-1.5 text-muted-foreground">
                          <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
                          {tx("dev.git.loadingDetail", "Loading commit detail")}
                        </span>
                      ) : detail === "error" ? (
                        <span className="text-destructive">
                          {tx("dev.git.detailFailed", "Could not load this commit.")}
                        </span>
                      ) : (
                        <div className="space-y-1.5">
                          <p className="whitespace-pre-wrap break-words text-[11.5px] leading-4 text-foreground">
                            {detail.message}
                          </p>
                          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-muted-foreground">
                            <button
                              type="button"
                              onClick={(event) => {
                                event.stopPropagation();
                                copyHash(detail.hash);
                              }}
                              className="flex items-center gap-1 rounded bg-muted/70 px-1 py-0.5 font-mono text-[10px] hover:bg-muted"
                              title={tx("dev.git.copyHash", "Copy full hash")}
                            >
                              {copied === detail.hash ? (
                                <Check className="h-2.5 w-2.5 text-emerald-500" aria-hidden />
                              ) : (
                                <Copy className="h-2.5 w-2.5" aria-hidden />
                              )}
                              {detail.hash.slice(0, 10)}
                            </button>
                            <span className="truncate" title={detail.email}>
                              {detail.author}
                            </span>
                            <span className="tabular-nums">{fmtDateTime(detail.date)}</span>
                            {commit.parents.length > 0 ? (
                              <span className="font-mono text-[10px] opacity-80">
                                {tx("dev.git.parents", "parents")}:{" "}
                                {commit.parents.map((p) => p.slice(0, 7)).join(", ")}
                              </span>
                            ) : null}
                          </div>
                          {detail.files.length > 0 ? (
                            <div className="space-y-0.5 border-t border-border/50 pt-1.5">
                              <p className="flex items-center justify-between text-[10px] text-muted-foreground">
                                <span>
                                  {t("dev.git.filesChanged", {
                                    defaultValue: "{{n}} file(s) changed",
                                    n: detail.files.length,
                                  })}
                                </span>
                                <span className="tabular-nums">
                                  <span className="text-emerald-500">+{detail.additions}</span>{" "}
                                  <span className="text-rose-500">-{detail.deletions}</span>
                                </span>
                              </p>
                              <div className="max-h-40 space-y-px overflow-y-auto">
                                {detail.files.map((file) => (
                                  <div
                                    key={file.path}
                                    className="flex items-center gap-1.5 rounded px-1 py-0.5 hover:bg-muted/50"
                                    title={file.path}
                                  >
                                    <span className="min-w-0 flex-1 truncate font-mono text-[10px] text-foreground/90">
                                      {file.path}
                                    </span>
                                    {file.binary ? (
                                      <span className="shrink-0 text-[9px] uppercase text-muted-foreground">
                                        bin
                                      </span>
                                    ) : (
                                      <span className="shrink-0 font-mono text-[9.5px] tabular-nums">
                                        <span className="text-emerald-500">+{file.additions}</span>{" "}
                                        <span className="text-rose-500">-{file.deletions}</span>
                                      </span>
                                    )}
                                  </div>
                                ))}
                              </div>
                            </div>
                          ) : null}
                        </div>
                      )}
                    </div>
                  </motion.div>
                ) : null}
              </AnimatePresence>
            </div>
          );
        })}

        {canLoadMore ? (
          <button
            type="button"
            onClick={() => setLimit((value) => value + PAGE_SIZE)}
            className="mx-2 mt-1 flex w-[calc(100%-16px)] items-center justify-center gap-1.5 rounded-md border border-border/60 py-1.5 text-[11px] text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
          >
            {loading ? <Loader2 className="h-3 w-3 animate-spin" aria-hidden /> : null}
            {t("dev.git.loadMore", {
              defaultValue: "Load more ({{shown}} of {{total}})",
              shown: commits.length,
              total,
            })}
          </button>
        ) : null}
      </div>
    </div>
  );
}
