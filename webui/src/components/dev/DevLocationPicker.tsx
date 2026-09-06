import { FileTypeIcon } from "./FileTypeIcon";
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
import { cn } from "@/lib/utils";

export type DevLocationItem = {
  name?: string;
  path: string;
  line: number;
  col?: number;
  kind?: string;
};

/**
 * Peek-style picker for multiple definitions / references (F12 / Shift+F12).
 */
export function DevLocationPicker({
  open,
  title,
  symbol,
  items,
  onOpenChange,
  onSelect,
}: {
  open: boolean;
  title: string;
  symbol?: string;
  items: DevLocationItem[];
  onOpenChange: (open: boolean) => void;
  onSelect: (item: DevLocationItem) => void;
}) {
  const { t } = useTranslation();
  const reduceMotion = useReducedMotion();
  const listRef = useRef<HTMLDivElement>(null);
  const itemRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const [highlightedIndex, setHighlightedIndex] = useState(0);

  useEffect(() => {
    if (!open) return;
    setHighlightedIndex(0);
    window.setTimeout(() => listRef.current?.focus(), 0);
  }, [open, items]);

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

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
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
      onOpenChange(false);
      onSelect(hit);
    }
    if (event.key === "Escape") {
      event.preventDefault();
      onOpenChange(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        showCloseButton={false}
        className={cn(
          "flex max-h-[min(26rem,calc(100vh-2rem))] w-[calc(100vw-2rem)] max-w-[32rem] flex-col gap-0 overflow-hidden p-0",
          "rounded-xl border border-border bg-background shadow-2xl",
        )}
      >
        <DialogTitle className="sr-only">{title}</DialogTitle>
        <DialogDescription className="sr-only">
          {symbol
            ? t("dev.locations.forSymbol", {
                defaultValue: "Locations for {{symbol}}",
                symbol,
              })
            : title}
        </DialogDescription>
        <AnimatePresence initial={false}>
          {open ? (
            <motion.div
              key="locations"
              initial={reduceMotion ? false : { opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={reduceMotion ? undefined : { opacity: 0, y: 4 }}
              transition={{ type: "spring", duration: 0.3, bounce: 0 }}
              className="flex min-h-0 flex-1 flex-col"
            >
              <div className="flex shrink-0 items-center justify-between gap-2 border-b border-border px-3 py-2">
                <span className="text-[12px] font-semibold text-foreground">
                  {title}
                  {symbol ? (
                    <span className="ml-2 font-mono font-normal text-muted-foreground">
                      {symbol}
                    </span>
                  ) : null}
                </span>
                <span className="tabular-nums text-[11px] text-muted-foreground">
                  {items.length}
                </span>
              </div>
              <div
                ref={listRef}
                tabIndex={0}
                onKeyDown={handleKeyDown}
                className="min-h-0 flex-1 overflow-y-auto p-1.5 outline-none"
              >
                {items.length === 0 ? (
                  <p className="px-3 py-6 text-[12.5px] text-muted-foreground">
                    {t("dev.locations.empty", {
                      defaultValue: "No locations found.",
                    })}
                  </p>
                ) : (
                  <ul className="space-y-0.5">
                    {items.map((item, index) => {
                      const highlighted = index === highlightedIndex;
                      return (
                        <li key={`${item.path}:${item.line}:${item.col ?? 0}:${index}`}>
                          <button
                            ref={(node) => {
                              itemRefs.current[index] = node;
                            }}
                            type="button"
                            onClick={() => {
                              onOpenChange(false);
                              onSelect(item);
                            }}
                            onMouseEnter={() => setHighlightedIndex(index)}
                            className={cn(
                              "flex w-full items-start gap-2 rounded-lg px-2.5 py-1.5 text-left transition-colors",
                              highlighted
                                ? "bg-muted text-foreground"
                                : "text-foreground hover:bg-muted/70",
                            )}
                          >
                            <FileTypeIcon
                              name={item.path.split(/[/\\]/).pop() || item.path}
                              className="mt-0.5"
                            />
                            <span className="min-w-0 flex-1">
                              <span className="block truncate text-[13px] font-medium">
                                {item.name || item.path.split("/").pop() || item.path}
                                {item.kind ? (
                                  <span className="ml-1.5 text-[11px] font-normal text-muted-foreground">
                                    {item.kind}
                                  </span>
                                ) : null}
                              </span>
                              <span className="block truncate font-mono text-[11px] tabular-nums text-muted-foreground">
                                {item.path}:{item.line}
                                {item.col ? `:${item.col}` : ""}
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
