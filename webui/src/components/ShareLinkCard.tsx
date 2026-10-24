// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { Check, Copy, ExternalLink } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * The share link itself, front and centre: what the host came for is to copy
 * it. One tone per kind so a session link and an application link never look
 * alike (violet: the live session, emerald: the application).
 */
const TONES = {
  session: {
    card: "border-violet-300/70 bg-violet-50 dark:border-violet-400/30 dark:bg-violet-500/10",
    label: "text-violet-800 dark:text-violet-200",
    link: "text-violet-950 dark:text-violet-50",
    button: "bg-violet-600 text-white hover:bg-violet-700 dark:bg-violet-500 dark:hover:bg-violet-400",
  },
  app: {
    card: "border-emerald-300/70 bg-emerald-50 dark:border-emerald-400/30 dark:bg-emerald-500/10",
    label: "text-emerald-800 dark:text-emerald-200",
    link: "text-emerald-950 dark:text-emerald-50",
    button: "bg-emerald-600 text-white hover:bg-emerald-700 dark:bg-emerald-500 dark:hover:bg-emerald-400",
  },
} as const;

export function ShareLinkCard({
  link,
  tone,
  label,
  copied,
  onCopy,
}: {
  link: string;
  tone: keyof typeof TONES;
  label: string;
  copied: boolean;
  onCopy: () => void;
}) {
  const { t } = useTranslation();
  const style = TONES[tone];
  return (
    <div className={cn("rounded-xl border px-3.5 py-3", style.card)}>
      <div className={cn("text-[11.5px] font-semibold uppercase tracking-wide", style.label)}>{label}</div>
      <code className={cn("mt-1.5 block select-all break-all font-mono text-[13.5px] leading-snug", style.link)}>
        {link}
      </code>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button size="sm" className={cn("gap-1.5 shadow-sm", style.button)} onClick={onCopy}>
          {copied ? <Check className="h-4 w-4" aria-hidden /> : <Copy className="h-4 w-4" aria-hidden />}
          {copied
            ? t("chat.liveShare.copied", { defaultValue: "Copied" })
            : t("chat.liveShare.copy", { defaultValue: "Copy the link" })}
        </Button>
        <Button size="sm" variant="ghost" className={cn("gap-1.5", style.label)} asChild>
          <a href={link} target="_blank" rel="noreferrer">
            <ExternalLink className="h-4 w-4" aria-hidden />
            {t("chat.liveShare.open", { defaultValue: "Open" })}
          </a>
        </Button>
      </div>
    </div>
  );
}
