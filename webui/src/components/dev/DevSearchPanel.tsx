import { useCallback, useEffect, useRef, useState } from "react";
import {
  CaseSensitive,
  ChevronDown,
  ChevronRight,
  Loader2,
  Regex,
  Replace,
  ReplaceAll,
  Search,
  SlidersHorizontal,
  Sparkles,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { Input } from "@/components/ui/input";
import { replaceInProject, searchProject } from "@/lib/api";
import type { ProjectSearchPayload } from "@/lib/types";
import { cn } from "@/lib/utils";

export function DevSearchPanel({
  token,
  sessionKey,
  onOpenMatch,
  seed,
}: {
  token: string;
  sessionKey: string;
  onOpenMatch: (relativePath: string, line: number) => void;
  /** Pre-fills the include filter, e.g. from "Find in Folder…" in the tree. */
  seed?: { include: string; nonce: number } | null;
}) {
  const { t } = useTranslation();
  const tx = useCallback(
    (key: string, fallback: string) => t(key, { defaultValue: fallback }),
    [t],
  );

  const [query, setQuery] = useState("");
  const [replacement, setReplacement] = useState("");
  const [include, setInclude] = useState("");
  const [exclude, setExclude] = useState("");
  const [showFilters, setShowFilters] = useState(false);
  const [showReplace, setShowReplace] = useState(false);
  const [regex, setRegex] = useState(false);
  const [caseSensitive, setCaseSensitive] = useState(false);
  const [semantic, setSemantic] = useState(false);
  const [loading, setLoading] = useState(false);
  const [busyPath, setBusyPath] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [result, setResult] = useState<ProjectSearchPayload | null>(null);
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set());
  const requestSeq = useRef(0);
  const queryRef = useRef<HTMLInputElement | null>(null);

  const runSearch = useCallback(async () => {
    const term = query.trim();
    if (!term) {
      setResult(null);
      setError(null);
      return;
    }
    const seq = ++requestSeq.current;
    setLoading(true);
    setError(null);
    setNotice(null);
    try {
      const payload = await searchProject(token, sessionKey, term, {
        semantic,
        regex: semantic ? false : regex,
        caseSensitive: semantic ? false : caseSensitive,
        include: semantic ? undefined : include,
        exclude: semantic ? undefined : exclude,
      });
      if (requestSeq.current === seq) {
        setResult(payload);
        setCollapsed(new Set());
      }
    } catch (err) {
      if (requestSeq.current === seq) {
        setError(err instanceof Error ? err.message : String(err));
        setResult(null);
      }
    } finally {
      if (requestSeq.current === seq) setLoading(false);
    }
  }, [caseSensitive, exclude, include, query, regex, semantic, sessionKey, token]);

  // "Find in Folder…" arrives as a nonce so the same folder twice still applies.
  const seedNonce = seed?.nonce ?? 0;
  const seedInclude = seed?.include ?? "";
  useEffect(() => {
    if (!seedNonce) return;
    setInclude(seedInclude);
    // Only reveal the filter row when there is a filter to read. Searching the
    // whole project seeds an empty include, and opening the row for that would
    // just be noise.
    if (seedInclude) setShowFilters(true);
    queryRef.current?.focus();
  }, [seedInclude, seedNonce]);

  const runReplace = useCallback(
    async (paths?: string[]) => {
      const term = query.trim();
      if (!term) return;
      setBusyPath(paths?.[0] ?? "*");
      setError(null);
      setNotice(null);
      try {
        const payload = await replaceInProject(token, sessionKey, term, replacement, {
          regex,
          caseSensitive,
          include,
          exclude,
          paths,
        });
        // The results on screen describe text that no longer exists, so the
        // only honest thing to show is a fresh search. It clears the notice on
        // the way in, hence the ordering: the count is what the user came for.
        await runSearch();
        setNotice(
          t("dev.search.replaced", {
            defaultValue: "{{count}} replaced in {{files}} file(s)",
            count: payload.replacements,
            files: payload.files_changed,
          }),
        );
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusyPath(null);
      }
    },
    [
      caseSensitive,
      exclude,
      include,
      query,
      regex,
      replacement,
      runSearch,
      sessionKey,
      t,
      token,
    ],
  );

  const toggleFile = useCallback((path: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }, []);

  const replaceDisabled = semantic || busyPath !== null || !query.trim();

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="shrink-0 space-y-1.5 px-2 pb-2 pt-1">
        <div className="flex items-start gap-1">
          <button
            type="button"
            onClick={() => {
              if (semantic) return;
              setShowReplace((v) => !v);
            }}
            disabled={semantic}
            className="mt-1 shrink-0 rounded-md p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-40"
            title={
              semantic
                ? tx(
                    "dev.search.replaceUnavailableSemantic",
                    "Replace is unavailable in semantic mode",
                  )
                : tx("dev.search.toggleReplace", "Toggle replace")
            }
            aria-expanded={showReplace && !semantic}
            aria-label={tx("dev.search.toggleReplace", "Toggle replace")}
          >
            {showReplace ? (
              <ChevronDown className="h-3.5 w-3.5" aria-hidden />
            ) : (
              <ChevronRight className="h-3.5 w-3.5" aria-hidden />
            )}
          </button>
          <div className="min-w-0 flex-1 space-y-1">
            <div className="relative">
              <Input
                ref={queryRef}
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") void runSearch();
                }}
                placeholder={
                  semantic
                    ? tx(
                        "dev.search.semanticPlaceholder",
                        "Semantic search (e.g. auth middleware)…",
                      )
                    : tx("dev.search.placeholder", "Search in project…")
                }
                className="h-8 rounded-lg pr-8 text-[12px]"
                aria-label={
                  semantic
                    ? tx(
                        "dev.search.semanticPlaceholder",
                        "Semantic search (e.g. auth middleware)…",
                      )
                    : tx("dev.search.placeholder", "Search in project…")
                }
              />
              <button
                type="button"
                onClick={() => void runSearch()}
                className="absolute right-1 top-1/2 -translate-y-1/2 rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                aria-label={tx("dev.search.run", "Search")}
              >
                {loading ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                ) : (
                  <Search className="h-3.5 w-3.5" aria-hidden />
                )}
              </button>
            </div>
            {showReplace && !semantic ? (
              <div className="relative">
                <Input
                  value={replacement}
                  onChange={(event) => setReplacement(event.target.value)}
                  placeholder={tx("dev.search.replacePlaceholder", "Replace")}
                  className="h-8 rounded-lg pr-8 text-[12px]"
                  aria-label={tx("dev.search.replacePlaceholder", "Replace")}
                />
                <button
                  type="button"
                  onClick={() => void runReplace()}
                  disabled={replaceDisabled}
                  className="absolute right-1 top-1/2 -translate-y-1/2 rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-40"
                  title={tx("dev.search.replaceAll", "Replace all")}
                  aria-label={tx("dev.search.replaceAll", "Replace all")}
                >
                  {busyPath === "*" ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                  ) : (
                    <ReplaceAll className="h-3.5 w-3.5" aria-hidden />
                  )}
                </button>
              </div>
            ) : null}
          </div>
        </div>
        <div className="flex items-center gap-1 pl-5">
          <button
            type="button"
            onClick={() => {
              setSemantic((v) => !v);
              setShowReplace(false);
            }}
            className={cn(
              "rounded-md p-1 transition-colors",
              semantic
                ? "bg-foreground text-background"
                : "text-muted-foreground hover:bg-muted hover:text-foreground",
            )}
            title={tx(
              "dev.search.semantic",
              "Semantic search (embeddings / codebase meaning)",
            )}
            aria-pressed={semantic}
          >
            <Sparkles className="h-3.5 w-3.5" aria-hidden />
          </button>
          <button
            type="button"
            onClick={() => setCaseSensitive((v) => !v)}
            disabled={semantic}
            className={cn(
              "rounded-md p-1 transition-colors disabled:opacity-40",
              caseSensitive && !semantic
                ? "bg-foreground text-background"
                : "text-muted-foreground hover:bg-muted hover:text-foreground",
            )}
            title={tx("dev.search.caseSensitive", "Match case")}
            aria-pressed={caseSensitive && !semantic}
          >
            <CaseSensitive className="h-3.5 w-3.5" aria-hidden />
          </button>
          <button
            type="button"
            onClick={() => setRegex((v) => !v)}
            disabled={semantic}
            className={cn(
              "rounded-md p-1 transition-colors disabled:opacity-40",
              regex && !semantic
                ? "bg-foreground text-background"
                : "text-muted-foreground hover:bg-muted hover:text-foreground",
            )}
            title={tx("dev.search.regex", "Regular expression")}
            aria-pressed={regex && !semantic}
          >
            <Regex className="h-3.5 w-3.5" aria-hidden />
          </button>
          <button
            type="button"
            onClick={() => setShowFilters((v) => !v)}
            disabled={semantic}
            className={cn(
              "rounded-md p-1 transition-colors disabled:opacity-40",
              !semantic && (showFilters || include.trim() || exclude.trim())
                ? "bg-foreground text-background"
                : "text-muted-foreground hover:bg-muted hover:text-foreground",
            )}
            title={tx("dev.search.toggleFilters", "Files to include / exclude")}
            aria-pressed={showFilters && !semantic}
          >
            <SlidersHorizontal className="h-3.5 w-3.5" aria-hidden />
          </button>
          {result ? (
            <span className="ml-auto truncate text-[11px] text-muted-foreground">
              {result.mode === "semantic" || result.tool === "semantic"
                ? t("dev.search.semanticResults", {
                    defaultValue: "{{count}} semantic",
                    count: result.total,
                  })
                : t("dev.search.results", {
                    defaultValue: "{{count}} results",
                    count: result.total,
                  })}
              {result.truncated ? "+" : ""}
            </span>
          ) : null}
        </div>
        {showFilters && !semantic ? (
          <div className="space-y-1 pl-5">
            <label className="block text-[10.5px] uppercase tracking-wide text-muted-foreground">
              {tx("dev.search.filesToInclude", "files to include")}
            </label>
            <Input
              value={include}
              onChange={(event) => setInclude(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void runSearch();
              }}
              placeholder="*.ts, src/**"
              className="h-7 rounded-lg font-mono text-[11px]"
            />
            <label className="block text-[10.5px] uppercase tracking-wide text-muted-foreground">
              {tx("dev.search.filesToExclude", "files to exclude")}
            </label>
            <Input
              value={exclude}
              onChange={(event) => setExclude(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void runSearch();
              }}
              placeholder="*.test.ts, dist"
              className="h-7 rounded-lg font-mono text-[11px]"
            />
          </div>
        ) : null}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-1 pb-2">
        {error ? (
          <p className="px-2 py-1.5 text-[12px] text-destructive">{error}</p>
        ) : null}
        {notice ? (
          <p className="px-2 py-1.5 text-[12px] text-emerald-600 dark:text-emerald-400">
            {notice}
          </p>
        ) : null}
        {!error && result && result.files.length === 0 ? (
          <p className="px-2 py-1.5 text-[12px] text-muted-foreground">
            {tx("dev.search.noResults", "No results.")}
          </p>
        ) : null}
        {result?.files.map((file) => {
          const isCollapsed = collapsed.has(file.path);
          return (
            <div key={file.path} className="group/file mb-0.5">
              <div className="relative flex items-center">
                <button
                  type="button"
                  onClick={() => toggleFile(file.path)}
                  className="flex w-full items-center gap-1 rounded-md px-1.5 py-1 text-left hover:bg-muted/60"
                >
                  {isCollapsed ? (
                    <ChevronRight className="h-3 w-3 shrink-0 text-muted-foreground" aria-hidden />
                  ) : (
                    <ChevronDown className="h-3 w-3 shrink-0 text-muted-foreground" aria-hidden />
                  )}
                  <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-foreground">
                    {file.path}
                  </span>
                  <span
                    className={cn(
                      "shrink-0 rounded-full bg-muted px-1.5 text-[10px] text-muted-foreground",
                      showReplace && "group-hover/file:invisible",
                    )}
                  >
                    {file.matches.length}
                  </span>
                </button>
                {showReplace ? (
                  <button
                    type="button"
                    onClick={() => void runReplace([file.path])}
                    disabled={replaceDisabled}
                    className="absolute right-1 rounded bg-background/95 p-0.5 text-muted-foreground opacity-0 shadow-sm transition-opacity hover:text-foreground focus-visible:opacity-100 disabled:opacity-40 group-hover/file:opacity-100"
                    title={tx("dev.search.replaceInFile", "Replace in this file")}
                    aria-label={`${tx("dev.search.replaceInFile", "Replace in this file")} - ${file.path}`}
                  >
                    {busyPath === file.path ? (
                      <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
                    ) : (
                      <Replace className="h-3 w-3" aria-hidden />
                    )}
                  </button>
                ) : null}
              </div>
              {!isCollapsed
                ? file.matches.map((match, index) => (
                    <button
                      key={`${match.line}-${match.col}-${index}`}
                      type="button"
                      onClick={() => onOpenMatch(file.path, match.line)}
                      className="flex w-full items-baseline gap-2 rounded-md py-0.5 pl-6 pr-2 text-left hover:bg-muted/60"
                    >
                      <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
                        {match.line}
                      </span>
                      <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-foreground/85">
                        {match.text.trim()}
                      </span>
                    </button>
                  ))
                : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}
