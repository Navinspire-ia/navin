// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { Toggle } from "@fluentui/react";
import { useTranslation } from "react-i18next";

import { OfficialLink, accessLabel, type Tx } from "@/components/studio/tenders/tenders-ui";
import { countryLabel } from "@/lib/crm-catalog";
import type { TenderSource } from "@/lib/tenders-api";

function sourceCountry(iso: string, locale: string, tx: Tx): string {
  const code = iso.trim().toUpperCase();
  if (code === "EU") return tx("countryEu", "EU / EEA");
  if (code === "INTL") return tx("countryWorld", "World");
  return countryLabel(code, locale) || code;
}

export function SourcesTable({
  rows,
  selected,
  token,
  tx,
  onToggle,
}: {
  rows: TenderSource[];
  selected: string[];
  token: string;
  tx: Tx;
  onToggle: (id: string, checked: boolean) => void;
}) {
  const { i18n } = useTranslation();
  const locale = i18n.resolvedLanguage || i18n.language || "fr";
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[36rem] border-collapse text-left text-[13px]">
        <caption className="sr-only">{tx("sourcesTableCaption", "Official sources")}</caption>
        <thead>
          <tr className="border-b border-black/10 text-[11px] uppercase tracking-wide text-muted-foreground dark:border-white/10">
            <th className="w-10 py-2 pr-2 font-semibold">{tx("colPick", "On")}</th>
            <th className="py-2 pr-2 font-semibold">{tx("colCountry", "Country")}</th>
            <th className="py-2 pr-2 font-semibold">{tx("colPlatform", "Platform")}</th>
            <th className="py-2 pr-2 font-semibold">{tx("colAccess", "Access")}</th>
            <th className="py-2 font-semibold">{tx("colUrl", "URL")}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id} className="border-b border-black/5 align-top dark:border-white/10">
              <td className="py-2 pr-2">
                <Toggle
                  checked={selected.includes(row.id)}
                  onChange={(_, checked) => onToggle(row.id, Boolean(checked))}
                  ariaLabel={row.name}
                />
              </td>
              <td className="py-2 pr-2 tabular-nums">
                <span className="font-medium">{sourceCountry(row.country, locale, tx)}</span>
                <span className="ml-1 text-muted-foreground">{row.country}</span>
              </td>
              <td className="py-2 pr-2">{row.name}</td>
              <td className="py-2 pr-2 text-muted-foreground">{accessLabel(row, tx)}</td>
              <td className="py-2">
                <OfficialLink
                  href={row.url}
                  token={token}
                  className="break-all text-teal-700 underline underline-offset-2 dark:text-teal-300"
                >
                  {row.url.replace(/^https:\/\/(www\.)?/, "")}
                </OfficialLink>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
