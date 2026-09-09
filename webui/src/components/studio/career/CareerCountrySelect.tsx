// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useMemo } from "react";

import { SearchableSelect } from "@/components/crm/SearchableSelect";
import { countryLabel, countryOptions, normalizeCountryValue } from "@/lib/crm-catalog";

function uniqueCodes(values: string[]): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  for (const raw of values) {
    const code = normalizeCountryValue(raw);
    if (!code || seen.has(code)) continue;
    seen.add(code);
    out.push(code);
  }
  return out;
}

export function CareerCountryMultiSelect({
  values,
  onChange,
  lang,
  placeholder,
  searchPlaceholder,
  emptyLabel,
  testId,
}: {
  values: string[];
  onChange: (next: string[]) => void;
  lang: string;
  placeholder: string;
  searchPlaceholder: string;
  emptyLabel: string;
  testId?: string;
}) {
  const selected = useMemo(() => uniqueCodes(values), [values]);
  const picked = useMemo(() => new Set(selected), [selected]);
  const options = useMemo(
    () => countryOptions(lang).filter((row) => !picked.has(row.value)),
    [lang, picked],
  );

  const add = (raw: string) => {
    const code = normalizeCountryValue(raw);
    if (!code || picked.has(code)) return;
    onChange([...selected, code]);
  };

  return (
    <div className="grid gap-3" data-testid={testId || "career-country-select"}>
      <SearchableSelect
        value=""
        options={options}
        onChange={add}
        placeholder={placeholder}
        searchPlaceholder={searchPlaceholder}
        emptyLabel={emptyLabel}
        allowEmpty
      />
      {selected.length ? (
        <div className="flex flex-wrap gap-2">
          {selected.map((iso) => (
            <button
              key={iso}
              type="button"
              onClick={() => onChange(selected.filter((item) => item !== iso))}
              className="rounded-full bg-muted px-3 py-1.5 text-xs font-medium text-foreground"
            >
              {countryLabel(iso, lang)} ×
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
