// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useState, type ReactNode } from "react";
import { DefaultButton, Icon, PrimaryButton } from "@fluentui/react";
import "@/lib/fluent-icons";

import { BUTTON_STYLES, type Tx } from "@/components/studio/tenders/tenders-ui";
import { cn } from "@/lib/utils";

export type WizardStepMeta = { id: number; key: string; icon: string };

export function WizardStepper({
  steps,
  current,
  tx,
  onSelect,
}: {
  steps: readonly WizardStepMeta[];
  current: number;
  tx: Tx;
  onSelect: (id: number) => void;
}) {
  return (
    <ol className="flex gap-1 overflow-x-auto pb-1" aria-label={tx("wizardStepsAria", "Setup steps")}>
      {steps.map((row) => {
        const active = row.id === current;
        const done = row.id < current;
        return (
          <li key={row.id} className="min-w-0 shrink-0">
            <button
              type="button"
              onClick={() => onSelect(row.id)}
              className={cn(
                "flex min-h-11 cursor-pointer items-center gap-2 rounded-full px-3 text-[13px] transition-[transform,background-color,color] duration-150 active:scale-[0.96]",
                active
                  ? "bg-emerald-800 text-white dark:bg-emerald-300 dark:text-slate-950"
                    : done
                    ? "bg-emerald-800/10 text-emerald-900 dark:bg-emerald-300/15 dark:text-emerald-100"
                    : "bg-muted/50 text-muted-foreground",
              )}
              aria-current={active ? "step" : undefined}
            >
              <span className="tabular-nums text-[12px] font-semibold">{row.id}</span>
              {active ? <span className="max-w-[9rem] truncate">{tx(`wizard.${row.key}`, row.key)}</span> : null}
            </button>
          </li>
        );
      })}
    </ol>
  );
}

export function WizardFooter({
  tx,
  busy,
  canContinue,
  continueLabel,
  showSkip,
  showBack,
  onBack,
  onSkip,
  onContinue,
}: {
  tx: Tx;
  busy: boolean;
  canContinue: boolean;
  continueLabel: string;
  showSkip?: boolean;
  showBack?: boolean;
  onBack?: () => void;
  onSkip?: () => void;
  onContinue: () => void;
}) {
  return (
    <div className="mt-8 flex flex-wrap items-center gap-3 border-t border-black/5 pt-5 dark:border-white/10">
      {showBack ? (
        <DefaultButton text={tx("wizardBack", "Back")} onClick={onBack} styles={BUTTON_STYLES} />
      ) : null}
      {showSkip ? (
        <DefaultButton text={tx("skipStep", "Skip for now")} onClick={onSkip} styles={BUTTON_STYLES} />
      ) : null}
      <PrimaryButton
        text={continueLabel}
        disabled={!canContinue}
        onClick={onContinue}
        styles={BUTTON_STYLES}
      />
      {busy ? (
        <span className="text-[12px] text-muted-foreground">{tx("working", "Working")}...</span>
      ) : null}
    </div>
  );
}

export function Block({
  title,
  body,
  children,
}: {
  title: string;
  body?: string;
  children: ReactNode;
}) {
  return (
    <section className="grid gap-3 rounded-2xl bg-muted/25 p-4">
      <div>
        <h3 className="text-balance text-[15px] font-semibold">{title}</h3>
        {body ? <p className="mt-1 text-pretty text-[13px] text-muted-foreground">{body}</p> : null}
      </div>
      {children}
    </section>
  );
}

export function FileStatus({
  icon,
  title,
  status,
  action,
  onAction,
}: {
  icon: string;
  title: string;
  status: string;
  action: string;
  onAction: () => void;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl bg-muted/25 px-4 py-4">
      <div className="flex min-w-0 items-start gap-3">
        <Icon iconName={icon} className="mt-0.5 text-teal-800 dark:text-teal-200" />
        <div className="min-w-0">
          <p className="font-medium">{title}</p>
          <p className="text-[13px] text-muted-foreground">{status}</p>
        </div>
      </div>
      <DefaultButton text={action} onClick={onAction} styles={BUTTON_STYLES} />
    </div>
  );
}

export function FileLibrary({
  icon,
  title,
  files,
  emptyLabel,
  action,
  onImport,
  onRemove,
  onPreview,
  tx,
}: {
  icon: string;
  title: string;
  files: { file_id?: string; name?: string; title?: string; excerpt?: string; client?: string; year?: string }[];
  emptyLabel: string;
  action: string;
  onImport: () => void;
  onRemove: (row: { file_id?: string; title?: string; name?: string }) => void;
  onPreview?: (fileId: string) => Promise<string>;
  tx: Tx;
}) {
  const [live, setLive] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState("");
  const openPreview = async (fileId: string) => {
    if (!onPreview || live[fileId] || loading === fileId) return;
    setLoading(fileId);
    try {
      const text = (await onPreview(fileId)).trim();
      if (text) setLive((prev) => ({ ...prev, [fileId]: text }));
    } catch {
      setLive((prev) => ({ ...prev, [fileId]: "" }));
    } finally {
      setLoading("");
    }
  };
  return (
    <div className="grid gap-3 rounded-2xl bg-muted/25 px-4 py-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <Icon iconName={icon} className="mt-0.5 text-teal-800 dark:text-teal-200" />
          <div className="min-w-0">
            <p className="font-medium">{title}</p>
            <p className="text-[13px] text-muted-foreground">
              {files.length ? tx("filesOnFile", "{{count}} on file", { count: files.length }) : emptyLabel}
            </p>
          </div>
        </div>
        <DefaultButton text={action} onClick={onImport} styles={BUTTON_STYLES} />
      </div>
      {files.length ? (
        <ul className="grid gap-2">
          {files.map((row, index) => (
            <li
              key={row.file_id || `${row.title || row.name || "file"}-${index}`}
              className="flex flex-wrap items-start justify-between gap-3 rounded-xl bg-background/70 px-3 py-2"
            >
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{row.title || row.name}</p>
                {row.client || row.year ? (
                  <p className="text-[12px] text-muted-foreground">
                    {[row.client, row.year].filter(Boolean).join(" · ")}
                  </p>
                ) : null}
                {row.excerpt || (row.file_id && live[row.file_id]) ? (
                  <details className="mt-1">
                    <summary className="cursor-pointer text-[12px] text-teal-800 dark:text-teal-200">
                      {tx("previewExtract", "Preview extract")}
                    </summary>
                    <p className="mt-1 whitespace-pre-wrap text-[12px] text-muted-foreground">
                      {row.excerpt || live[row.file_id || ""]}
                    </p>
                  </details>
                ) : row.file_id && onPreview ? (
                  <button
                    type="button"
                    className="mt-1 text-left text-[12px] text-teal-800 dark:text-teal-200"
                    onClick={() => void openPreview(row.file_id || "")}
                  >
                    {loading === row.file_id
                      ? tx("loadingExtract", "Loading extract...")
                      : tx("previewExtract", "Preview extract")}
                  </button>
                ) : (
                  <p className="mt-1 text-[12px] text-muted-foreground">{tx("noExtract", "No text extracted yet.")}</p>
                )}
              </div>
              <DefaultButton
                text={tx("removeFile", "Remove")}
                onClick={() => onRemove(row)}
                styles={BUTTON_STYLES}
              />
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
