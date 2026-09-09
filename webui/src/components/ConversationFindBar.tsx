// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { ChevronDown, ChevronUp, Search, X } from "lucide-react";
import { type RefObject } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export function ConversationFindBar({
  query,
  onQuery,
  index,
  total,
  onPrev,
  onNext,
  onClose,
  inputRef,
  compact,
}: {
  query: string;
  onQuery: (value: string) => void;
  index: number;
  total: number;
  onPrev: () => void;
  onNext: () => void;
  onClose: () => void;
  inputRef?: RefObject<HTMLInputElement | null> | null;
  compact?: boolean;
}) {
  const { t } = useTranslation();
  const count =
    total > 0
      ? t("thread.find.count", { defaultValue: "{{current}} / {{total}}", current: index + 1, total })
      : query.trim()
        ? t("thread.find.empty", { defaultValue: "No matches" })
        : "";

  return (
    <div
      className={cn(
        "flex items-center gap-1 rounded-lg border border-border/70 bg-background pl-2 pr-0.5",
        compact ? "h-8" : "h-9",
      )}
    >
      <Search className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
      <input
        ref={inputRef as RefObject<HTMLInputElement> | undefined}
        value={query}
        onChange={(event) => onQuery(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            if (event.shiftKey) onPrev();
            else onNext();
          }
          if (event.key === "Escape") {
            event.preventDefault();
            onClose();
          }
        }}
        placeholder={t("thread.find.placeholder", { defaultValue: "Search in conversation" })}
        aria-label={t("thread.find.placeholder", { defaultValue: "Search in conversation" })}
        className="h-full w-[9.5rem] bg-transparent text-[12.5px] outline-none placeholder:text-muted-foreground sm:w-44"
      />
      <span className="min-w-[3.25rem] text-center text-[10px] tabular-nums text-muted-foreground">
        {count}
      </span>
      <Button
        type="button"
        size="icon"
        variant="ghost"
        className="h-7 w-7"
        onClick={onPrev}
        disabled={total === 0}
        aria-label={t("thread.find.prev", { defaultValue: "Previous match" })}
      >
        <ChevronUp className="h-3.5 w-3.5" />
      </Button>
      <Button
        type="button"
        size="icon"
        variant="ghost"
        className="h-7 w-7"
        onClick={onNext}
        disabled={total === 0}
        aria-label={t("thread.find.next", { defaultValue: "Next match" })}
      >
        <ChevronDown className="h-3.5 w-3.5" />
      </Button>
      <Button
        type="button"
        size="icon"
        variant="ghost"
        className="h-7 w-7"
        onClick={onClose}
        aria-label={t("thread.find.close", { defaultValue: "Close search" })}
      >
        <X className="h-3.5 w-3.5" />
      </Button>
    </div>
  );
}
