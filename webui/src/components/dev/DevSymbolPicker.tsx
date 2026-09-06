import { Braces, Search } from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import {
  type KeyboardEvent,
  useEffect,
  useRef,
  useState,
} from "react";
import { useTranslation } from "react-i18next";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";
import { fetchProjectSymbols } from "@/lib/api";
import type { ProjectSymbol } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * Go to Symbol in Workspace (Ctrl/Cmd+T) via the code index.
 */
export function DevSymbolPicker({
  open,
  token,
  sessionKey,
  onOpenChange,
  onSelect,
}: {
  open: boolean;
  token: string;
  sessionKey: string;
  onOpenChange: (open: boolean) => void;
  onSelect: (path: string, line: number) => void;
}) {
  const { t } = useTranslation();
  const reduceMotion = useReducedMotion();
  const inputRef = useRef<HTMLInputElement>(null);
  const itemRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<ProjectSymbol[]>([]);
  const [loading, setLoading] = useState(false);
  const [highlightedIndex, setHighlightedIndex] = useState(0);
  const requestId = useRef(0);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setItems([]);
    setHighlightedIndex(0);
    window.setTimeout(() => inputRef.current?.focus(), 0);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const id = ++requestId.current;
    const q = query.trim();
    if (!q) {
      setItems([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    const timer = window.setTimeout(() => {
      void fetchProjectSymbols(token, sessionKey, q, { limit: 50 })
        .then((payload) => {
          if (id !== requestId.current) return;
          setItems(payload.items ?? []);
          setHighlightedIndex(0);
        })
        .catch(() => {
          if (id !== requestId.current) return;
          setItems([]);
        })
        .finally(() => {
          if (id === requestId.current) setLoading(false);
        });
    }, 80);
    return () => window.clearTimeout(timer);
  }, [open, query, sessionKey, token]);

  useEffect(() => {
    itemRefs.current = itemRefs.current.slice(0, items.length);
  }, [items.length]);

  useEffect(() => {
    if (!open) return;
    itemRefs.current[highlightedIndex]?.scrollIntoView({
      block: "nearest",
      inline: "nearest",
    });
  }, [highlightedIndex, open]);

  const handleSelect = (item: ProjectSymbol) => {
    onOpenChange(false);
    onSelect(item.path, Math.max(1, item.line || 1));
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    const count = items.length;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      if (!count) return;
      setHighlightedIndex((index) => (index + 1) % count);
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      if (!count) return;
      setHighlightedIndex((index) => (index - 1 + count) % count);
      return;
    }
    if (event.key === "Enter") {
      const hit = items[highlightedIndex];
      if (!hit) return;
      event.preventDefault();
      handleSelect(hit);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        showCloseButton={false}
        className={cn(
          "flex max-h-[min(28rem,calc(100vh-2rem))] w-[calc(100vw-2rem)] max-w-[34rem] flex-col gap-0 overflow-hidden p-0",
          "rounded-xl border border-border bg-background shadow-2xl",
        )}
      >
        <DialogTitle className="sr-only">
          {t("dev.symbolPicker.title", { defaultValue: "Go to Symbol" })}
        </DialogTitle>
        <DialogDescription className="sr-only">
          {t("dev.symbolPicker.placeholder", {
            defaultValue: "Type a symbol name...",
          })}
        </DialogDescription>
        <AnimatePresence initial={false}>
          {open ? (
            <motion.div
              key="symbol-picker"
              initial={reduceMotion ? false : { opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={reduceMotion ? undefined : { opacity: 0, y: 4 }}
              transition={{ type: "spring", duration: 0.3, bounce: 0 }}
              className="flex min-h-0 flex-1 flex-col"
            >
              <div className="flex h-11 shrink-0 items-center gap-2 border-b border-border px-3">
                <Search
                  className="h-4 w-4 shrink-0 text-muted-foreground"
                  aria-hidden
                />
                <input
                  ref={inputRef}
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder={t("dev.symbolPicker.placeholder", {
                    defaultValue: "Type a symbol name...",
                  })}
                  aria-label={t("dev.symbolPicker.title", {
                    defaultValue: "Go to Symbol",
                  })}
                  className="h-full min-w-0 flex-1 bg-transparent text-[14px] outline-none placeholder:text-muted-foreground"
                />
                {loading ? (
                  <span className="text-[11px] text-muted-foreground">...</span>
                ) : null}
              </div>
              <div className="min-h-0 flex-1 overflow-y-auto p-1.5">
                {items.length === 0 ? (
                  <p className="px-3 py-6 text-[12.5px] text-muted-foreground">
                    {loading
                      ? t("dev.symbolPicker.loading", {
                          defaultValue: "Searching...",
                        })
                      : query.trim()
                        ? t("dev.symbolPicker.empty", {
                            defaultValue: "No matching symbols.",
                          })
                        : t("dev.symbolPicker.hint", {
                            defaultValue: "Start typing to search symbols.",
                          })}
                  </p>
                ) : (
                  <ul className="space-y-0.5">
                    {items.map((item, index) => {
                      const highlighted = index === highlightedIndex;
                      return (
                        <li key={`${item.path}:${item.name}:${item.line}:${index}`}>
                          <button
                            ref={(node) => {
                              itemRefs.current[index] = node;
                            }}
                            type="button"
                            onClick={() => handleSelect(item)}
                            onMouseEnter={() => setHighlightedIndex(index)}
                            className={cn(
                              "flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left transition-colors",
                              highlighted
                                ? "bg-muted text-foreground"
                                : "text-foreground hover:bg-muted/70",
                            )}
                          >
                            <Braces
                              className="h-3.5 w-3.5 shrink-0 text-muted-foreground"
                              aria-hidden
                            />
                            <span className="min-w-0 flex-1">
                              <span className="flex min-w-0 items-baseline gap-2">
                                <span className="truncate text-[13px] font-medium">
                                  {item.name}
                                </span>
                                {item.kind ? (
                                  <span className="shrink-0 text-[10px] uppercase tracking-wide text-muted-foreground">
                                    {item.kind}
                                  </span>
                                ) : null}
                              </span>
                              <span className="block truncate font-mono text-[11px] text-muted-foreground">
                                {item.path}:{item.line}
                              </span>
                            </span>
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
            </motion.div>
          ) : null}
        </AnimatePresence>
      </DialogContent>
    </Dialog>
  );
}
