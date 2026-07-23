import { useCallback, useEffect, useState } from "react";
import { GitBranch, Loader2, RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";

import { fetchGitChanges } from "@/lib/api";
import type { GitChangesPayload } from "@/lib/types";
import { cn } from "@/lib/utils";

const STATUS_LETTER: Record<string, string> = {
  modified: "M",
  added: "A",
  deleted: "D",
  renamed: "R",
  copied: "C",
  typechange: "T",
  conflict: "!",
  untracked: "U",
};

export function DevGitPanel({
  token,
  sessionKey,
  selectedPath,
  onShowDiff,
}: {
  token: string;
  sessionKey: string;
  selectedPath: string | null;
  onShowDiff: (relativePath: string) => void;
}) {
  const { t } = useTranslation();
  const tx = useCallback(
    (key: string, fallback: string) => t(key, { defaultValue: fallback }),
    [t],
  );

  const [payload, setPayload] = useState<GitChangesPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    setLoading(true);
    setError(null);
    fetchGitChanges(token, sessionKey)
      .then(setPayload)
      .catch((err) => {
        setError(err instanceof Error ? err.message : String(err));
        setPayload(null);
      })
      .finally(() => setLoading(false));
  }, [sessionKey, token]);

  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, 15_000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 items-center justify-between gap-2 px-2 pb-1.5 pt-1">
        <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
          {tx("dev.git.changes", "Changes")}
          {payload?.is_repo && payload.files.length > 0 ? ` · ${payload.files.length}` : ""}
        </span>
        <button
          type="button"
          onClick={refresh}
          className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
          aria-label={tx("dev.git.refresh", "Refresh changes")}
        >
          {loading ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
          ) : (
            <RefreshCw className="h-3.5 w-3.5" aria-hidden />
          )}
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-1 pb-2">
        {error ? (
          <p className="px-2 py-1.5 text-[12px] text-destructive">{error}</p>
        ) : null}
        {!error && payload && !payload.is_repo ? (
          <div className="flex flex-col items-center gap-2 px-3 py-6 text-center text-muted-foreground">
            <GitBranch className="h-6 w-6 opacity-40" aria-hidden />
            <p className="text-[12px] leading-5">
              {tx("dev.git.notRepo", "This project is not a git repository.")}
            </p>
          </div>
        ) : null}
        {!error && payload?.is_repo && payload.files.length === 0 ? (
          <p className="px-2 py-1.5 text-[12px] text-muted-foreground">
            {tx("dev.git.clean", "Working tree clean.")}
          </p>
        ) : null}
        {payload?.is_repo
          ? payload.files.map((file) => (
              <button
                key={file.path}
                type="button"
                onClick={() => onShowDiff(file.path)}
                className={cn(
                  "flex w-full items-center gap-2 rounded-md px-1.5 py-1 text-left transition-colors",
                  selectedPath === file.path
                    ? "bg-background shadow-sm ring-1 ring-border/60"
                    : "hover:bg-muted/60",
                )}
                title={file.path}
              >
                <span
                  className={cn(
                    "w-4 shrink-0 text-center font-mono text-[11px] font-bold",
                    file.status === "deleted" || file.status === "conflict"
                      ? "text-destructive"
                      : "text-muted-foreground",
                  )}
                >
                  {STATUS_LETTER[file.status] ?? "M"}
                </span>
                <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-foreground">
                  {file.path}
                </span>
              </button>
            ))
          : null}
      </div>
    </div>
  );
}
