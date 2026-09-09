// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

"use client";

import { useEffect } from "react";
import { useTranslation } from "react-i18next";

/**
 * Confirmation style Cursor : renvoyer depuis un message précédent
 * revient en arrière (fork) et efface la suite de la conversation.
 */
export function RevertPromptDialog({
  open,
  busy,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  busy?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const { t } = useTranslation();

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onCancel();
      } else if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        onConfirm();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onCancel, onConfirm]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[80] flex items-center justify-center bg-black/45 p-4">
      <div
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="revert-prompt-title"
        className="w-full max-w-md rounded-2xl border border-border/70 bg-popover p-5 shadow-2xl"
      >
        <h2
          id="revert-prompt-title"
          className="text-[15px] font-semibold text-foreground"
        >
          {t("thread.revert.title", {
            defaultValue: "Submit from a previous message?",
          })}
        </h2>
        <p className="mt-2 text-[13px] leading-relaxed text-muted-foreground">
          {t("thread.revert.body", {
            defaultValue:
              "Submitting from a previous message will revert the chat to before this message and clear the messages after this one.",
          })}
        </p>
        <div className="mt-5 flex flex-wrap items-center justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            className="rounded-lg px-3 py-1.5 text-[13px] text-muted-foreground hover:text-foreground"
          >
            {t("thread.revert.cancel", { defaultValue: "Cancel" })}
            <kbd className="ms-1.5 text-[11px] opacity-70">esc</kbd>
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={busy}
            className="rounded-lg bg-sky-500 px-3.5 py-1.5 text-[13px] font-semibold text-slate-950 hover:bg-sky-400 disabled:opacity-60"
          >
            {t("thread.revert.confirm", { defaultValue: "Revert" })}
            <kbd className="ms-1.5 text-[11px] opacity-80">↵</kbd>
          </button>
        </div>
      </div>
    </div>
  );
}
