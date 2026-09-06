import type { ReactNode } from "react";
import { Pencil, Trash2, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { displayText } from "@/lib/crm-format";

type Props = {
  open: boolean;
  title: string;
  emptyLabel?: string;
  canWrite?: boolean;
  extraActions?: ReactNode;
  tx: (key: string, fallback: string) => string;
  onClose: () => void;
  onEdit?: () => void;
  onDelete?: () => void;
  children?: ReactNode;
};

export function CrmFichePane({
  open,
  title,
  canWrite,
  extraActions,
  tx,
  onClose,
  onEdit,
  onDelete,
  children,
}: Props) {
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      <DialogContent
        showCloseButton={false}
        className="max-h-[90vh] max-w-2xl overflow-y-auto rounded-[22px] border-border/70 bg-popover p-5 shadow-2xl"
      >
        <DialogDescription className="sr-only">{title}</DialogDescription>
        <div className="flex items-start gap-2">
          <DialogTitle
            className="min-w-0 flex-1 text-sm font-semibold leading-snug"
            style={{ textWrap: "balance" } as never}
          >
            {title}
          </DialogTitle>
          <div className="flex shrink-0 items-center gap-0.5">
            {extraActions}
            {canWrite && onEdit ? (
              <Button
                type="button"
                size="icon"
                variant="ghost"
                className="h-7 w-7 cursor-pointer"
                aria-label={tx("crm.edit", "Modifier")}
                onClick={onEdit}
              >
                <Pencil className="h-3.5 w-3.5" />
              </Button>
            ) : null}
            {canWrite && onDelete ? (
              <Button
                type="button"
                size="icon"
                variant="ghost"
                className="h-7 w-7 cursor-pointer text-destructive hover:text-destructive"
                aria-label={tx("crm.delete", "Supprimer")}
                onClick={onDelete}
              >
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            ) : null}
            <Button
              type="button"
              size="icon"
              variant="ghost"
              className="h-7 w-7 cursor-pointer"
              aria-label={tx("crm.closeFiche", "Fermer la fiche")}
              onClick={onClose}
            >
              <X className="h-3.5 w-3.5" />
            </Button>
          </div>
        </div>
        {open ? <div className="mt-3 space-y-3">{children}</div> : null}
      </DialogContent>
    </Dialog>
  );
}

export function CrmFicheFact({ label, value }: { label: string; value: unknown }) {
  const text = displayText(value);
  if (!text) return null;
  return (
    <div>
      <dt className="text-[11px] text-muted-foreground">{label}</dt>
      <dd className="font-medium">{text}</dd>
    </div>
  );
}
