// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { Brain, LayoutTemplate, Repeat, Search, type LucideIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";

type UtilityItem = {
  id: string;
  icon: LucideIcon;
  label: string;
  onClick?: () => void;
  /** "agents" = skills / agent tooling; "tools" = plugins, loop, search. */
  group: "agents" | "tools";
};

/**
 * Muted buttons for the utility sub-modules (Skills, Loop, Search).
 * Shared by the chat header and the workbench chat bar so they look identical
 * everywhere.
 *
 * - `layout="inline"`: single row (legacy).
 * - `layout="rows"`: two rows matching the workbench toolbar - Skills on the
 *   agents row, Loop / Search on the tools row.
 */
export function UtilityModuleButtons({
  onOpenSkills,
  onOpenTemplates,
  onOpenAutomations,
  onOpenSearch,
  showLabels = false,
  layout = "inline",
  className,
}: {
  onOpenApps?: () => void;
  onOpenSkills?: () => void;
  onOpenTemplates?: () => void;
  onOpenAutomations?: () => void;
  onOpenSearch?: () => void;
  showLabels?: boolean;
  layout?: "inline" | "rows";
  className?: string;
}) {
  const { t } = useTranslation();

  if (!onOpenSkills && !onOpenTemplates && !onOpenAutomations && !onOpenSearch) {
    return null;
  }

  const items: UtilityItem[] = (
    [
      {
        id: "skills",
        icon: Brain,
        label: t("sidebar.skills.title", { defaultValue: "Skills" }),
        onClick: onOpenSkills,
        group: "agents" as const,
      },
      {
        id: "templates",
        icon: LayoutTemplate,
        label: t("sidebar.templates", { defaultValue: "Templates" }),
        onClick: onOpenTemplates,
        group: "agents" as const,
      },
      {
        id: "automations",
        icon: Repeat,
        label: t("sidebar.automations", { defaultValue: "Loop" }),
        onClick: onOpenAutomations,
        group: "tools" as const,
      },
      {
        id: "search",
        icon: Search,
        label: t("sidebar.searchAria", { defaultValue: "Search" }),
        onClick: onOpenSearch,
        group: "tools" as const,
      },
    ] satisfies UtilityItem[]
  ).filter((item) => Boolean(item.onClick));

  const renderButton = ({ id, icon: Icon, label, onClick }: UtilityItem) => (
    <button
      key={id}
      type="button"
      onClick={onClick}
      className={cn(
        "shrink-0 text-muted-foreground/80 transition-colors hover:bg-muted/60 hover:text-foreground",
        showLabels
          ? "flex h-8 items-center gap-1.5 rounded-md px-2 text-[12px] font-medium"
          : "inline-flex h-8 w-8 items-center justify-center rounded-md",
        className,
      )}
      aria-label={label}
      title={label}
    >
      <Icon className="h-4 w-4 shrink-0" aria-hidden />
      {showLabels ? <span className="truncate">{label}</span> : null}
    </button>
  );

  if (layout === "rows") {
    const agents = items.filter((item) => item.group === "agents");
    const tools = items.filter((item) => item.group === "tools");
    return (
      <div className="flex min-w-0 flex-col gap-0.5">
        {agents.length > 0 ? (
          <div className="flex min-w-0 items-center gap-0.5 overflow-x-auto">
            {agents.map(renderButton)}
          </div>
        ) : null}
        {tools.length > 0 ? (
          <div className="flex min-w-0 items-center gap-0.5 overflow-x-auto">
            {tools.map(renderButton)}
          </div>
        ) : null}
      </div>
    );
  }

  return <>{items.map(renderButton)}</>;
}
