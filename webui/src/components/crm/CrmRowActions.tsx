// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { Pencil, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";

type Props = {
  tx: (key: string, fallback: string) => string;
  onEdit: () => void;
  onDelete: () => void;
};

export function CrmRowActions({ tx, onEdit, onDelete }: Props) {
  return (
    <div className="flex gap-1">
      <Button
        type="button"
        size="icon"
        variant="ghost"
        className="h-7 w-7 cursor-pointer"
        aria-label={tx("crm.edit", "Modifier")}
        onClick={(event) => {
          event.stopPropagation();
          onEdit();
        }}
      >
        <Pencil className="h-3.5 w-3.5" />
      </Button>
      <Button
        type="button"
        size="icon"
        variant="ghost"
        className="h-7 w-7 cursor-pointer text-destructive hover:text-destructive"
        aria-label={tx("crm.delete", "Supprimer")}
        onClick={(event) => {
          event.stopPropagation();
          onDelete();
        }}
      >
        <Trash2 className="h-3.5 w-3.5" />
      </Button>
    </div>
  );
}
