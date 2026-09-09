// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { Command, Search } from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import {
  type KeyboardEvent,
  useEffect,
  useMemo,
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

import { filterCommands, type DevCommand } from "./devPalette";

/**
 * Workbench command palette (Ctrl/Cmd+Shift+P). Commands are provided by the
 * host so each action stays wired to real DevWorkbench state.
 */
export function DevCommandPalette({
  open,
  commands,
  onOpenChange,
}: {
  open: boolean;
  commands: readonly DevCommand[];
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  const reduceMotion = useReducedMotion();
  const inputRef = useRef<HTMLInputElement>(null);
  const itemRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const [query, setQuery] = useState("");
  const [highlightedIndex, setHighlightedIndex] = useState(0);

  const items = useMemo(
    () => filterCommands(commands, query),
    [commands, query],
  );

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setHighlightedIndex(0);
    window.setTimeout(() => inputRef.current?.focus(), 0);
  }, [open]);

  useEffect(() => {
    setHighlightedIndex(0);
  }, [query]);

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

  const run = (cmd: DevCommand) => {
    onOpenChange(false);
    // Defer so the dialog unmounts before focus moves to the editor.
    window.setTimeout(() => cmd.run(), 0);
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
      run(hit);
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
          {t("dev.commands.title", { defaultValue: "Command Palette" })}
        </DialogTitle>
        <DialogDescription className="sr-only">
          {t("dev.commands.placeholder", {
            defaultValue: "Type a command...",
          })}
        </DialogDescription>
        <AnimatePresence initial={false}>
          {open ? (
            <motion.div
              key="command-palette"
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
                  placeholder={t("dev.commands.placeholder", {
                    defaultValue: "Type a command...",
                  })}
                  aria-label={t("dev.commands.title", {
                    defaultValue: "Command Palette",
                  })}
                  className="h-full min-w-0 flex-1 bg-transparent text-[14px] outline-none placeholder:text-muted-foreground"
                />
                <Command
                  className="h-3.5 w-3.5 shrink-0 text-muted-foreground/70"
                  aria-hidden
                />
              </div>
              <div className="min-h-0 flex-1 overflow-y-auto p-1.5">
                {items.length === 0 ? (
                  <p className="px-3 py-6 text-[12.5px] text-muted-foreground">
                    {t("dev.commands.empty", {
                      defaultValue: "No matching commands.",
                    })}
                  </p>
                ) : (
                  <ul className="space-y-0.5">
                    {items.map((item, index) => {
                      const highlighted = index === highlightedIndex;
                      return (
                        <li key={item.id}>
                          <button
                            ref={(node) => {
                              itemRefs.current[index] = node;
                            }}
                            type="button"
                            onClick={() => run(item)}
                            onMouseEnter={() => setHighlightedIndex(index)}
                            className={cn(
                              "flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left transition-colors",
                              highlighted
                                ? "bg-muted text-foreground"
                                : "text-foreground hover:bg-muted/70",
                            )}
                          >
                            <span className="min-w-0 flex-1 truncate text-[13px] font-medium">
                              {item.label}
                            </span>
                            {item.hint ? (
                              <span className="shrink-0 font-mono text-[10.5px] text-muted-foreground">
                                {item.hint}
                              </span>
                            ) : null}
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
