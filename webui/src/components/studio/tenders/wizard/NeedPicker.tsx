// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useState } from "react";
import { TextField } from "@fluentui/react";

import { SearchableSelect } from "@/components/crm/SearchableSelect";
import { TagList, type Tx } from "@/components/studio/tenders/tenders-ui";
import {
  addNeedToken,
  needLabel,
  needSelectOptions,
  type NeedOption,
} from "@/lib/tender-needs";

export function NeedPicker({
  label,
  catalog,
  locale,
  tags,
  onChange,
  tx,
}: {
  label: string;
  catalog: NeedOption[];
  locale: string;
  tags: string[];
  onChange: (next: string[]) => void;
  tx: Tx;
}) {
  const [draft, setDraft] = useState("");
  const [pick, setPick] = useState("");
  const options = needSelectOptions(catalog, locale, tags);

  const push = (value: string) => {
    onChange(addNeedToken(value, tags, catalog));
    setDraft("");
    setPick("");
  };

  return (
    <div data-testid="need-picker">
      <p className="mb-1.5 text-[12px] font-medium text-foreground">{label}</p>
      {options.length ? (
        <div className="mb-2">
          <SearchableSelect
            value={pick}
            options={options}
            onChange={(value) => {
              const row = catalog.find((item) => item.id === value);
              push(row ? needLabel(row, locale) : value);
            }}
            placeholder={tx("pickFromCatalog", "Pick from the official list")}
            searchPlaceholder={tx("searchNeed", "Search a type or domain")}
            emptyLabel={tx("crm.noResults", "No results")}
          />
        </div>
      ) : null}
      <TextField
        value={draft}
        placeholder={tx("orTypeNeed", "Or type a value, then Enter")}
        onChange={(_, next) => setDraft(next || "")}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            push(draft);
          }
        }}
      />
      <div className="mt-2">
        <TagList values={tags} onRemove={(value) => onChange(tags.filter((row) => row !== value))} />
      </div>
    </div>
  );
}
