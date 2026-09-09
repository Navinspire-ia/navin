// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

type Props = {
  toolbar: ReactNode;
  list: ReactNode;
  fiche?: ReactNode;
  before?: ReactNode;
  className?: string;
};

export function CrmMasterDetail({ toolbar, list, fiche, before, className }: Props) {
  return (
    <div className={cn("flex h-full min-h-0 flex-1 flex-col", className)}>
      {before}
      <div className="shrink-0">{toolbar}</div>
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">{list}</div>
      {fiche}
    </div>
  );
}
