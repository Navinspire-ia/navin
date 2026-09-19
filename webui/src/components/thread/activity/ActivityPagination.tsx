// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { DefaultButton, type IButtonStyles } from "@fluentui/react";
import { useTranslation } from "react-i18next";

export const ACTIVITY_DETAIL_PAGE_SIZE = 20;

const BUTTON_STYLES: IButtonStyles = {
  root: { minWidth: 64, height: 40, color: "inherit", background: "transparent", borderColor: "hsl(var(--border))" },
  rootHovered: { color: "inherit", background: "hsl(var(--muted))" },
  rootPressed: { color: "inherit", background: "hsl(var(--muted))" },
  rootDisabled: { background: "transparent", color: "hsl(var(--muted-foreground))", opacity: 0.5 },
};

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
      <DefaultButton
        text={t("message.activityPreviousPage", { defaultValue: "Previous" })}
        disabled={page === 0}
        onClick={() => onPageChange(page - 1)}
        styles={BUTTON_STYLES}
      />
      <span className="tabular-nums" aria-live="polite">
        {page * ACTIVITY_DETAIL_PAGE_SIZE + 1}-{Math.min((page + 1) * ACTIVITY_DETAIL_PAGE_SIZE, total)} / {total}
      </span>
      <DefaultButton
        text={t("message.activityNextPage", { defaultValue: "Next" })}
        disabled={page >= last}
        onClick={() => onPageChange(page + 1)}
        styles={BUTTON_STYLES}
      />
    </div>
  );
}
