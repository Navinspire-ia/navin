// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useMemo, useRef, useState, type WheelEvent } from "react";
import { Command } from "cmdk";
import { Check, ChevronsUpDown } from "lucide-react";

import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import type { SearchableOption } from "@/lib/crm-catalog";
import { cn } from "@/lib/utils";

type Props = {
  value: string;
  options: SearchableOption[];
  onChange: (value: string) => void;
  placeholder?: string;
  searchPlaceholder?: string;
  emptyLabel?: string;
  disabled?: boolean;
  allowEmpty?: boolean;
  emptyValue?: string;
  emptyLabelOption?: string;
  className?: string;
};

function matchesQuery(item: SearchableOption, needle: string) {
  if (!needle) return true;
  const hay = `${item.value} ${item.label} ${item.keywords || ""} ${item.hint || ""}`.toLowerCase();
  return hay.includes(needle);
}

export function SearchableSelect({
  value,
  options,
  onChange,
  placeholder = "Select",
  searchPlaceholder = "Search...",
  emptyLabel = "No results",
  disabled,
  allowEmpty,
  emptyValue = "",
  emptyLabelOption,
  className,
}: Props) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const items = useMemo(() => {
    const next = [...options];
    if (allowEmpty) {
      next.unshift({
        value: emptyValue || "__empty__",
        label: emptyLabelOption || placeholder,
        keywords: "empty none",
      });
    }
    return next;
  }, [allowEmpty, emptyLabelOption, emptyValue, options, placeholder]);

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!needle) return items;
    return items.filter((item) => matchesQuery(item, needle));
  }, [items, search]);

  const selected = options.find((item) => item.value === value);
  const triggerLabel = selected?.label || (value ? value : placeholder);

  const close = (nextOpen: boolean) => {
    setOpen(nextOpen);
    if (!nextOpen) setSearch("");
  };

  const stopWheel = (event: WheelEvent) => {
    event.stopPropagation();
  };

  return (
    <Popover open={open} onOpenChange={close} modal={false}>
      <PopoverTrigger asChild>
        <button
          type="button"
          disabled={disabled}
          className={cn(
            "flex h-10 w-full cursor-pointer items-center justify-between gap-2 rounded-md border border-input bg-background px-3 text-left text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50",
            !selected && "text-muted-foreground",
            className,
          )}
        >
          <span className="min-w-0 truncate">{triggerLabel}</span>
          <ChevronsUpDown className="h-3.5 w-3.5 shrink-0 opacity-50" aria-hidden />
        </button>
      </PopoverTrigger>
      <PopoverContent
        container={typeof document !== "undefined" ? document.body : undefined}
        className="pointer-events-auto z-[400] overflow-hidden p-0"
        onOpenAutoFocus={(event) => {
          event.preventDefault();
          requestAnimationFrame(() => inputRef.current?.focus());
        }}
        onCloseAutoFocus={(event) => event.preventDefault()}
        onWheel={stopWheel}
        onPointerDown={(event) => event.stopPropagation()}
      >
        <Command shouldFilter={false} className="pointer-events-auto bg-transparent" loop>
          <Command.Input
            ref={inputRef}
            value={search}
            onValueChange={setSearch}
            placeholder={searchPlaceholder}
            className="flex h-10 w-full border-b border-border/60 bg-transparent px-3 text-sm outline-none placeholder:text-muted-foreground"
          />
          <Command.List
            className="max-h-60 overflow-y-auto overscroll-contain p-1"
            onWheel={stopWheel}
          >
            <Command.Empty className="px-3 py-6 text-center text-[12px] text-muted-foreground">
              {emptyLabel}
            </Command.Empty>
            {filtered.map((item) => {
              const realValue = item.value === "__empty__" ? emptyValue : item.value;
              const active = value === realValue;
              return (
                <Command.Item
                  key={`${item.value}-${item.label}`}
                  value={`${item.label} ${item.value} ${item.keywords || ""}`}
                  keywords={(item.keywords || item.label).split(/\s+/)}
                  onSelect={() => {
                    onChange(realValue);
                    close(false);
                  }}
                  className={cn(
                    "flex cursor-pointer items-center gap-2 rounded-[12px] px-2.5 py-2 text-[13px] outline-none data-[selected=true]:bg-foreground/[0.055] dark:data-[selected=true]:bg-white/[0.08]",
                    active && "font-medium",
                  )}
                >
                  <Check className={cn("h-3.5 w-3.5 shrink-0", active ? "opacity-100" : "opacity-0")} />
                  <span className="min-w-0 flex-1 truncate">{item.label}</span>
                  {item.hint ? (
                    <span className="shrink-0 text-[11px] text-muted-foreground">{item.hint}</span>
                  ) : null}
                </Command.Item>
              );
            })}
          </Command.List>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
