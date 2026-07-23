import { useCallback, useEffect, useState } from "react";
import { FileText, Loader2, RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";

import { fetchGitDiff } from "@/lib/api";
import type { GitDiffPayload } from "@/lib/types";
import { cn } from "@/lib/utils";

function lineClass(line: string): string {
  if (line.startsWith("+++") || line.startsWith("---")) {
    return "text-muted-foreground font-semibold";
  }
  if (line.startsWith("@@")) return "text-muted-foreground bg-muted/40";
  if (line.startsWith("+")) return "bg-foreground/[0.07] text-foreground";
  if (line.startsWith("-")) return "bg-destructive/10 text-destructive";
  if (line.startsWith("diff ") || line.startsWith("index ")) {
    return "text-muted-foreground/70";
  }
  return "text-foreground/80";
}

export function DevDiffView({
  token,
  sessionKey,
  file,
  onOpenFile,
}: {
  token: string;
  sessionKey: string;
  file: string;
  onOpenFile: (relativePath: string) => void;
}) {
  const { t } = useTranslation();
  const tx = useCallback(
    (key: string, fallback: string) => t(key, { defaultValue: fallback }),
    [t],
  );

  const [payload, setPayload] = useState<GitDiffPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    setLoading(true);
    setError(null);
    fetchGitDiff(token, sessionKey, file)
      .then(setPayload)
      .catch((err) => {
        setError(err instanceof Error ? err.message : String(err));
        setPayload(null);
      })
      .finally(() => setLoading(false));
  }, [file, sessionKey, token]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const lines = payload?.diff ? payload.diff.split("\n") : [];

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-border/50 px-3 py-1.5">
        <span className="truncate font-mono text-[11px] text-muted-foreground">
          {file}
          {payload?.untracked
            ? ` · ${tx("dev.git.untracked", "untracked")}`
            : ""}
        </span>
        <div className="flex shrink-0 items-center gap-1">
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
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-auto bg-background">
        {error ? (
          <p className="px-3 py-2 text-[12px] text-destructive">{error}</p>
        ) : null}
        {!error && !loading && payload && !payload.diff.trim() ? (
          <p className="px-3 py-2 text-[12px] text-muted-foreground">
            {tx("dev.git.noDiff", "No changes for this file.")}
          </p>
        ) : null}
        {lines.length ? (
          <pre className="min-w-max px-0 py-1 font-mono text-[12px] leading-[1.45]">
            {lines.map((line, index) => (
              <div key={index} className={cn("px-3", lineClass(line))}>
                {line || " "}
              </div>
            ))}
          </pre>
        ) : null}
      </div>
    </div>
  );
}
