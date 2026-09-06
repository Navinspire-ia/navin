import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  ArrowLeft,
  Check,
  Columns2,
  FileText,
  Loader2,
  MoreHorizontal,
  RefreshCw,
  Rows3,
  Search,
  WrapText,
} from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { useTranslation } from "react-i18next";

import { fetchGitDiff } from "@/lib/api";
import type { GitDiffPayload } from "@/lib/types";
import { cn } from "@/lib/utils";

import {
  unifiedToSideBySide,
  type DiffViewMode,
  type SideBySideRow,
} from "./diffSideBySide";
import {
  DEFAULT_DIFF_KEEP_ENDS,
  buildDiffBlocks,
  collectFindHits,
  findMatchOffsets,
  parseUnifiedDiff,
  type DiffBlock,
  type UnifiedDiffLine,
} from "./diffModel";

const MODE_STORAGE_KEY = "navin.dev.diffMode";
const WRAP_STORAGE_KEY = "navin.dev.diffWordWrap";
const WS_STORAGE_KEY = "navin.dev.diffIgnoreWhitespace";

function loadMode(): DiffViewMode {
  try {
    const saved = window.localStorage.getItem(MODE_STORAGE_KEY);
    if (saved === "unified" || saved === "split") return saved;
  } catch {
    /* ignore */
  }
  return "unified";
}

function loadBool(key: string, fallback: boolean): boolean {
  try {
    const saved = window.localStorage.getItem(key);
    if (saved === "1") return true;
    if (saved === "0") return false;
  } catch {
    /* ignore */
  }
  return fallback;
}

function persistBool(key: string, value: boolean) {
  try {
    window.localStorage.setItem(key, value ? "1" : "0");
  } catch {
    /* ignore */
  }
}

function gutterClass(kind: UnifiedDiffLine["kind"]): string {
  if (kind === "add") return "bg-emerald-500/70";
  if (kind === "remove") return "bg-destructive/80";
  if (kind === "gap") return "bg-muted-foreground/30";
  return "bg-transparent";
}

function lineBgClass(kind: UnifiedDiffLine["kind"]): string {
  if (kind === "add") {
    return "bg-emerald-500/10 text-emerald-900 dark:text-emerald-100";
  }
  if (kind === "remove") {
    return "bg-destructive/10 text-destructive";
  }
  if (kind === "meta" || kind === "gap") {
    return "bg-muted/40 text-muted-foreground";
  }
  return "bg-muted/20 text-foreground/80";
}

function prefixFor(kind: UnifiedDiffLine["kind"]): string {
  if (kind === "add") return "+";
  if (kind === "remove") return "-";
  if (kind === "context") return " ";
  return "";
}

function sideCellClass(kind: SideBySideRow["kind"], side: "left" | "right"): string {
  if (kind === "meta") return "bg-muted/40 text-muted-foreground";
  if (kind === "context") return "bg-muted/20 text-foreground/80";
  if (kind === "remove") {
    return side === "left"
      ? "bg-destructive/10 text-destructive"
      : "bg-muted/20 text-transparent";
  }
  if (kind === "add") {
    return side === "right"
      ? "bg-emerald-500/10 text-emerald-900 dark:text-emerald-100"
      : "bg-muted/20 text-transparent";
  }
  return side === "left"
    ? "bg-destructive/10 text-destructive"
    : "bg-emerald-500/10 text-emerald-900 dark:text-emerald-100";
}

function HighlightedText({
  text,
  query,
  active,
}: {
  text: string;
  query: string;
  active: boolean;
}) {
  const hits = findMatchOffsets(text, query);
  if (!hits.length) return <>{text || " "}</>;
  const parts: ReactNode[] = [];
  let cursor = 0;
  hits.forEach((hit, index) => {
    if (hit.start > cursor) {
      parts.push(text.slice(cursor, hit.start));
    }
    parts.push(
      <mark
        key={`${hit.start}-${index}`}
        className={cn(
          "rounded-sm px-0.5",
          active
            ? "bg-amber-400/80 text-foreground"
            : "bg-amber-300/40 text-foreground",
        )}
      >
        {text.slice(hit.start, hit.end)}
      </mark>,
    );
    cursor = hit.end;
  });
  if (cursor < text.length) parts.push(text.slice(cursor));
  return <>{parts}</>;
}

export function DevDiffView({
  token,
  sessionKey,
  file,
  against,
  onBack,
  onOpenFile,
}: {
  token: string;
  sessionKey: string;
  file: string;
  /** Compare against this file instead of against HEAD. */
  against?: string | null;
  onBack?: () => void;
  onOpenFile: (relativePath: string) => void;
}) {
  const { t } = useTranslation();
  const tx = useCallback(
    (key: string, fallback: string) => t(key, { defaultValue: fallback }),
    [t],
  );
  const reduceMotion = useReducedMotion();
  const menuRef = useRef<HTMLDivElement | null>(null);
  const findInputRef = useRef<HTMLInputElement | null>(null);

  const [payload, setPayload] = useState<GitDiffPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<DiffViewMode>(() =>
    typeof window === "undefined" ? "unified" : loadMode(),
  );
  const [wordWrap, setWordWrap] = useState(() =>
    typeof window === "undefined" ? false : loadBool(WRAP_STORAGE_KEY, false),
  );
  const [ignoreWhitespace, setIgnoreWhitespace] = useState(() =>
    typeof window === "undefined" ? false : loadBool(WS_STORAGE_KEY, false),
  );
  const [collapsedAll, setCollapsedAll] = useState(false);
  const [expandedIds, setExpandedIds] = useState<Set<string>>(() => new Set());
  const [menuOpen, setMenuOpen] = useState(false);
  const [findOpen, setFindOpen] = useState(false);
  const [findQuery, setFindQuery] = useState("");
  const [findIndex, setFindIndex] = useState(0);

  const setModePersist = useCallback((next: DiffViewMode) => {
    setMode(next);
    try {
      window.localStorage.setItem(MODE_STORAGE_KEY, next);
    } catch {
      /* ignore */
    }
  }, []);

  const setWordWrapPersist = useCallback((next: boolean) => {
    setWordWrap(next);
    persistBool(WRAP_STORAGE_KEY, next);
  }, []);

  const setIgnoreWhitespacePersist = useCallback((next: boolean) => {
    setIgnoreWhitespace(next);
    persistBool(WS_STORAGE_KEY, next);
  }, []);

  const refresh = useCallback(() => {
    setLoading(true);
    setError(null);
    fetchGitDiff(token, sessionKey, file, {
      against,
      ignoreWhitespace,
    })
      .then(setPayload)
      .catch((err) => {
        setError(err instanceof Error ? err.message : String(err));
        setPayload(null);
      })
      .finally(() => setLoading(false));
  }, [against, file, ignoreWhitespace, sessionKey, token]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    setExpandedIds(new Set());
    setCollapsedAll(false);
    setFindQuery("");
    setFindIndex(0);
  }, [file, against]);

  useEffect(() => {
    if (!menuOpen) return;
    const close = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setMenuOpen(false);
      }
    };
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, [menuOpen]);

  useEffect(() => {
    if (findOpen) {
      window.setTimeout(() => findInputRef.current?.focus(), 0);
    }
  }, [findOpen]);

  const parsed = useMemo(
    () => parseUnifiedDiff(payload?.diff ?? ""),
    [payload?.diff],
  );

  const keepEnds = collapsedAll ? 0 : DEFAULT_DIFF_KEEP_ENDS;

  const blocks = useMemo(
    () =>
      buildDiffBlocks(parsed.lines, {
        keepEnds,
        expandedIds,
      }),
    [expandedIds, keepEnds, parsed.lines],
  );

  const splitRows = useMemo(
    () => (payload?.diff ? unifiedToSideBySide(payload.diff) : []),
    [payload?.diff],
  );

  const findHits = useMemo(
    () => collectFindHits(blocks, findQuery),
    [blocks, findQuery],
  );

  useEffect(() => {
    setFindIndex(0);
  }, [findQuery, file]);

  const activeHit = findHits[findIndex] ?? null;

  const expandBlock = (id: string) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      next.add(id);
      return next;
    });
  };

  const collapseAll = () => {
    setCollapsedAll(true);
    setExpandedIds(new Set());
    setMenuOpen(false);
  };

  const openFind = () => {
    setMenuOpen(false);
    setFindOpen(true);
  };

  const wrapClass = wordWrap ? "whitespace-pre-wrap break-words" : "whitespace-pre";

  const renderUnifiedLine = (line: UnifiedDiffLine, blockIndex: number) => {
    const isActive =
      Boolean(activeHit)
      && activeHit.blockIndex === blockIndex
      && activeHit.line === line;
    return (
      <div
        key={`${blockIndex}-${line.oldNo ?? "x"}-${line.newNo ?? "x"}-${line.kind}`}
        className={cn("flex font-mono text-[12px] leading-[1.45]", lineBgClass(line.kind))}
        data-diff-block={blockIndex}
      >
        <span
          className={cn("w-1 shrink-0 self-stretch", gutterClass(line.kind))}
          aria-hidden
        />
        <span className="w-10 shrink-0 select-none px-1.5 text-right text-[10px] text-muted-foreground/70">
          {line.oldNo ?? ""}
        </span>
        <span className="w-10 shrink-0 select-none border-r border-border/40 px-1.5 text-right text-[10px] text-muted-foreground/70">
          {line.newNo ?? ""}
        </span>
        <span className="w-4 shrink-0 select-none text-center text-muted-foreground/80">
          {prefixFor(line.kind)}
        </span>
        <span className={cn("min-w-0 flex-1 px-2", wrapClass)}>
          <HighlightedText
            text={line.text}
            query={findQuery}
            active={isActive}
          />
        </span>
      </div>
    );
  };

  const renderBlocks = (items: DiffBlock[]) =>
    items.map((block, blockIndex) => {
      if (block.type === "line") {
        return renderUnifiedLine(block.line, blockIndex);
      }
      return (
        <button
          key={block.id}
          type="button"
          onClick={() => expandBlock(block.id)}
          className="flex w-full items-center gap-2 border-y border-border/40 bg-muted/30 px-3 py-1 text-left font-mono text-[11px] text-muted-foreground hover:bg-muted/50 hover:text-foreground"
        >
          <span className="w-1 shrink-0 self-stretch rounded-sm bg-muted-foreground/40" aria-hidden />
          {t("dev.git.diffUnmodifiedCollapsed", {
            defaultValue: "{{n}} unmodified lines",
            n: block.count,
          })}
        </button>
      );
    });

  return (
    <div className="flex h-0 min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-border/50 px-3 py-1.5">
        <span className="truncate font-mono text-[11px] text-muted-foreground">
          {against ? `${against} ↔ ${file}` : file}
          {payload?.untracked
            ? ` · ${tx("dev.git.untracked", "untracked")}`
            : ""}
          {ignoreWhitespace
            ? ` · ${tx("dev.git.diffIgnoreWhitespaceShort", "ws")}`
            : ""}
        </span>
        <div className="flex shrink-0 items-center gap-1">
          {onBack ? (
            <button
              type="button"
              onClick={onBack}
              className="flex items-center gap-1 rounded-md px-1.5 py-1 text-[11px] text-muted-foreground hover:bg-muted hover:text-foreground"
              data-testid="dev-diff-back"
            >
              <ArrowLeft className="h-3 w-3" aria-hidden />
              {tx("dev.diffBack", "Back")}
            </button>
          ) : null}
          <div className="mr-1 flex items-center rounded-md border border-border/60 p-0.5">
            <button
              type="button"
              onClick={() => setModePersist("split")}
              className={cn(
                "flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] transition-colors",
                mode === "split"
                  ? "bg-muted text-foreground"
                  : "text-muted-foreground hover:text-foreground",
              )}
              title={tx("dev.git.diffSplit", "Side by side")}
              aria-pressed={mode === "split"}
            >
              <Columns2 className="h-3 w-3" aria-hidden />
              {tx("dev.git.diffSplitShort", "Split")}
            </button>
            <button
              type="button"
              onClick={() => setModePersist("unified")}
              className={cn(
                "flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] transition-colors",
                mode === "unified"
                  ? "bg-muted text-foreground"
                  : "text-muted-foreground hover:text-foreground",
              )}
              title={tx("dev.git.diffUnified", "Unified")}
              aria-pressed={mode === "unified"}
            >
              <Rows3 className="h-3 w-3" aria-hidden />
              {tx("dev.git.diffUnifiedShort", "Unified")}
            </button>
          </div>
          <button
            type="button"
            onClick={() => onOpenFile(file)}
            className="flex items-center gap-1 rounded-md px-1.5 py-1 text-[11px] text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            <FileText className="h-3 w-3" aria-hidden />
            {tx("dev.git.openFile", "Open file")}
          </button>
          <button
            type="button"
            onClick={refresh}
            className="flex items-center gap-1 rounded-md px-1.5 py-1 text-[11px] text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            {loading ? (
              <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
            ) : (
              <RefreshCw className="h-3 w-3" aria-hidden />
            )}
            {tx("dev.refreshFile", "Reload")}
          </button>
          <div className="relative" ref={menuRef}>
            <button
              type="button"
              onClick={() => setMenuOpen((open) => !open)}
              className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
              aria-label={tx("dev.git.diffMore", "Diff options")}
              aria-expanded={menuOpen}
            >
              <MoreHorizontal className="h-3.5 w-3.5" aria-hidden />
            </button>
            {menuOpen ? (
              <div className="absolute right-0 top-full z-30 mt-1 w-56 rounded-md border border-border bg-popover p-1 shadow-md">
                <p className="px-2 py-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                  {tx("dev.git.diffLayout", "Layout")}
                </p>
                <button
                  type="button"
                  onClick={() => {
                    setModePersist("unified");
                    setMenuOpen(false);
                  }}
                  className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-[12px] text-popover-foreground hover:bg-muted"
                >
                  <span className="w-3.5">
                    {mode === "unified" ? <Check className="h-3.5 w-3.5" aria-hidden /> : null}
                  </span>
                  {tx("dev.git.diffUnified", "Unified")}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setModePersist("split");
                    setMenuOpen(false);
                  }}
                  className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-[12px] text-popover-foreground hover:bg-muted"
                >
                  <span className="w-3.5">
                    {mode === "split" ? <Check className="h-3.5 w-3.5" aria-hidden /> : null}
                  </span>
                  {tx("dev.git.diffSplit", "Side by side")}
                </button>
                <div className="my-1 border-t border-border/60" />
                <button
                  type="button"
                  onClick={() => {
                    setIgnoreWhitespacePersist(!ignoreWhitespace);
                    setMenuOpen(false);
                  }}
                  className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-[12px] text-popover-foreground hover:bg-muted"
                >
                  <span className="w-3.5">
                    {ignoreWhitespace ? <Check className="h-3.5 w-3.5" aria-hidden /> : null}
                  </span>
                  {tx("dev.git.diffIgnoreWhitespace", "Ignore Whitespace")}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setWordWrapPersist(!wordWrap);
                    setMenuOpen(false);
                  }}
                  className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-[12px] text-popover-foreground hover:bg-muted"
                >
                  <span className="w-3.5">
                    {wordWrap ? <Check className="h-3.5 w-3.5" aria-hidden /> : null}
                  </span>
                  <WrapText className="h-3.5 w-3.5 shrink-0" aria-hidden />
                  {tx("dev.git.diffWordWrap", "Word Wrap")}
                </button>
                <button
                  type="button"
                  onClick={openFind}
                  className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-[12px] text-popover-foreground hover:bg-muted"
                >
                  <span className="w-3.5" />
                  <Search className="h-3.5 w-3.5 shrink-0" aria-hidden />
                  {tx("dev.git.diffFindInChanges", "Find in Changes")}
                </button>
                <button
                  type="button"
                  onClick={collapseAll}
                  className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-[12px] text-popover-foreground hover:bg-muted"
                >
                  <span className="w-3.5" />
                  {tx("dev.git.diffCollapseAll", "Collapse All")}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setMenuOpen(false);
                    refresh();
                  }}
                  className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-[12px] text-popover-foreground hover:bg-muted"
                >
                  <span className="w-3.5" />
                  <RefreshCw className="h-3.5 w-3.5 shrink-0" aria-hidden />
                  {tx("dev.git.diffRefresh", "Refresh Changes")}
                </button>
              </div>
            ) : null}
          </div>
        </div>
      </div>

      {findOpen ? (
        <div className="flex shrink-0 items-center gap-2 border-b border-border/50 bg-muted/30 px-3 py-1.5">
          <Search className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />
          <input
            ref={findInputRef}
            value={findQuery}
            onChange={(event) => setFindQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                setFindOpen(false);
                setFindQuery("");
              } else if (event.key === "Enter") {
                event.preventDefault();
                if (!findHits.length) return;
                setFindIndex((prev) =>
                  event.shiftKey
                    ? (prev - 1 + findHits.length) % findHits.length
                    : (prev + 1) % findHits.length,
                );
              }
            }}
            placeholder={tx("dev.git.diffFindPlaceholder", "Find in changes")}
            className="min-w-0 flex-1 rounded border border-border/60 bg-background px-2 py-1 text-[12px] outline-none focus:ring-1 focus:ring-ring"
          />
          <span className="shrink-0 text-[11px] text-muted-foreground">
            {findQuery.trim()
              ? findHits.length
                ? `${findIndex + 1}/${findHits.length}`
                : "0/0"
              : ""}
          </span>
          <button
            type="button"
            onClick={() => {
              setFindOpen(false);
              setFindQuery("");
            }}
            className="rounded px-1.5 py-0.5 text-[11px] text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            {tx("dev.git.diffFindClose", "Close")}
          </button>
        </div>
      ) : null}

      <div
        className="min-h-0 flex-1 overflow-x-auto overflow-y-auto overscroll-contain bg-background"
        data-testid="dev-diff-scroll"
      >
        {error ? (
          <p className="px-3 py-2 text-[12px] text-destructive">{error}</p>
        ) : null}
        {!error && !loading && payload && !payload.diff.trim() ? (
          <p className="px-3 py-2 text-[12px] text-muted-foreground">
            {against
              ? tx("dev.git.identical", "These two files are identical.")
              : tx("dev.git.noDiff", "No changes for this file.")}
          </p>
        ) : null}
        <AnimatePresence mode="wait" initial={false}>
          {mode === "split" && splitRows.length ? (
            <motion.div
              key="split"
              initial={reduceMotion ? false : { opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={reduceMotion ? undefined : { opacity: 0, y: -2 }}
              transition={{ duration: 0.18 }}
              className={wordWrap ? "w-full" : "min-w-max"}
            >
              <div className="sticky top-0 z-10 grid grid-cols-2 border-b border-border/50 bg-muted/40 text-[10.5px] font-medium uppercase tracking-wide text-muted-foreground">
                <div className="border-r border-border/50 px-3 py-1">
                  {tx("dev.git.diffBefore", "Before")}
                </div>
                <div className="px-3 py-1">
                  {tx("dev.git.diffAfter", "After")}
                </div>
              </div>
              {splitRows.map((row, index) => (
                <div
                  key={index}
                  className="grid grid-cols-2 font-mono text-[12px] leading-[1.45]"
                >
                  <div
                    className={cn(
                      "flex min-w-0 border-r border-border/40",
                      sideCellClass(row.kind, "left"),
                    )}
                  >
                    <span
                      className={cn(
                        "w-1 shrink-0 self-stretch",
                        row.kind === "remove" || row.kind === "change"
                          ? "bg-destructive/80"
                          : "bg-transparent",
                      )}
                      aria-hidden
                    />
                    <span className="w-10 shrink-0 select-none px-1.5 text-right text-[10px] text-muted-foreground/70">
                      {row.leftNo ?? ""}
                    </span>
                    <span className={cn("min-w-0 flex-1 px-2", wrapClass)}>
                      {row.left ?? " "}
                    </span>
                  </div>
                  <div
                    className={cn(
                      "flex min-w-0",
                      sideCellClass(row.kind, "right"),
                    )}
                  >
                    <span
                      className={cn(
                        "w-1 shrink-0 self-stretch",
                        row.kind === "add" || row.kind === "change"
                          ? "bg-emerald-500/70"
                          : "bg-transparent",
                      )}
                      aria-hidden
                    />
                    <span className="w-10 shrink-0 select-none px-1.5 text-right text-[10px] text-muted-foreground/70">
                      {row.rightNo ?? ""}
                    </span>
                    <span className={cn("min-w-0 flex-1 px-2", wrapClass)}>
                      {row.right ?? " "}
                    </span>
                  </div>
                </div>
              ))}
            </motion.div>
          ) : null}
          {mode === "unified" && blocks.length ? (
            <motion.div
              key="unified"
              initial={reduceMotion ? false : { opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={reduceMotion ? undefined : { opacity: 0, y: -2 }}
              transition={{ duration: 0.18 }}
              className={cn("py-1", wordWrap ? "w-full" : "min-w-max")}
            >
              {parsed.binary ? (
                <p className="px-3 py-2 text-[12px] text-muted-foreground">
                  {tx("dev.git.diffBinary", "Binary file diff.")}
                </p>
              ) : (
                renderBlocks(blocks)
              )}
            </motion.div>
          ) : null}
        </AnimatePresence>
        {mode === "split" &&
        payload?.diff.trim() &&
        !splitRows.length &&
        !loading &&
        !error ? (
          <p className="px-3 py-2 text-[12px] text-muted-foreground">
            {tx(
              "dev.git.diffSplitFallback",
              "Could not parse this diff for side-by-side view. Switch to Unified.",
            )}
          </p>
        ) : null}
      </div>
    </div>
  );
}
