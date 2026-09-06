import { Braces, ListTree } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { fetchFileOutline } from "@/lib/api";
import type { ProjectSymbol } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * Document outline for the active editor file (index symbols by line).
 */
export function DevOutlinePanel({
  token,
  sessionKey,
  path,
  activeLine,
  onOpen,
}: {
  token: string;
  sessionKey: string;
  path: string | null;
  /** 1-based caret/reveal line used to highlight the nearest symbol. */
  activeLine?: number | null;
  onOpen: (path: string, line: number) => void;
}) {
  const { t } = useTranslation();
  const [items, setItems] = useState<ProjectSymbol[]>([]);
  const [loading, setLoading] = useState(false);
  const requestId = useRef(0);

  useEffect(() => {
    if (!path) {
      setItems([]);
      setLoading(false);
      return;
    }
    const id = ++requestId.current;
    setLoading(true);
    void fetchFileOutline(token, sessionKey, path, { limit: 200 })
      .then((payload) => {
        if (id !== requestId.current) return;
        setItems(payload.items ?? []);
      })
      .catch(() => {
        if (id !== requestId.current) return;
        setItems([]);
      })
      .finally(() => {
        if (id === requestId.current) setLoading(false);
      });
  }, [path, sessionKey, token]);

  const nearestIndex = nearestSymbolIndex(items, activeLine ?? null);

  if (!path) {
    return (
      <p className="px-3 py-4 text-[12px] text-muted-foreground">
        {t("dev.outline.noFile", {
          defaultValue: "Open a file to see its outline.",
        })}
      </p>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center gap-1.5 border-b border-border/40 px-2 py-1.5">
        <ListTree className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />
        <span className="truncate font-mono text-[10.5px] text-muted-foreground">
          {path}
        </span>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto py-1">
        {loading && items.length === 0 ? (
          <p className="px-3 py-3 text-[12px] text-muted-foreground">
            {t("dev.outline.loading", { defaultValue: "Loading outline..." })}
          </p>
        ) : items.length === 0 ? (
          <p className="px-3 py-3 text-[12px] text-muted-foreground">
            {t("dev.outline.empty", {
              defaultValue: "No symbols indexed for this file.",
            })}
          </p>
        ) : (
          items.map((item, index) => {
            const active = index === nearestIndex;
            return (
              <button
                key={`${item.name}:${item.line}:${index}`}
                type="button"
                onClick={() => onOpen(item.path, Math.max(1, item.line || 1))}
                className={cn(
                  "flex w-full items-center gap-2 px-2 py-1 text-left transition-colors",
                  active
                    ? "bg-muted/80 text-foreground"
                    : "text-foreground hover:bg-muted/50",
                )}
              >
                <Braces
                  className="h-3 w-3 shrink-0 text-muted-foreground"
                  aria-hidden
                />
                <span className="min-w-0 flex-1 truncate text-[12px]">
                  {item.name}
                </span>
                {item.kind ? (
                  <span className="shrink-0 text-[10px] uppercase tracking-wide text-muted-foreground">
                    {item.kind}
                  </span>
                ) : null}
                <span className="shrink-0 font-mono text-[10px] text-muted-foreground/80">
                  {item.line}
                </span>
              </button>
            );
          })
        )}
      </div>
    </div>
  );
}

export function nearestSymbolIndex(
  items: readonly ProjectSymbol[],
  activeLine: number | null,
): number {
  if (!items.length || activeLine == null || activeLine < 1) return -1;
  let best = -1;
  for (let i = 0; i < items.length; i += 1) {
    const line = items[i]?.line ?? 0;
    if (line <= activeLine) best = i;
    else break;
  }
  return best;
}
