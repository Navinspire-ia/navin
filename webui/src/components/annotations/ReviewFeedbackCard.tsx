// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useState } from "react";
import { ChevronRight, MessageSquareText } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { ReviewItem } from "@/lib/annotations";
import { cn } from "@/lib/utils";

/**
 * A sent batch of annotations, as it reads in the thread: one compact line
 * that opens on the passages and what was asked about each.
 */
export function ReviewFeedbackCard({ items }: { items: ReviewItem[] }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const count = items.length;
  const title = t("annotations.count", {
    count,
    defaultValue: count === 1 ? "Review: 1 annotation" : `Review: ${count} annotations`,
  });
  return (
    <div
      className="w-fit min-w-0 max-w-full rounded-[16px] bg-secondary/70 text-start text-[length:var(--chat-font-size)]"
      data-testid="review-feedback-card"
    >
      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 rounded-[16px] px-3.5 py-2 text-start font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <MessageSquareText className="h-4 w-4 shrink-0 text-amber-500" aria-hidden />
        <span className="min-w-0 flex-1 truncate">{title}</span>
        <ChevronRight
          className={cn("h-4 w-4 shrink-0 text-muted-foreground transition-transform", open && "rotate-90")}
          aria-hidden
        />
      </button>
      {open ? (
        <ol className="space-y-2 border-t border-border/50 px-3.5 py-2.5">
          {items.map((item, index) => (
            <li key={index} className="min-w-0 text-[0.93em] leading-snug">
              <div className="flex flex-wrap items-center gap-x-2">
                <span className="font-medium">
                  {t(`annotations.actions.${item.action}`, { defaultValue: item.label })}
                </span>
                {item.location ? (
                  <span className="font-mono text-[0.9em] text-muted-foreground">{item.location}</span>
                ) : null}
              </div>
              <blockquote className="mt-1 line-clamp-4 whitespace-pre-wrap border-s-2 border-amber-500/60 ps-2 text-muted-foreground [overflow-wrap:anywhere]">
                {item.quote}
              </blockquote>
              {item.comment ? (
                <p className="mt-1 whitespace-pre-wrap [overflow-wrap:anywhere]">{item.comment}</p>
              ) : null}
            </li>
          ))}
        </ol>
      ) : null}
    </div>
  );
}
