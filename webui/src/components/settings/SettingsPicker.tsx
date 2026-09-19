// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useId, useState, type ReactNode } from "react";
import { Check, ChevronDown, Search } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

export interface SettingsPickerOption {
  value: string;
  label: string;
  icon?: ReactNode;
}

/** Shared with the model provider menu, including desktop portal positioning and scrolling. */
export function SettingsPicker({
  value, options, ariaLabel, label, placeholder = "", disabled = false,
  searchable = false, allowCustomValue = false, onChange,
}: {
  value: string;
  options: SettingsPickerOption[];
  ariaLabel: string;
  label?: string;
  placeholder?: string;
  disabled?: boolean;
  searchable?: boolean;
  allowCustomValue?: boolean;
  onChange: (value: string) => void;
}) {
  const { t } = useTranslation();
  const id = useId();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const selected = options.find((option) => option.value === value);
  const caption = selected?.label ?? (value || placeholder);
  const normalizedQuery = query.trim().toLowerCase();
  const visible = options.filter((option) =>
    `${option.label} ${option.value}`.toLowerCase().includes(normalizedQuery),
  );
  const canUseCustom = allowCustomValue && query.trim().length > 0
    && !options.some((option) => option.value === query.trim());
  const showSearch = searchable || allowCustomValue;
  const select = (next: string) => {
    if (next !== value) onChange(next);
    setOpen(false);
  };

  return (
    <div className="flex w-[min(360px,70vw)] min-w-0 max-w-full flex-col gap-1.5">
      {label ? <label htmlFor={id} className="text-[13px] font-medium">{label}</label> : null}
      <DropdownMenu open={open} onOpenChange={(next) => { setQuery(""); setOpen(next); }}>
        <DropdownMenuTrigger asChild>
          <Button
            id={id}
            type="button"
            variant="outline"
            aria-label={`${ariaLabel}: ${caption}`}
            disabled={disabled || (options.length === 0 && !allowCustomValue)}
            className={cn(
              "h-9 w-full justify-between rounded-full border-input bg-background px-3 text-[12px] font-normal shadow-none",
              "hover:bg-accent/55 focus-visible:ring-2 focus-visible:ring-ring",
            )}
          >
            <span className="flex min-w-0 items-center gap-2">
              {selected?.icon}
              <span className={cn("min-w-0 truncate font-medium", !selected && !value && "text-muted-foreground")}>
                {caption}
              </span>
            </span>
            <ChevronDown className="ml-2 h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent
          aria-labelledby={id}
          align="end"
          onKeyDown={(event) => {
            if (event.key !== "Escape") return;
            event.preventDefault();
            event.stopPropagation();
            setOpen(false);
          }}
          className="w-[360px] max-w-[calc(100vw-2rem)] p-1.5"
        >
          {showSearch ? (
            <div className="sticky top-0 z-10 bg-popover p-1 pb-1.5">
              <div className="relative">
                <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden />
                <Input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  aria-label={t("settings.picker.search")}
                  placeholder={t("settings.picker.search")}
                  onKeyDown={(event) => {
                    if (event.key === "Escape" || event.key === "Tab") return;
                    event.stopPropagation();
                    if (event.key === "Enter" && canUseCustom) {
                      event.preventDefault();
                      select(query.trim());
                    } else if (event.key === "ArrowDown") {
                      event.preventDefault();
                      event.currentTarget.closest('[role="menu"]')
                        ?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
                    }
                  }}
                  className="h-8 rounded-full pl-8 pr-3 text-[12px]"
                />
              </div>
            </div>
          ) : null}
          {visible.map((option) => (
            <DropdownMenuItem
              key={option.value}
              onSelect={() => select(option.value)}
              className={cn(
                "flex cursor-default items-center justify-between gap-2 rounded-[12px] px-2 py-1.5 text-[12px] focus:bg-muted/85 focus:text-foreground",
                option.value === value && "bg-muted/80 text-foreground focus:bg-muted",
              )}
            >
              <span className="flex min-w-0 items-center gap-2">
                {option.icon}
                <span className="truncate font-medium">{option.label}</span>
              </span>
              {option.value === value ? <Check className="h-3.5 w-3.5 shrink-0" aria-hidden /> : null}
            </DropdownMenuItem>
          ))}
          {canUseCustom ? (
            <DropdownMenuItem onSelect={() => select(query.trim())}>
              <span className="truncate">{t("settings.picker.useValue", { value: query.trim() })}</span>
            </DropdownMenuItem>
          ) : visible.length === 0 ? (
            <p className="px-2 py-3 text-[12px] text-muted-foreground">{t("settings.picker.noResults")}</p>
          ) : null}
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}
