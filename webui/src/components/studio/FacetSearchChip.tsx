// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useId, useMemo, useRef, useState } from "react";
import {
  Callout,
  Checkbox,
  DefaultButton,
  DirectionalHint,
  Icon,
  SearchBox,
  SelectableOptionMenuItemType,
  type ICheckboxStyles,
  type IDropdownOption,
} from "@fluentui/react";
import "@/lib/fluent-icons";

import { cn } from "@/lib/utils";

function fold(text: string): string {
  return text
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase();
}

/** Long labels wrap instead of being cut; the box stays aligned with the first line. */
const CHECKBOX_STYLES: ICheckboxStyles = {
  root: { alignItems: "flex-start" },
  label: { alignItems: "flex-start", width: "100%" },
  checkbox: { marginTop: 1, flexShrink: 0 },
  text: { fontSize: 13, lineHeight: "18px", whiteSpace: "normal", overflowWrap: "anywhere", minWidth: 0 },
};

export type FacetSearchChipCopy = {
  searchPlaceholder: string;
  empty: string;
  clear: string;
};

/**
 * Board-style multi-select pill with a search box and a scrolling list.
 * Long facets (every country in the world, hundreds of buyers) stay usable:
 * type to narrow, tick what you want, the header groups of the option list are kept.
 */
export function FacetSearchChip({
  label,
  options,
  selected,
  onChange,
  copy,
  tone = "indigo",
  width = "md",
  testId,
}: {
  label: string;
  /** Same shape as a Fluent Dropdown: headers group the list, dividers are ignored. */
  options: IDropdownOption[];
  selected: string[];
  onChange: (next: string[]) => void;
  copy: FacetSearchChipCopy;
  tone?: "indigo" | "emerald";
  /** "lg" for long labels (buyers, domains) so they wrap on two lines instead of ten. */
  width?: "md" | "lg";
  testId?: string;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const anchor = useRef<HTMLButtonElement>(null);
  const listId = useId();
  const picked = useMemo(() => new Set(selected), [selected]);
  const needle = fold(query.trim());
  const visible = useMemo(() => {
    const out: IDropdownOption[] = [];
    let header: IDropdownOption | null = null;
    let headerShown = false;
    for (const option of options) {
      if (option.itemType === SelectableOptionMenuItemType.Divider) continue;
      if (option.itemType === SelectableOptionMenuItemType.Header) {
        header = option;
        headerShown = false;
        continue;
      }
      if (needle && !fold(String(option.text)).includes(needle) && !fold(String(option.key)).includes(needle)) continue;
      if (header && !headerShown) {
        out.push(header);
        headerShown = true;
      }
      out.push(option);
    }
    return out;
  }, [needle, options]);
  const textByKey = useMemo(() => new Map(options.map((option) => [String(option.key), String(option.text)])), [options]);
  const summary =
    selected.length === 0
      ? label
      : selected.length === 1
        ? textByKey.get(selected[0]) || selected[0]
        : `${label} · ${selected.length}`;
  const active = selected.length > 0;
  const activeClass =
    tone === "emerald"
      ? "border-emerald-700 text-emerald-800 dark:border-emerald-300 dark:text-emerald-200"
      : "border-indigo-600 text-indigo-700 dark:border-indigo-400 dark:text-indigo-300";
  const toggle = (key: string, checked: boolean) => {
    if (checked) onChange(picked.has(key) ? selected : [...selected, key]);
    else onChange(selected.filter((item) => item !== key));
  };
  return (
    <>
      <button
        ref={anchor}
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-label={label}
        onClick={() => setOpen((value) => !value)}
        className={cn(
          "inline-flex h-[34px] min-w-[118px] max-w-[16rem] shrink-0 cursor-pointer items-center gap-2 rounded-full border bg-background pl-3.5 pr-3 text-[13px] transition-colors duration-150",
          active ? cn("font-semibold", activeClass) : "border-black/20 font-medium hover:bg-muted/50 dark:border-white/20",
        )}
        data-testid={testId}
      >
        <span className="min-w-0 flex-1 truncate text-left">{summary}</span>
        <Icon iconName="ChevronDown" className="text-[10px]" aria-hidden />
      </button>
      {open ? (
        <Callout
          target={anchor.current}
          isBeakVisible={false}
          gapSpace={6}
          directionalHint={DirectionalHint.bottomLeftEdge}
          preventDismissOnScroll
          preventDismissOnResize
          setInitialFocus
          onDismiss={() => {
            setOpen(false);
            setQuery("");
          }}
          styles={{ calloutMain: { borderRadius: 12 } }}
        >
          <div className={cn("p-2", width === "lg" ? "w-[24rem]" : "w-[19rem]")} data-testid={testId ? `${testId}-menu` : undefined}>
            <SearchBox
              placeholder={copy.searchPlaceholder}
              value={query}
              autoFocus
              onChange={(_, value) => setQuery(value || "")}
              onClear={() => setQuery("")}
              styles={{ root: { borderRadius: 8 } }}
            />
            <div
              id={listId}
              role="listbox"
              aria-multiselectable
              className="mt-2 max-h-72 overflow-y-auto overscroll-contain pr-1.5 [scrollbar-gutter:stable]"
              data-menu-scroll=""
            >
              {visible.length ? (
                visible.map((option) =>
                  option.itemType === SelectableOptionMenuItemType.Header ? (
                    <p
                      key={`h-${option.key}`}
                      className="sticky top-0 bg-background px-1 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground"
                    >
                      {option.text}
                    </p>
                  ) : (
                    <div
                      key={String(option.key)}
                      className="rounded-md px-1 py-1 hover:bg-muted/60"
                      role="option"
                      aria-selected={picked.has(String(option.key))}
                    >
                      <Checkbox
                        label={String(option.text)}
                        title={String(option.text)}
                        checked={picked.has(String(option.key))}
                        onChange={(_, checked) => toggle(String(option.key), Boolean(checked))}
                        styles={CHECKBOX_STYLES}
                      />
                    </div>
                  ),
                )
              ) : (
                <p className="px-1 py-3 text-sm text-muted-foreground">{copy.empty}</p>
              )}
            </div>
            {active ? (
              <div className="mt-2 flex justify-end border-t border-black/10 pt-2 dark:border-white/10">
                <DefaultButton
                  text={copy.clear}
                  onClick={() => onChange([])}
                  styles={{ root: { minHeight: 32, height: 32, minWidth: 0, padding: "0 12px" } }}
                />
              </div>
            ) : null}
          </div>
        </Callout>
      ) : null}
    </>
  );
}
