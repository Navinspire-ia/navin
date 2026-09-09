// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";

interface ActivityGroupProps {
  title: string;
  /** i18n key for the title; `title` becomes the fallback when provided. */
  titleKey?: string;
  icon?: LucideIcon;
  children: ReactNode;
  className?: string;
}

export function ActivityGroup({
  title: rawTitle,
  titleKey,
  icon: Icon,
  children,
  className,
}: ActivityGroupProps) {
  const { t } = useTranslation();
  const title = titleKey ? t(titleKey, { defaultValue: rawTitle }) : rawTitle;
  return (
    <section
      className={cn(
        "min-w-0 py-1 motion-safe:animate-in motion-safe:fade-in-0 motion-safe:slide-in-from-bottom-1 motion-safe:duration-200",
        className,
      )}
    >
      <div className="mb-1 flex min-w-0 items-center gap-1.5 pl-0.5 text-[12px] font-medium text-muted-foreground/70">
        {Icon ? <Icon className="h-3 w-3 shrink-0" aria-hidden /> : null}
        <span className="min-w-0 truncate">{title}</span>
      </div>
      <div className="min-w-0">{children}</div>
    </section>
  );
}
