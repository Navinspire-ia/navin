// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useMemo } from "react";
import { countryLabel, countryOptions, normalizeCountryValue } from "@/lib/crm-catalog";
import { CareerMultiSelect } from "./CareerMultiSelect";

export function CareerCountryMultiSelect({ values, onChange, lang, placeholder, searchPlaceholder, emptyLabel, testId, label }: {
  values: string[]; onChange: (next: string[]) => void; lang: string; placeholder: string;
  searchPlaceholder: string; emptyLabel: string; testId?: string; label?: string;
}) {
  const selected = [...new Set(values.map(normalizeCountryValue).filter(Boolean))];
  const options = useMemo(() => countryOptions(lang).map(option => ({ key: option.value, text: countryLabel(option.value, lang) })), [lang]);
  return <div data-testid={testId || "career-country-select"}>
    <CareerMultiSelect label={label || placeholder} values={selected} options={options} onChange={onChange}
      placeholder={searchPlaceholder} allowCustom={false} emptyLabel={emptyLabel} removeLabel={lang.startsWith("fr") ? "Retirer" : "Remove"} />
  </div>;
}
