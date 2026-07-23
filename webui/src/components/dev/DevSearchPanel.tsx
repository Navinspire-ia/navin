import { useCallback, useRef, useState } from "react";
import { CaseSensitive, ChevronDown, ChevronRight, Loader2, Regex, Search } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Input } from "@/components/ui/input";
import { searchProject } from "@/lib/api";
import type { ProjectSearchPayload } from "@/lib/types";
import { cn } from "@/lib/utils";

export function DevSearchPanel({
  token,
  sessionKey,
  onOpenMatch,
}: {
  token: string;
  sessionKey: string;
  onOpenMatch: (relativePath: string, line: number) => void;
}) {
  const { t } = useTranslation();
  const tx = useCallback(
    (key: string, fallback: string) => t(key, { defaultValue: fallback }),
    [t],
  );

  const [query, setQuery] = useState("");
  const [regex, setRegex] = useState(false);
  const [caseSensitive, setCaseSensitive] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ProjectSearchPayload | null>(null);
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set());
  const requestSeq = useRef(0);

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
    try {
      const payload = await searchProject(token, sessionKey, term, {
        regex,
        caseSensitive,
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
  }, [caseSensitive, query, regex, sessionKey, token]);

  const toggleFile = useCallback((path: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }, []);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="shrink-0 space-y-1.5 px-2 pb-2 pt-1">
        <div className="relative">
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") void runSearch();
            }}
            placeholder={tx("dev.search.placeholder", "Search in project…")}
            className="h-8 rounded-lg pr-8 text-[12px]"
            aria-label={tx("dev.search.placeholder", "Search in project…")}
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
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => setCaseSensitive((v) => !v)}
            className={cn(
              "rounded-md p-1 transition-colors",
              caseSensitive
                ? "bg-foreground text-background"
                : "text-muted-foreground hover:bg-muted hover:text-foreground",
            )}
            title={tx("dev.search.caseSensitive", "Match case")}
            aria-pressed={caseSensitive}
          >
            <CaseSensitive className="h-3.5 w-3.5" aria-hidden />
          </button>
          <button
            type="button"
            onClick={() => setRegex((v) => !v)}
            className={cn(
              "rounded-md p-1 transition-colors",
              regex
                ? "bg-foreground text-background"
                : "text-muted-foreground hover:bg-muted hover:text-foreground",
            )}
            title={tx("dev.search.regex", "Regular expression")}
            aria-pressed={regex}
          >
            <Regex className="h-3.5 w-3.5" aria-hidden />
          </button>
          {result ? (
            <span className="ml-auto truncate text-[11px] text-muted-foreground">
              {t("dev.search.results", {
                defaultValue: "{{count}} results",
                count: result.total,
              })}
              {result.truncated ? "+" : ""}
            </span>
          ) : null}
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-1 pb-2">
        {error ? (
          <p className="px-2 py-1.5 text-[12px] text-destructive">{error}</p>
        ) : null}
        {!error && result && result.files.length === 0 ? (
          <p className="px-2 py-1.5 text-[12px] text-muted-foreground">
            {tx("dev.search.noResults", "No results.")}
          </p>
        ) : null}
        {result?.files.map((file) => {
          const isCollapsed = collapsed.has(file.path);
          return (
            <div key={file.path} className="mb-0.5">
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
                <span className="shrink-0 rounded-full bg-muted px-1.5 text-[10px] text-muted-foreground">
                  {file.matches.length}
                </span>
              </button>
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
