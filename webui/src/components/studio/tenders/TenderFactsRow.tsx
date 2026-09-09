// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { cn } from "@/lib/utils";
import { factDisplay, type TenderFact } from "@/lib/tender-facts";
import type { Tx } from "@/components/studio/tenders/tenders-ui";

export function TenderFactsRow({
  facts,
  tx,
  className,
  testId = "tenders-facts-row",
}: {
  facts: TenderFact[];
  tx: Tx;
  className?: string;
  testId?: string;
}) {
  return (
    <dl
      data-testid={testId}
      className={cn("grid min-w-0 grid-cols-2 gap-x-3 gap-y-2 sm:grid-cols-4", className)}
    >
      {facts.map((item) => {
        const value = factDisplay(item, tx);
        return (
          <div key={item.key} className="min-w-0" data-testid={`tenders-fact-${item.key}`}>
            <dt className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
              {tx(item.labelKey, item.labelFallback)}
            </dt>
            <dd className="mt-0.5 truncate text-sm font-medium" title={value}>
              {item.key === "stage" && item.value
                ? tx(`stage.${item.value}`, item.value)
                : value}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}
