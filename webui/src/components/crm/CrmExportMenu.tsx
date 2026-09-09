// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { Download } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { CrmRecord } from "@/lib/api";
import { downloadCrmExport, type CrmExportKind } from "@/lib/crm-export";

type Props = {
  kind: CrmExportKind;
  rows: CrmRecord[];
  companies?: CrmRecord[];
  contacts?: CrmRecord[];
  locale?: string;
  tx: (key: string, fallback: string) => string;
};

export function CrmExportMenu({ kind, rows, companies, contacts, locale = "fr-FR", tx }: Props) {
  const disabled = rows.length === 0;
  const run = (format: "csv" | "xlsx") => {
    downloadCrmExport(kind, rows, format, { companies, contacts, locale, tx });
  };

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="h-8 shrink-0 gap-1.5 px-2.5 text-[12px]"
          disabled={disabled}
          title={
            disabled
              ? tx("crm.exportEmpty", "Aucun resultat a exporter. Change les filtres.")
              : tx("crm.exportHint", "Exporte toutes les lignes filtrees, pas seulement cette page.")
          }
        >
          <Download className="h-3.5 w-3.5" aria-hidden />
          {tx("crm.export", "Exporter")}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-40">
        <DropdownMenuItem disabled={disabled} onSelect={() => run("csv")}>
          {tx("crm.exportCsv", "CSV")}
        </DropdownMenuItem>
        <DropdownMenuItem disabled={disabled} onSelect={() => run("xlsx")}>
          {tx("crm.exportExcel", "Excel")}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
