// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useRef, useState } from "react";
import { ComboBox, DefaultButton, Stack, type IComboBox } from "@fluentui/react";

export type CareerChoice = { key: string; text: string; aliases?: string[] };
const fold = (value: string) => value.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLocaleLowerCase();

/** Fluent callouts remain usable inside the company Panel's focus trap. */
export function CareerMultiSelect({ label, values, options, onChange, placeholder, allowCustom = true, emptyLabel, removeLabel, disabled }: {
  label: string; values: string[]; options: CareerChoice[]; onChange: (values: string[]) => void;
  placeholder: string; allowCustom?: boolean; emptyLabel: string; removeLabel: string; disabled?: boolean;
}) {
  const [query, setQuery] = useState("");
  const combo = useRef<IComboBox>(null);
  // Keep persisted values stable while displaying their label in the active language.
  const localized = options.map(option => ({ ...option, key: values.find(value =>
    [option.key, ...(option.aliases || [])].some(alias => fold(alias) === fold(value))) || option.key }));
  const all: CareerChoice[] = [...localized, ...values.filter(value => !localized.some(option => option.key === value)).map(value => ({ key: value, text: value }))];
  const matches = all.filter(option => fold(`${option.text} ${option.key} ${(option.aliases || []).join(" ")}`).includes(fold(query.trim())));
  const add = (raw: string) => {
    const next = [...values];
    for (const part of raw.split(/[,;\n]/).map(value => value.trim()).filter(Boolean)) {
      const option = all.find(item => [item.text, item.key, ...(item.aliases || [])].some(label => fold(label) === fold(part)));
      if (!option && !allowCustom) continue;
      const value = option?.key || part;
      if (!next.some(item => fold(item) === fold(value))) next.push(value);
    }
    onChange(next);
    setQuery("");
  };
  return <Stack tokens={{ childrenGap: 8 }}>
    <ComboBox componentRef={combo} label={label} ariaLabel={label} multiSelect selectedKey={values} text={query}
      options={matches} allowFreeform autoComplete="off" disabled={disabled}
      placeholder={placeholder} onInputValueChange={value => { setQuery(value); if (value.trim()) combo.current?.focus(true); }}
      onChange={(event, option, _index, value) => {
        // Fluent submits the hovered option on blur in freeform mode. Leaving
        // this field must not toggle a selection or undo the preceding click.
        if (event?.type === "blur") return;
        if (option) {
          onChange(option.selected ? [...new Set([...values, String(option.key)])] : values.filter(item => item !== option.key));
          setQuery("");
        } else if (value) add(value);
      }}
      useComboBoxAsMenuWidth scrollSelectedToTop={false}
      comboBoxOptionStyles={{ root: { selectors: { ":after": { pointerEvents: "none" } } } }}
      calloutProps={{ calloutMaxHeight: 280, styles: { calloutMain: { overflowY: "auto", overscrollBehavior: "contain" } } }}
      styles={{ root: { minWidth: 0 }, input: { minWidth: 0 }, optionsContainerWrapper: { maxHeight: 280 } }} />
    {!!query && !matches.length && <p className="text-xs text-muted-foreground">{allowCustom ? placeholder : emptyLabel}</p>}
    {!!values.length && <Stack horizontal wrap tokens={{ childrenGap: 6 }}>
      {values.map(value => <DefaultButton key={value} text={all.find(option => option.key === value)?.text || value}
        ariaLabel={`${removeLabel} ${all.find(option => option.key === value)?.text || value}`} iconProps={{ iconName: "Cancel" }}
        disabled={disabled} onClick={() => onChange(values.filter(item => item !== value))}
        styles={{ root: { minHeight: 36, height: "auto", maxWidth: "100%", borderRadius: 18 }, label: { whiteSpace: "normal", overflowWrap: "anywhere" } }} />)}
    </Stack>}
  </Stack>;
}
