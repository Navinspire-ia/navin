// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * In-app confirmation dialog for destructive actions.
 *
 * Always use this instead of window.confirm: native dialogs are not available
 * in the packaged desktop builds (exe / .app / AppImage) and look foreign to
 * the product everywhere else.
 */

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description: string;
  /** Label of the destructive action button (e.g. "Delete"). */
  confirmLabel: string;
  onCancel: () => void;
  onConfirm: () => void;
}

export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  onCancel,
  onConfirm,
}: ConfirmDialogProps) {
  const { t } = useTranslation();
  return (
    <AlertDialog open={open} onOpenChange={(o) => (!o ? onCancel() : undefined)}>
      <AlertDialogContent className="w-[min(calc(100vw-2rem),24rem)] gap-0 rounded-[28px] border border-white/70 bg-card/95 p-5 text-center shadow-[0_24px_80px_rgba(15,23,42,0.20)] backdrop-blur-xl data-[state=open]:zoom-in-95 sm:rounded-[28px]">
        <AlertDialogHeader className="items-center space-y-0 text-center">
          <div className="mb-5 grid h-16 w-16 place-items-center rounded-full bg-destructive/10 text-destructive">
            <div className="grid h-9 w-9 place-items-center rounded-full border border-destructive/20 bg-destructive/5">
              <Trash2 className="h-5 w-5" strokeWidth={2.4} aria-hidden />
            </div>
          </div>
          <AlertDialogTitle className="text-center text-[20px] font-semibold leading-tight tracking-[-0.02em] text-foreground">
            {title}
          </AlertDialogTitle>
          <AlertDialogDescription className="mt-3 max-w-[17rem] text-center text-[14px] leading-6 text-muted-foreground">
            {description}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter className="mt-7 !grid grid-cols-1 gap-3 space-x-0 sm:grid-cols-2 sm:space-x-0">
          <AlertDialogCancel
            onClick={onCancel}
            className="mt-0 h-11 w-full min-w-0 rounded-full border-0 bg-muted/70 px-5 text-[15px] font-semibold text-foreground shadow-none hover:bg-muted"
          >
            {t("deleteConfirm.cancel", { defaultValue: "Cancel" })}
          </AlertDialogCancel>
          <AlertDialogAction
            onClick={onConfirm}
            className="h-11 w-full min-w-0 !whitespace-normal rounded-full bg-destructive px-5 text-center text-[15px] font-semibold text-destructive-foreground shadow-[0_10px_25px_rgba(239,68,68,0.28)] hover:bg-destructive/90"
          >
            {confirmLabel}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
