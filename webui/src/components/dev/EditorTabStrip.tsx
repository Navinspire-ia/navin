// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * The open-files tab strip under the editor toolbar.
 *
 * Extracted from DevWorkbench (monolith split, audit-cursor-gap): the
 * strip is presentational; routing decisions (code mode, split-pane focus,
 * on-demand preview loading) stay in the shell via the onSelect closure,
 * and the per-tab color cascade stays in the shell via textClassName.
 *
 * These sat in the toolbar above until every control there was shrink-0
 * and the tab strip was the single flexible item, so a narrow column - the
 * normal case with the chat panel open - handed the strip whatever the
 * buttons left over, down to nothing. The tabs were still open and still
 * in the DOM, merely zero pixels wide, which reads as "opening a file
 * replaced the last one". A row of their own cannot be squeezed by the
 * toolbar, and costs height only once a file is open.
 */

import { X } from "lucide-react";
import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";

import { FileTypeIcon } from "./FileTypeIcon";

/** One open editor file. DevWorkbench aliases its OpenTab to this shape. */
export interface WorkbenchTab {
  path: string;
  displayPath: string;
  name: string;
}

export function EditorTabStrip({
  tabs,
  isTabActive,
  isDirty,
  textClassName,
  onSelect,
  onClose,
}: {
  tabs: WorkbenchTab[];
  isTabActive: (tab: WorkbenchTab) => boolean;
  isDirty: (tab: WorkbenchTab) => boolean;
  textClassName: (tab: WorkbenchTab) => string | undefined;
  onSelect: (tab: WorkbenchTab) => void;
  onClose: (path: string) => void;
}) {
  const { t } = useTranslation();
  if (tabs.length === 0) return null;
  return (
    <div
      className="flex h-9 shrink-0 items-center gap-1 overflow-x-auto border-b border-border/55 bg-muted/10 px-2"
      data-testid="editor-tabs"
    >
      {tabs.map((tab) => {
        const isActive = isTabActive(tab);
        return (
          <div
            key={tab.path}
            className={cn(
              "group flex shrink-0 cursor-pointer items-center gap-1.5 rounded-lg px-2.5 py-1 text-[12px] font-medium transition-colors",
              isActive
                ? "bg-background text-foreground shadow-sm ring-1 ring-border/60"
                : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
            )}
            onClick={() => onSelect(tab)}
            onAuxClick={(event) => {
              // Middle-click closes any tab, including the last one.
              if (event.button === 1) {
                event.preventDefault();
                event.stopPropagation();
                onClose(tab.path);
              }
            }}
          >
            <FileTypeIcon name={tab.name} />
            <span
              className={cn("max-w-[10rem] truncate", textClassName(tab))}
              title={tab.displayPath}
            >
              {tab.name}
            </span>
            {isDirty(tab) ? (
              <span
                className="h-1.5 w-1.5 shrink-0 rounded-full bg-amber-500/90"
                aria-label={t("dev.unsaved", "Unsaved changes")}
              />
            ) : null}
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                onClose(tab.path);
              }}
              className={cn(
                "rounded p-0.5 transition-opacity hover:bg-muted",
                // Always visible on the active tab so the last open file
                // can still be closed (hover-only X was easy to miss).
                isActive
                  ? "opacity-70 hover:opacity-100"
                  : "opacity-0 group-hover:opacity-100",
              )}
              aria-label={t("dev.closeTab", "Close tab")}
            >
              <X className="h-3 w-3" aria-hidden />
            </button>
          </div>
        );
      })}
    </div>
  );
}
