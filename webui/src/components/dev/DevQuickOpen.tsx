// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { Search } from "lucide-react";
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
import { fetchProjectFiles } from "@/lib/api";
import type { ProjectFileMatch } from "@/lib/types";
import { cn } from "@/lib/utils";

import { FileTypeIcon, FolderTypeIcon } from "./FileTypeIcon";

/**
 * Cursor-style Quick Open (Ctrl/Cmd+P): fuzzy project files via /file-search.
 */
export function DevQuickOpen({
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
  onSelect: (path: string, line?: number) => void;
}) {
  const { t } = useTranslation();
  const reduceMotion = useReducedMotion();
  const inputRef = useRef<HTMLInputElement>(null);
  const itemRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<ProjectFileMatch[]>([]);
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
    setLoading(true);
    const timer = window.setTimeout(() => {
      void fetchProjectFiles(token, sessionKey, q, { limit: 40 })
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
    }, q ? 80 : 0);
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

  const handleSelect = (item: ProjectFileMatch) => {
    onOpenChange(false);
    onSelect(item.path, item.line);
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
          {t("dev.quickOpen.title", { defaultValue: "Quick Open" })}
        </DialogTitle>
        <DialogDescription className="sr-only">
          {t("dev.quickOpen.placeholder", {
            defaultValue: "Search files by name...",
          })}
        </DialogDescription>
        <AnimatePresence initial={false}>
          {open ? (
            <motion.div
              key="quick-open"
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
                  placeholder={t("dev.quickOpen.placeholder", {
                    defaultValue: "Search files by name...",
                  })}
                  aria-label={t("dev.quickOpen.title", {
                    defaultValue: "Quick Open",
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
                      ? t("dev.quickOpen.loading", { defaultValue: "Searching..." })
                      : t("dev.quickOpen.empty", {
                          defaultValue: "No matching files.",
                        })}
                  </p>
                ) : (
                  <ul className="space-y-0.5">
                    {items.map((item, index) => {
                      const highlighted = index === highlightedIndex;
                      return (
                        <li key={`${item.kind}:${item.path}:${item.line ?? 0}`}>
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
                            {item.kind === "directory" ? (
                              <FolderTypeIcon name={item.name} />
                            ) : (
                              <FileTypeIcon name={item.kind === "symbol" ? item.path : item.name} />
                            )}
                            <span className="min-w-0 flex-1">
                              <span className="block truncate text-[13px] font-medium">
                                {item.name}
                              </span>
                              <span className="block truncate font-mono text-[11px] text-muted-foreground">
                                {item.path}
                                {item.line ? `:${item.line}` : ""}
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
