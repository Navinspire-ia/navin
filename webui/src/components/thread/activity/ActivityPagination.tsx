// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";

export const ACTIVITY_DETAIL_PAGE_SIZE = 20;

export function ActivityPagination({ page, total, onPageChange }: {
  page: number;
  total: number;
  onPageChange: (page: number) => void;
}) {
  const { t } = useTranslation();
  if (total <= ACTIVITY_DETAIL_PAGE_SIZE) return null;
  const last = Math.ceil(total / ACTIVITY_DETAIL_PAGE_SIZE) - 1;
  return (
    <div className="flex flex-wrap items-center gap-2 py-1 text-xs text-muted-foreground" data-testid="activity-pagination">
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="h-10 min-w-16 bg-transparent text-inherit"
        disabled={page === 0}
        onClick={() => onPageChange(page - 1)}
      >
        {t("message.activityPreviousPage", { defaultValue: "Previous" })}
      </Button>
      <span className="tabular-nums" aria-live="polite">
        {page * ACTIVITY_DETAIL_PAGE_SIZE + 1}-{Math.min((page + 1) * ACTIVITY_DETAIL_PAGE_SIZE, total)} / {total}
      </span>
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="h-10 min-w-16 bg-transparent text-inherit"
        disabled={page >= last}
        onClick={() => onPageChange(page + 1)}
      >
        {t("message.activityNextPage", { defaultValue: "Next" })}
      </Button>
    </div>
  );
}
