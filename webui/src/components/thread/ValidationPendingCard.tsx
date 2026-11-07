// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useTranslation } from "react-i18next";
import { CircleAlert } from "lucide-react";
import { MarkdownText } from "@/components/MarkdownText";

/** Stable persisted report heading, not a keyword match against normal prose. */
export function isValidationPendingMessage(content: string): boolean {
  return /^## Validation pending\r?\n/.test(content.trimStart());
}

export function ValidationPendingCard({ content, onOpenFilePreview }: {
  content: string;
  onOpenFilePreview?: (path: string) => void;
}) {
  const { t } = useTranslation();
  // Keep the actual stop cause outside the disclosure. Full command output,
  // status table, next steps and changed paths remain available verbatim.
  const cause = content.match(/### Why it stopped\r?\n+([\s\S]*?)(?=\r?\n### |$)/)?.[1]?.trim();
  return (
    <section className="rounded-xl border border-border bg-muted/30 p-4" data-testid="validation-pending-card">
      <h3 className="flex items-center gap-2 text-base font-semibold text-foreground">
        <CircleAlert className="h-4 w-4 shrink-0 text-destructive" aria-hidden />
        {t("validationPending.title")}
      </h3>
      <p className="mt-1 text-sm text-muted-foreground">{t("validationPending.summary")}</p>
      {cause ? <div className="mt-3 text-sm [overflow-wrap:anywhere]"><MarkdownText onOpenFilePreview={onOpenFilePreview}>{cause}</MarkdownText></div> : null}
      <details className="mt-3">
        <summary className="min-h-10 cursor-pointer rounded-md py-2 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
          {t("validationPending.details")}
        </summary>
        <div className="mt-2 max-h-96 overflow-auto rounded-lg bg-background/60 p-3 text-sm [overflow-wrap:anywhere]" tabIndex={0} role="region" aria-label={t("validationPending.details")}>
          <MarkdownText onOpenFilePreview={onOpenFilePreview}>{content}</MarkdownText>
        </div>
      </details>
    </section>
  );
}
