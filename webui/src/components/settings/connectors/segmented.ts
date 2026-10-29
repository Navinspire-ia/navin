// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { cn } from "@/lib/utils";

/**
 * One look for every segmented choice of a connector dialog: how to connect
 * (sign-in or key) and the kind of action the agent may run.
 */
export const SEGMENT_GROUP_CLASS =
  "inline-flex max-w-full flex-wrap rounded-xl border border-border/60 bg-muted/50 p-0.5";

export function segmentClass(selected: boolean): string {
  return cn(
    "rounded-[10px] px-3 py-1.5 text-[12.5px] font-semibold transition-[color,background-color,box-shadow] duration-150",
    selected ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
  );
}
